# ╔══════════════════════════════════════════════════════════════╗
# ║          KENSHIN FILE NEXUS — FILE STORE BOT                ║
# ║          by @KENSHIN_ANIME  |  Powered by Pyrofork          ║
# ╚══════════════════════════════════════════════════════════════╝

import asyncio
import base64
import logging
import os
import random
import time

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, enums, filters, idle
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from pyrogram.errors import FloodWait, UserNotParticipant

from config import Config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("KenshinNexus")

# ── Pyrogram Client ───────────────────────────────────────────────────────────
app = Client(
    "kenshin_nexus",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

# ══════════════════════════════════════════════════════════════════════════════
#  MONGODB
# ══════════════════════════════════════════════════════════════════════════════

mongo_client  = AsyncIOMotorClient(Config.MONGO_URI)
db_mongo      = mongo_client["kenshin_nexus"]
settings_col  = db_mongo["settings"]
users_col     = db_mongo["users"]
DOC_ID        = "main"

async def get_settings() -> dict:
    doc = await settings_col.find_one({"_id": DOC_ID})
    if doc:
        return doc
    default = {
        "_id": DOC_ID,
        "admins":          [Config.OWNER_ID],
        "fsub_channels":   [],
        "start_images":    [],
        "start_message":   "",
        "start_sticker":   "",
        "pre_file_msg":    "",       # message before file delivery
        "auto_del_msg":    "",       # custom auto-delete warning message
        "total_links":     0,
        "auto_delete_time": Config.AUTO_DELETE_TIME,
    }
    await settings_col.insert_one(default)
    return default

async def update_settings(field: str, value):
    await settings_col.update_one(
        {"_id": DOC_ID}, {"$set": {field: value}}, upsert=True
    )

async def register_user(uid: int):
    await users_col.update_one({"_id": uid}, {"$set": {"_id": uid}}, upsert=True)

async def get_all_users() -> list:
    return [doc["_id"] async for doc in users_col.find({}, {"_id": 1})]

async def get_total_users() -> int:
    return await users_col.count_documents({})

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

batch_sessions: dict[int, list[int]] = {}

def encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def decode(s: str) -> str:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode()).decode()

async def is_admin(uid: int) -> bool:
    cfg = await get_settings()
    return uid == Config.OWNER_ID or uid in cfg.get("admins", [])

def fill_placeholders(text: str, user: User) -> str:
    """Replace all {placeholder} tokens with real user data.
    Supported: {mention} {first_name} {last_name} {username} {id} {channel}
    """
    if not text:
        return text
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    return (
        text
        .replace("{mention}",    mention)
        .replace("{first_name}", user.first_name or "")
        .replace("{last_name}",  user.last_name  or "")
        .replace("{username}",   f"@{user.username}" if user.username else user.first_name)
        .replace("{id}",         str(user.id))
        .replace("{channel}",    "@KENSHIN_ANIME")
    )

async def auto_delete(client: Client, chat_id: int, msg_ids: list, delay: int):
    await asyncio.sleep(delay)
    for mid in msg_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass

async def _delete_after(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass

async def get_fsub_violations(client: Client, user_id: int) -> list:
    cfg = await get_settings()
    violations = []
    for ch in cfg.get("fsub_channels", []):
        try:
            member = await client.get_chat_member(ch["id"], user_id)
            if member.status.value in ("left", "kicked", "banned"):
                violations.append(ch)
        except UserNotParticipant:
            violations.append(ch)
        except Exception as e:
            logger.warning(f"FSub check error [{ch['id']}]: {e}")
    return violations

async def build_fsub_markup(
    client: Client, violations: list, encoded: str
) -> InlineKeyboardMarkup:
    buttons = []
    for ch in violations:
        try:
            if ch["type"] == "public":
                chat = await client.get_chat(ch["id"])
                url = f"https://t.me/{chat.username}"
            elif ch["type"] == "private_request":
                inv = await client.create_chat_invite_link(
                    ch["id"], creates_join_request=True
                )
                url = inv.invite_link
            else:
                inv = await client.create_chat_invite_link(ch["id"])
                url = inv.invite_link
            buttons.append(
                [InlineKeyboardButton(f"📡  Join — {ch['name']}", url=url)]
            )
        except Exception as e:
            logger.error(f"Button gen failed [{ch['id']}]: {e}")
    buttons.append(
        [InlineKeyboardButton(
            "✅  I've Joined — Verify & Unlock",
            callback_data=f"verify_{encoded}"
        )]
    )
    return InlineKeyboardMarkup(buttons)

# ── Default messages (HTML + blockquote) ──────────────────────────────────────

DEFAULT_START = (
    "<b>⚡ KENSHIN FILE NEXUS — SYSTEM ONLINE ⚡</b>\n\n"
    "<blockquote>"
    "╔══════════════════════════════╗\n"
    "║   INITIALIZING SYSTEM...     ║\n"
    "║   ENCRYPTION   :  ACTIVE  ✓  ║\n"
    "║   STORAGE      :  SECURED ✓  ║\n"
    "║   FORCE-SUB    :  ENABLED ✓  ║\n"
    "║   STATUS       :  ONLINE  ✓  ║\n"
    "╚══════════════════════════════╝"
    "</blockquote>\n\n"
    "🌑 <b>Welcome to the Shadow Archive.</b>\n"
    "Files stored here are encrypted, permanent,\n"
    "and accessible only through verified access links.\n\n"
    "🔗 Use /help to access the <b>Command Matrix</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "<i>⚡ Powered by Kenshin Anime</i>"
)

DEFAULT_PRE_FILE = (
    "<b>MADARA UCHIHA 🌑</b>\n\n"
    "Greetings, {mention}.\n\n"
    "I am the Ghost of the Uchiha, the supreme authority guarding "
    "the vaults of @KENSHIN_ANIME. You stand before a power that "
    "transcends your understanding.\n\n"
    "<b>THE DECREE:</b>\n"
    "<blockquote>Retrieve your files using the provided link. "
    "Do not waste my time with common inquiries. "
    "I have no interest in the weak.</blockquote>\n\n"
    "<b>SUPREMACY: @KENSHIN_ANIME</b>\n\n"
    "<blockquote>\"Wake up to reality! Nothing ever goes as planned "
    "in this accursed world.\"</blockquote>"
)

DEFAULT_AUTO_DEL = (
    "<b>⏳ AUTO-PURGE SEQUENCE ACTIVE</b>\n\n"
    "<blockquote>🗑️ Files self-destruct in <b>{time}</b>\n"
    "💾 Download &amp; save immediately before they vanish.</blockquote>"
)

FSUB_MSG = (
    "<b>🔐 SECURITY PROTOCOL — ACCESS RESTRICTED</b>\n\n"
    "<blockquote>"
    "[ CLEARANCE CHECK  : FAILED     ]\n"
    "[ FILE ACCESS      : LOCKED     ]\n"
    "[ ACTION REQUIRED  : JOIN BELOW ]"
    "</blockquote>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ Subscribe to <b>ALL</b> channels below to unlock\n"
    "your transmission from the KENSHIN FILE NEXUS.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

async def deliver_files(client: Client, user: User, encoded: str):
    cfg = await get_settings()
    delete_time = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
    user_id = user.id

    try:
        decoded = decode(encoded)
    except Exception:
        await client.send_message(
            user_id,
            "<b>❌ CORRUPTED LINK</b> — Payload invalid or tampered.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    sent_ids: list[int] = []

    # ── Pre-file message ──────────────────────────────────────────────────────
    pre_text = cfg.get("pre_file_msg") or DEFAULT_PRE_FILE
    pre_text = fill_placeholders(pre_text, user)
    try:
        pre_m = await client.send_message(
            user_id, pre_text, parse_mode=enums.ParseMode.HTML
        )
        sent_ids.append(pre_m.id)
    except Exception as e:
        logger.warning(f"Pre-file msg failed: {e}")

    # ── Single file ───────────────────────────────────────────────────────────
    if decoded.startswith("file_"):
        mid = int(decoded.split("_", 1)[1])
        try:
            m = await client.copy_message(user_id, Config.DB_CHANNEL, mid)
            sent_ids.append(m.id)
        except Exception as e:
            await client.send_message(
                user_id,
                f"<b>❌ RETRIEVAL FAILED:</b>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return

    # ── Batch ─────────────────────────────────────────────────────────────────
    elif decoded.startswith("batch_"):
        parts = decoded.split("_")
        s_id, e_id = int(parts[1]), int(parts[2])
        count = e_id - s_id + 1
        notice = await client.send_message(
            user_id,
            f"<b>⚡ BATCH TRANSFER SEQUENCE</b>\n\n"
            f"<blockquote>"
            f"[ MODE    : BATCH DELIVERY     ]\n"
            f"[ FILES   : {count:<22}]\n"
            f"[ STATUS  : TRANSMITTING...    ]"
            f"</blockquote>\n"
            f"<i>Copying files from the nexus...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        sent_ids.append(notice.id)
        for mid in range(s_id, e_id + 1):
            try:
                m = await client.copy_message(user_id, Config.DB_CHANNEL, mid)
                sent_ids.append(m.id)
                await asyncio.sleep(0.3)
            except Exception as ex:
                logger.warning(f"Batch copy failed [{mid}]: {ex}")
    else:
        await client.send_message(
            user_id, "<b>❌ UNKNOWN LINK FORMAT.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # ── Auto-delete notice ────────────────────────────────────────────────────
    if sent_ids and delete_time > 0:
        mins, secs = divmod(delete_time, 60)
        time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        del_text = cfg.get("auto_del_msg") or DEFAULT_AUTO_DEL
        del_text = del_text.replace("{time}", time_str)
        del_text = fill_placeholders(del_text, user)
        d = await client.send_message(
            user_id, del_text, parse_mode=enums.ParseMode.HTML
        )
        sent_ids.append(d.id)
        asyncio.create_task(auto_delete(client, user_id, sent_ids, delete_time))

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    user = message.from_user
    uid  = user.id
    await register_user(uid)
    cfg  = await get_settings()

    # ── Deep-link ────────────────────────────────────────────────────────────
    if len(message.command) > 1:
        encoded    = message.command[1]
        violations = await get_fsub_violations(client, uid)
        if violations:
            markup = await build_fsub_markup(client, violations, encoded)
            await message.reply(
                FSUB_MSG, reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
            return
        await deliver_files(client, user, encoded)
        return

    # ── Welcome flow: message FIRST, then sticker ────────────────────────────
    welcome = cfg.get("start_message") or DEFAULT_START
    welcome = fill_placeholders(welcome, user)

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Kenshin Anime", url="https://t.me/KENSHIN_ANIME"),
            InlineKeyboardButton("📚 Help", callback_data="cb_help"),
        ],
        [InlineKeyboardButton("⚙️ Command Matrix", callback_data="cb_cmds")],
    ])

    # 1️⃣ Send welcome message (with or without image)
    images = cfg.get("start_images", [])
    if images:
        try:
            await client.send_photo(
                message.chat.id,
                photo=random.choice(images),
                caption=welcome,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            await message.reply(
                welcome, reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML,
            )
    else:
        await message.reply(
            welcome, reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML,
        )

    # 2️⃣ Sticker AFTER message — auto-deletes in 5s
    if cfg.get("start_sticker"):
        try:
            sticker_msg = await client.send_sticker(
                message.chat.id, cfg["start_sticker"]
            )
            asyncio.create_task(_delete_after(sticker_msg, 5))
        except Exception:
            pass


@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, message: Message):
    await message.reply(
        "<b>📡 KENSHIN FILE NEXUS — COMMAND MATRIX</b>\n\n"
        "<blockquote>"
        "[ CENTRAL DATABASE  :  CONNECTED  ]\n"
        "[ AUTHORIZATION     :  GRANTED    ]\n"
        "[ LOADING REGISTRY  :  COMPLETE   ]"
        "</blockquote>\n\n"
        "<b>━━━ 🌐 USER PROTOCOLS ━━━</b>\n"
        "/start → Initialize the system protocol\n"
        "/help → Display this central command manual\n"
        "/ping → Check system latency &amp; status\n"
        "/cancel → Terminate the current operation\n\n"
        "<b>━━━ 🔗 LINK GENERATION ━━━</b>\n"
        "/genlink → Generate a singular encrypted file link\n"
        "  ↳ <i>Reply to any file/media</i>\n"
        "/batch → Initiate a multi-file batch sequence\n"
        "/process → Finalize batch &amp; generate access key\n\n"
        "<b>━━━ 🛡️ ADMIN MATRIX ━━━</b>\n"
        "/stats → View global system analytics\n"
        "/broadcast → Transmit a global frequency message\n"
        "/add_admin → Authorize a new system administrator\n"
        "/del_admin → Revoke administrative privileges\n\n"
        "<b>━━━ 📡 SECURITY CHANNELS ━━━</b>\n"
        "/add_fsub → Link a security requirement channel\n"
        "/del_fsub → Remove a security requirement channel\n\n"
        "<b>━━━ 🎨 INTERFACE CONFIG ━━━</b>\n"
        "/set_start → Configure the primary greeting message\n"
        "/del_start → Reset greeting to default\n"
        "/add_img → Insert a visual asset into start rotation\n"
        "/del_imgs → Wipe all visual assets from memory\n"
        "/set_sticker → Set the system greeting sticker\n"
        "/del_sticker → Remove the greeting sticker\n\n"
        "<b>━━━ 📨 FILE DELIVERY CONFIG ━━━</b>\n"
        "/set_pre_msg → Set message shown BEFORE file delivery\n"
        "  ↳ <i>Supports: {mention} {first_name} {last_name} {username} {id} {channel}</i>\n"
        "/del_pre_msg → Reset to default pre-file message\n"
        "/set_auto_del_msg → Set custom auto-delete warning message\n"
        "  ↳ <i>Supports: {time} {mention} {first_name}</i>\n"
        "/del_auto_del_msg → Reset to default auto-delete message\n"
        "/set_delete_time → Configure auto-purge timer (seconds)\n\n"
        "<b>━━━ 📋 PLACEHOLDERS ━━━</b>\n"
        "<blockquote>"
        "{mention}    → Clickable user mention\n"
        "{first_name} → User's first name\n"
        "{last_name}  → User's last name\n"
        "{username}   → @username or first name\n"
        "{id}         → User's Telegram ID\n"
        "{channel}    → @KENSHIN_ANIME\n"
        "{time}       → Auto-delete countdown (del msg only)"
        "</blockquote>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>⚡ KENSHIN FILE NEXUS — Encrypted. Secured. Eternal.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("ping") & filters.private)
async def cmd_ping(client: Client, message: Message):
    t = time.monotonic()
    m = await message.reply(
        "<b>⚡ Scanning core systems...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    lat = (time.monotonic() - t) * 1000
    await m.edit(
        f"<b>🟢 SYSTEM STATUS — FULLY OPERATIONAL</b>\n\n"
        f"<blockquote>"
        f"[ LATENCY    : {lat:.2f} ms            ]\n"
        f"[ API STATUS : CONNECTED               ]\n"
        f"[ ENCRYPTION : ACTIVE                  ]\n"
        f"[ DATABASE   : MONGODB                 ]\n"
        f"[ UPTIME     : STABLE                  ]"
        f"</blockquote>\n\n"
        f"<i>⚡ All systems nominal. Nexus is alive.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, message: Message):
    uid = message.from_user.id
    if uid in batch_sessions:
        count = len(batch_sessions[uid])
        del batch_sessions[uid]
        await message.reply(
            f"<b>🛑 OPERATION TERMINATED</b>\n\n"
            f"<blockquote>"
            f"[ BATCH SESSION  : CLEARED    ]\n"
            f"[ FILES IN QUEUE : {count:<10}]\n"
            f"[ STATUS         : STANDBY    ]"
            f"</blockquote>\n"
            f"<i>All queued data purged from memory.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply(
            "<b>⚠️ NO ACTIVE OPERATION</b>\n"
            "<i>System is already in standby mode.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("genlink") & filters.private)
async def cmd_genlink(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    target = message.reply_to_message
    if not target:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "Reply to a <b>file/media/document</b> with /genlink.\n\n"
            "<b>Steps:</b>\n"
            "1️⃣ Send or forward a file to the bot\n"
            "2️⃣ Reply to that file with /genlink\n"
            "3️⃣ Get your shareable encrypted link ⚡",
            parse_mode=enums.ParseMode.HTML,
        )

    proc = await message.reply(
        "<b>⚙️ Encrypting payload — storing in nexus...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        fwd      = await target.forward(Config.DB_CHANNEL)
        link_str = encode(f"file_{fwd.id}")
        me       = await client.get_me()
        link     = f"https://t.me/{me.username}?start={link_str}"
        cfg      = await get_settings()
        await update_settings("total_links", cfg.get("total_links", 0) + 1)
        await proc.edit(
            f"<b>🔗 ENCRYPTION COMPLETE — LINK FORGED</b>\n\n"
            f"<blockquote>"
            f"[ STATUS     : SUCCESS              ]\n"
            f"[ TYPE       : SINGLE FILE          ]\n"
            f"[ MSG ID     : {fwd.id:<20}]\n"
            f"[ ENCRYPTED  : BASE64-URL           ]"
            f"</blockquote>\n\n"
            f"<b>🌐 Shareable Access Link:</b>\n"
            f"<code>{link}</code>\n\n"
            f"<i>📋 Tap the link above to copy. Share freely.</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⚡ KENSHIN FILE NEXUS</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await proc.edit(
            f"<b>❌ OPERATION FAILED:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    batch_sessions[message.from_user.id] = []
    await message.reply(
        "<b>📦 BATCH SEQUENCE — INITIATED</b>\n\n"
        "<blockquote>"
        "[ MODE      : CAPTURE ACTIVE     ]\n"
        "[ QUEUE     : 0 FILES            ]\n"
        "[ RECEIVER  : LISTENING...       ]"
        "</blockquote>\n\n"
        "<b>📤 HOW TO USE:</b>\n"
        "→ Forward or send files <b>one by one</b> to the bot\n"
        "→ Each file gets queued into the nexus automatically\n"
        "→ Send /process when <b>all files</b> are uploaded\n"
        "→ Send /cancel to abort the operation\n\n"
        "<i>⚡ Begin transmission now — the nexus is listening...</i>",
        parse_mode=enums.ParseMode.HTML,
    )


def _in_batch(_, __, msg: Message) -> bool:
    return bool(msg.from_user and msg.from_user.id in batch_sessions)

in_batch_filter = filters.create(_in_batch)

@app.on_message(
    filters.private & in_batch_filter &
    (filters.document | filters.video | filters.audio | filters.photo |
     filters.voice | filters.video_note | filters.animation)
)
async def collect_batch_file(client: Client, message: Message):
    uid = message.from_user.id
    try:
        fwd = await message.forward(Config.DB_CHANNEL)
        batch_sessions[uid].append(fwd.id)
        n = len(batch_sessions[uid])
        await message.reply(
            f"<b>✅ FILE #{n} QUEUED</b>\n"
            f"<i>Stored securely in the nexus.</i>\n\n"
            f"📦 <b>Queue size:</b> <code>{n}</code> file(s)\n"
            f"<i>Send /process when done | /cancel to abort.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(
            f"<b>❌ QUEUE ERROR:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("process") & filters.private)
async def cmd_process(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    uid     = message.from_user.id
    session = batch_sessions.get(uid)
    if not session:
        return await message.reply(
            "<b>⚠️ NO ACTIVE BATCH SESSION</b>\n"
            "<i>Start one with /batch first, then send files.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    start_id = min(session)
    end_id   = max(session)
    count    = len(session)
    proc     = await message.reply(
        "<b>⚙️ Sealing batch — generating encrypted access key...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    link_str = encode(f"batch_{start_id}_{end_id}")
    me       = await client.get_me()
    link     = f"https://t.me/{me.username}?start={link_str}"
    cfg      = await get_settings()
    await update_settings("total_links", cfg.get("total_links", 0) + 1)
    del batch_sessions[uid]
    await proc.edit(
        f"<b>🔗 BATCH SEALED — ACCESS KEY GENERATED</b>\n\n"
        f"<blockquote>"
        f"[ STATUS     : SUCCESS              ]\n"
        f"[ TYPE       : BATCH SEQUENCE       ]\n"
        f"[ FILES      : {count:<20}]\n"
        f"[ MSG RANGE  : {start_id} → {end_id}   ]\n"
        f"[ ENCRYPTED  : BASE64-URL           ]"
        f"</blockquote>\n\n"
        f"<b>🌐 Batch Access Link:</b>\n"
        f"<code>{link}</code>\n\n"
        f"<i>📋 Share this link — delivers all {count} file(s) at once.</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⚡ KENSHIN FILE NEXUS</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg        = await get_settings()
    total_u    = await get_total_users()
    del_time   = cfg.get("auto_delete_time", 0)
    del_str    = f"{del_time // 60}m {del_time % 60}s" if del_time else "DISABLED"
    await message.reply(
        f"<b>📊 SYSTEM ANALYTICS — GLOBAL REPORT</b>\n\n"
        f"<blockquote>"
        f"[ USERS REGISTERED  : {total_u:<10}]\n"
        f"[ ADMINS ACTIVE     : {len(cfg.get('admins', [])):<10}]\n"
        f"[ TOTAL LINKS GEN.  : {cfg.get('total_links', 0):<10}]\n"
        f"[ FSUB CHANNELS     : {len(cfg.get('fsub_channels', [])):<10}]\n"
        f"[ START IMAGES      : {len(cfg.get('start_images', [])):<10}]\n"
        f"[ AUTO-DELETE TIMER : {del_str:<10}]\n"
        f"[ STICKER ACTIVE    : {'YES' if cfg.get('start_sticker') else 'NO':<10}]\n"
        f"[ PRE-FILE MSG      : {'CUSTOM' if cfg.get('pre_file_msg') else 'DEFAULT':<10}]\n"
        f"[ DATABASE          : MONGODB    ]\n"
        f"[ SYSTEM STATUS     : ONLINE     ]"
        f"</blockquote>\n\n"
        f"<i>⚡ Report generated — KENSHIN FILE NEXUS</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("broadcast") & filters.private)
async def cmd_broadcast(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not message.reply_to_message:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\nReply to any message with /broadcast.",
            parse_mode=enums.ParseMode.HTML,
        )
    users = await get_all_users()
    prog  = await message.reply(
        f"<b>📡 INITIATING GLOBAL BROADCAST</b>\n"
        f"<i>Transmitting to <code>{len(users)}</code> registered nodes...</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    ok = fail = 0
    for uid in users:
        try:
            await message.reply_to_message.forward(uid)
            ok += 1
            await asyncio.sleep(0.05)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception:
            fail += 1
    await prog.edit(
        f"<b>📡 BROADCAST — TRANSMISSION COMPLETE</b>\n\n"
        f"<blockquote>"
        f"[ SUCCESS  : {ok} nodes reached  ]\n"
        f"[ FAILED   : {fail} nodes offline ]\n"
        f"[ TOTAL    : {len(users)} registered    ]"
        f"</blockquote>\n"
        f"<i>⚡ Global frequency transmission complete.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("add_fsub") & filters.private)
async def cmd_add_fsub(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split()
    if len(parts) < 3:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "<b>Usage:</b> /add_fsub &lt;channel_id&gt; &lt;type&gt;\n\n"
            "<b>Types:</b>\n"
            "• <code>public</code> → Public channel\n"
            "• <code>private</code> → Private channel (invite link)\n"
            "• <code>request</code> → Private channel (join request)\n\n"
            "<b>Example:</b> /add_fsub -1001234567890 private\n\n"
            "<i>⚠️ Bot must be admin in the channel first!</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        ch_id      = int(parts[1])
        raw        = parts[2].lower()
        if raw not in ("public", "private", "request"):
            return await message.reply(
                "<b>❌ Invalid type.</b> Use: <code>public</code>, <code>private</code>, or <code>request</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        ch_type = "private_request" if raw == "request" else raw
        chat    = await client.get_chat(ch_id)
        cfg     = await get_settings()
        if any(c["id"] == ch_id for c in cfg["fsub_channels"]):
            return await message.reply(
                "<b>⚠️ Channel already linked in security protocol.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        cfg["fsub_channels"].append({"id": ch_id, "name": chat.title, "type": ch_type})
        await update_settings("fsub_channels", cfg["fsub_channels"])
        await message.reply(
            f"<b>✅ SECURITY CHANNEL LINKED</b>\n\n"
            f"<blockquote>"
            f"[ CHANNEL  : {chat.title[:22]:<22}]\n"
            f"[ ID       : {ch_id}          ]\n"
            f"[ TYPE     : {ch_type:<22}    ]\n"
            f"[ STATUS   : ACTIVE            ]"
            f"</blockquote>\n"
            f"<i>⚡ Users must join this channel to access any file.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(
            f"<b>❌ OPERATION FAILED:</b>\n<code>{e}</code>\n\n"
            "<i>Ensure bot is admin in the channel.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("del_fsub") & filters.private)
async def cmd_del_fsub(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg      = await get_settings()
    channels = cfg.get("fsub_channels", [])
    if not channels:
        return await message.reply(
            "<b>⚠️ No security channels currently configured.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split()
    if len(parts) < 2:
        ch_list = "\n".join(
            [f"• <code>{c['id']}</code> — <b>{c['name']}</b> [{c['type']}]"
             for c in channels]
        )
        return await message.reply(
            f"<b>📋 ACTIVE SECURITY CHANNELS:</b>\n\n{ch_list}\n\n"
            f"<b>Usage:</b> /del_fsub &lt;channel_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        ch_id    = int(parts[1])
        new_list = [c for c in channels if c["id"] != ch_id]
        if len(new_list) == len(channels):
            return await message.reply(
                "<b>❌ Channel ID not found in security registry.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        await update_settings("fsub_channels", new_list)
        await message.reply(
            f"<b>🚫 SECURITY CHANNEL REMOVED</b>\n\n"
            f"<blockquote>"
            f"[ CHANNEL ID  : {ch_id}   ]\n"
            f"[ STATUS      : UNLINKED  ]"
            f"</blockquote>\n"
            f"<i>⚡ Channel removed from force-sub protocol.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(
            f"<b>❌ Error:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("set_start") & filters.private)
async def cmd_set_start(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "<b>Usage:</b> /set_start &lt;your HTML message&gt;\n\n"
            "<b>HTML Tags:</b> &lt;b&gt; &lt;i&gt; &lt;code&gt; &lt;blockquote&gt;\n"
            "<b>Placeholders:</b> {mention} {first_name} {username} {id} {channel}\n\n"
            "Use /del_start to restore the default message.",
            parse_mode=enums.ParseMode.HTML,
        )
    await update_settings("start_message", parts[1])
    await message.reply(
        "<b>✅ PRIMARY GREETING RECONFIGURED</b>\n"
        "<i>⚡ New welcome message will appear on next /start.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_start") & filters.private)
async def cmd_del_start(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML
        )
    await update_settings("start_message", "")
    await message.reply(
        "<b>✅ Start message reset to default.</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("set_pre_msg") & filters.private)
async def cmd_set_pre_msg(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "<b>Usage:</b> /set_pre_msg &lt;HTML message&gt;\n\n"
            "This message is sent to the user <b>BEFORE</b> file delivery.\n\n"
            "<b>Placeholders:</b>\n"
            "<blockquote>"
            "{mention}    → Clickable user mention\n"
            "{first_name} → User's first name\n"
            "{last_name}  → User's last name\n"
            "{username}   → @username or first name\n"
            "{id}         → User's Telegram ID\n"
            "{channel}    → @KENSHIN_ANIME"
            "</blockquote>\n\n"
            "<b>Example:</b>\n"
            "<code>MADARA UCHIHA 🌑\n\nGreetings, {mention}.\n\n&lt;blockquote&gt;Your files await.&lt;/blockquote&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await update_settings("pre_file_msg", parts[1])
    await message.reply(
        "<b>✅ PRE-FILE MESSAGE CONFIGURED</b>\n"
        "<i>⚡ This message will appear before every file delivery.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_pre_msg") & filters.private)
async def cmd_del_pre_msg(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML
        )
    await update_settings("pre_file_msg", "")
    await message.reply(
        "<b>✅ Pre-file message reset to default.</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("set_auto_del_msg") & filters.private)
async def cmd_set_auto_del_msg(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "<b>Usage:</b> /set_auto_del_msg &lt;HTML message&gt;\n\n"
            "This message appears after file delivery (with timer).\n\n"
            "<b>Placeholders:</b>\n"
            "<blockquote>"
            "{time}       → Auto-delete countdown (e.g. 10m 0s)\n"
            "{mention}    → Clickable user mention\n"
            "{first_name} → User's first name"
            "</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    await update_settings("auto_del_msg", parts[1])
    await message.reply(
        "<b>✅ AUTO-DELETE MESSAGE CONFIGURED</b>\n"
        "<i>⚡ This message will appear after every file delivery.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_auto_del_msg") & filters.private)
async def cmd_del_auto_del_msg(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML
        )
    await update_settings("auto_del_msg", "")
    await message.reply(
        "<b>✅ Auto-delete message reset to default.</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("add_img") & filters.private)
async def cmd_add_img(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    target = message.reply_to_message
    if not target or not target.photo:
        return await message.reply(
            "<b>⚠️ Reply to a PHOTO with /add_img to add it to the start image rotation.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg    = await get_settings()
    images = cfg.get("start_images", [])
    images.append(target.photo.file_id)
    await update_settings("start_images", images)
    await message.reply(
        f"<b>✅ VISUAL ASSET INTEGRATED</b>\n\n"
        f"<blockquote>"
        f"[ IMAGES IN ROTATION : {len(images)} ]\n"
        f"[ STATUS             : ACTIVE   ]"
        f"</blockquote>\n"
        f"<i>⚡ Bot will randomly select from available images on each /start.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_imgs") & filters.private)
async def cmd_del_imgs(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg   = await get_settings()
    count = len(cfg.get("start_images", []))
    await update_settings("start_images", [])
    await message.reply(
        f"<b>🗑️ VISUAL MEMORY PURGED</b>\n\n"
        f"<blockquote>"
        f"[ DELETED : {count} image(s) wiped  ]\n"
        f"[ STATUS  : MEMORY CLEARED         ]"
        f"</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("set_sticker") & filters.private)
async def cmd_set_sticker(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    target = message.reply_to_message
    if not target or not target.sticker:
        return await message.reply(
            "<b>⚠️ Reply to a STICKER with /set_sticker.</b>\n\n"
            "<i>The sticker appears AFTER the welcome message and auto-deletes in 5 seconds.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await update_settings("start_sticker", target.sticker.file_id)
    await message.reply(
        "<b>✅ GREETING STICKER CONFIGURED</b>\n\n"
        "Sticker will appear <b>after</b> the welcome message\n"
        "and auto-deletes in <b>5 seconds</b>.\n"
        "<i>⚡ Signature is now active.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_sticker") & filters.private)
async def cmd_del_sticker(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML
        )
    await update_settings("start_sticker", "")
    await message.reply(
        "<b>✅ Greeting sticker removed.</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("add_admin") & filters.private)
async def cmd_add_admin(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply(
            "<b>🔒 OWNER-ONLY COMMAND.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    target = message.reply_to_message
    parts  = message.text.split()
    if target:
        new_id = target.from_user.id
        name   = target.from_user.first_name
    elif len(parts) > 1:
        try:
            new_id = int(parts[1])
            name   = f"ID#{new_id}"
        except ValueError:
            return await message.reply(
                "<b>❌ Invalid user ID.</b>", parse_mode=enums.ParseMode.HTML
            )
    else:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "• Reply to a user's message → /add_admin\n"
            "• Or: /add_admin &lt;user_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg = await get_settings()
    if new_id == Config.OWNER_ID or new_id in cfg.get("admins", []):
        return await message.reply(
            "<b>⚠️ User already has admin clearance.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    admins = cfg.get("admins", [])
    admins.append(new_id)
    await update_settings("admins", admins)
    await message.reply(
        f"<b>✅ ADMIN CLEARANCE GRANTED</b>\n\n"
        f"<blockquote>"
        f"[ OPERATOR  : {name[:22]:<22}]\n"
        f"[ USER ID   : {new_id}         ]\n"
        f"[ LEVEL     : ADMINISTRATOR    ]\n"
        f"[ STATUS    : AUTHORIZED       ]"
        f"</blockquote>\n"
        f"<i>⚡ Access to admin command matrix granted.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("del_admin") & filters.private)
async def cmd_del_admin(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply(
            "<b>🔒 OWNER-ONLY COMMAND.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split()
    if len(parts) < 2:
        cfg        = await get_settings()
        admin_list = "\n".join([f"• <code>{a}</code>" for a in cfg.get("admins", [])]) or "<i>None</i>"
        return await message.reply(
            f"<b>📋 ACTIVE ADMINISTRATORS:</b>\n\n{admin_list}\n\n"
            f"<b>Usage:</b> /del_admin &lt;user_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        rm_id = int(parts[1])
        if rm_id == Config.OWNER_ID:
            return await message.reply(
                "<b>❌ Cannot revoke owner clearance.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        cfg    = await get_settings()
        admins = cfg.get("admins", [])
        if rm_id not in admins:
            return await message.reply(
                "<b>❌ User is not in the admin registry.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        admins.remove(rm_id)
        await update_settings("admins", admins)
        await message.reply(
            f"<b>🚫 ADMIN CLEARANCE REVOKED</b>\n\n"
            f"<blockquote>"
            f"[ USER ID  : {rm_id}        ]\n"
            f"[ STATUS   : UNAUTHORIZED   ]"
            f"</blockquote>\n"
            f"<i>⚡ Access to admin matrix terminated.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(
            f"<b>❌ Error:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("set_delete_time") & filters.private)
async def cmd_set_delete_time(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg   = await get_settings()
    parts = message.text.split()
    if len(parts) < 2:
        current = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
        status  = f"{current // 60}m {current % 60}s" if current else "DISABLED"
        return await message.reply(
            f"<b>⏳ AUTO-PURGE CONFIGURATION</b>\n\n"
            f"Current timer: <code>{current}s</code> → <b>{status}</b>\n\n"
            f"<b>Usage:</b> /set_delete_time &lt;seconds&gt;\n"
            f"Set <code>0</code> to <b>disable</b> auto-delete.\n\n"
            f"<b>Presets:</b>\n"
            f"<blockquote>"
            f"/set_delete_time 300  → 5 minutes\n"
            f"/set_delete_time 600  → 10 minutes\n"
            f"/set_delete_time 1800 → 30 minutes\n"
            f"/set_delete_time 3600 → 1 hour\n"
            f"/set_delete_time 0    → Disabled"
            f"</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        secs = int(parts[1])
        if secs < 0:
            return await message.reply(
                "<b>❌ Value cannot be negative.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        await update_settings("auto_delete_time", secs)
        status = f"{secs // 60}m {secs % 60}s" if secs else "DISABLED"
        await message.reply(
            f"<b>✅ AUTO-PURGE TIMER UPDATED</b>\n\n"
            f"<blockquote>"
            f"[ TIMER   : {status:<28}]\n"
            f"[ SECONDS : {secs:<28}]"
            f"</blockquote>\n"
            f"<i>⚡ Files will auto-delete after delivery.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except ValueError:
        await message.reply(
            "<b>❌ Invalid value.</b> Provide time in whole seconds (e.g. <code>600</code>).",
            parse_mode=enums.ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK QUERY
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query()
async def on_callback(client: Client, cq: CallbackQuery):
    data = cq.data
    uid  = cq.from_user.id
    user = cq.from_user

    if data.startswith("verify_"):
        encoded    = data[7:]
        violations = await get_fsub_violations(client, uid)
        if violations:
            markup = await build_fsub_markup(client, violations, encoded)
            await cq.answer(
                "⚠️ You haven't joined all required channels yet!", show_alert=True
            )
            try:
                await cq.message.edit_reply_markup(markup)
            except Exception:
                pass
            return
        await cq.answer("✅ Verification passed! Unlocking files...", show_alert=False)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await client.send_message(
            uid,
            "<b>✅ ACCESS GRANTED — INITIATING TRANSFER</b>\n\n"
            "<blockquote>"
            "[ SECURITY CHECK  : PASSED    ]\n"
            "[ CLEARANCE       : GRANTED   ]\n"
            "[ FILE ACCESS     : UNLOCKED  ]"
            "</blockquote>\n"
            "<i>Retrieving your files from the nexus...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await deliver_files(client, user, encoded)

    elif data == "cb_help":
        await cq.answer("📚 Send /help for the full Command Matrix!", show_alert=True)
    elif data == "cb_cmds":
        await cq.answer("⚙️ Send /help to see all available commands.", show_alert=True)
    else:
        await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    await app.start()
    me = await app.get_me()
    if not Config.DB_CHANNEL:
        logger.warning("⚠️  DB_CHANNEL is not set! File storage will fail.")
    logger.info("=" * 54)
    logger.info("  KENSHIN FILE NEXUS — SYSTEM ONLINE")
    logger.info(f"  Bot      :  @{me.username}")
    logger.info(f"  Name     :  {me.first_name}")
    logger.info(f"  Owner ID :  {Config.OWNER_ID}")
    logger.info(f"  DB Chan  :  {Config.DB_CHANNEL}")
    logger.info(f"  Database :  MongoDB ✓")
    logger.info(f"  Mode     :  HTML parse + Blockquote ✓")
    logger.info("=" * 54)
    await idle()
    await app.stop()
    logger.info("KENSHIN FILE NEXUS — SHUTDOWN COMPLETE")

app.run(main())
