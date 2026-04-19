# ╔══════════════════════════════════════════════════════════════════╗
# ║        MADARA UCHIHA FILE BOT — by @KENSHIN_ANIME               ║
# ║        Full-Featured | Multi-Clone | MongoDB | Pyrofork          ║
# ╚══════════════════════════════════════════════════════════════════╝

import asyncio
import base64
import logging
import random
import time

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, enums, filters, handlers, idle
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import (
    BotCommand, BotCommandScopeAllPrivateChats,
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, User,
)

from config import Config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("MadaraBot")

# ── Main client ───────────────────────────────────────────────────────────────
app = Client(
    "madara_main",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

# ══════════════════════════════════════════════════════════════════════════════
#  MONGODB  —  per-bot settings keyed by bot_id
# ══════════════════════════════════════════════════════════════════════════════
_mongo       = AsyncIOMotorClient(Config.MONGO_URI)
_db          = _mongo["madara_bot"]
_settings    = _db["settings"]
_users       = _db["users"]
_clones_col  = _db["clones"]

def _sid(bot_id: int) -> str:
    return f"bot_{bot_id}"

async def get_cfg(bot_id: int) -> dict:
    doc = await _settings.find_one({"_id": _sid(bot_id)})
    if doc:
        return doc
    default = {
        "_id":             _sid(bot_id),
        "bot_name":        "KENSHIN FILE NEXUS",
        "admins":          [Config.OWNER_ID],
        "fsub_channels":   [],
        "start_images":    [],
        "start_message":   "",
        "start_sticker":   "",
        "pre_file_msg":    "",
        "auto_del_msg":    "",
        "total_links":     0,
        "auto_delete_time": Config.AUTO_DELETE_TIME,
    }
    await _settings.insert_one(default)
    return default

async def upd(bot_id: int, field: str, value):
    await _settings.update_one(
        {"_id": _sid(bot_id)}, {"$set": {field: value}}, upsert=True
    )

async def reg_user(bot_id: int, uid: int):
    await _users.update_one(
        {"_id": f"{bot_id}_{uid}"}, {"$set": {"bot_id": bot_id, "user_id": uid}},
        upsert=True,
    )

async def get_users(bot_id: int) -> list[int]:
    return [
        doc["user_id"]
        async for doc in _users.find({"bot_id": bot_id}, {"user_id": 1})
    ]

async def count_users(bot_id: int) -> int:
    return await _users.count_documents({"bot_id": bot_id})

# ══════════════════════════════════════════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════════════════════════════════════════
# (bot_id, user_id) → [forwarded_msg_ids]
batch_sessions: dict[tuple, list] = {}

# owner_id → bot_id  (which bot is awaiting a clone token from owner)
clone_pending: dict[int, int] = {}

# bot_id → Client
active_clones: dict[int, Client] = {}

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def decode(s: str) -> str:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode()).decode()

async def is_admin(bot_id: int, uid: int) -> bool:
    if uid == Config.OWNER_ID:
        return True
    cfg = await get_cfg(bot_id)
    return uid in cfg.get("admins", [])

def fill(text: str, user: User, extra: dict = None) -> str:
    if not text:
        return text
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    result = (
        text
        .replace("{mention}",    mention)
        .replace("{first_name}", user.first_name or "")
        .replace("{last_name}",  user.last_name  or "")
        .replace("{username}",   f"@{user.username}" if user.username else user.first_name)
        .replace("{id}",         str(user.id))
        .replace("{channel}",    "@KENSHIN_ANIME")
    )
    if extra:
        for k, v in extra.items():
            result = result.replace(f"{{{k}}}", str(v))
    return result

async def auto_delete(client: Client, chat_id: int, msg_ids: list, delay: int):
    await asyncio.sleep(delay)
    for mid in msg_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass

async def _del_after(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass

async def get_fsub_violations(client: Client, bot_id: int, user_id: int) -> list:
    cfg = await get_cfg(bot_id)
    out = []
    for ch in cfg.get("fsub_channels", []):
        try:
            m = await client.get_chat_member(ch["id"], user_id)
            if m.status.value in ("left", "kicked", "banned"):
                out.append(ch)
        except UserNotParticipant:
            out.append(ch)
        except Exception as e:
            logger.warning(f"FSub [{ch['id']}]: {e}")
    return out

async def build_fsub_kb(client: Client, violations: list, encoded: str) -> InlineKeyboardMarkup:
    rows = []
    for ch in violations:
        try:
            if ch["type"] == "public":
                chat = await client.get_chat(ch["id"])
                url  = f"https://t.me/{chat.username}"
            elif ch["type"] == "private_request":
                inv  = await client.create_chat_invite_link(ch["id"], creates_join_request=True)
                url  = inv.invite_link
            else:
                inv  = await client.create_chat_invite_link(ch["id"])
                url  = inv.invite_link
            rows.append([InlineKeyboardButton(f"📡  Join — {ch['name']}", url=url)])
        except Exception as e:
            logger.error(f"FSub button [{ch['id']}]: {e}")
    rows.append([InlineKeyboardButton("✅  Verified — Unlock Files", callback_data=f"verify_{encoded}")])
    return InlineKeyboardMarkup(rows)

# ── Default messages ───────────────────────────────────────────────────────────
def DEFAULT_START(bot_name: str) -> str:
    return (
        f"<b>⚡ {bot_name} — SYSTEM ONLINE ⚡</b>\n\n"
        f"<blockquote>╔══════════════════════════════╗\n"
        f"║   INITIALIZING SYSTEM...     ║\n"
        f"║   ENCRYPTION   :  ACTIVE  ✓  ║\n"
        f"║   STORAGE      :  SECURED ✓  ║\n"
        f"║   FORCE-SUB    :  ENABLED ✓  ║\n"
        f"║   STATUS       :  ONLINE  ✓  ║\n"
        f"╚══════════════════════════════╝</blockquote>\n\n"
        f"🌑 <b>Welcome to the Shadow Archive.</b>\n"
        f"Files here are encrypted &amp; accessible only via verified links.\n\n"
        f"🔗 Use /help to access the <b>Command Matrix</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⚡ Powered by @KENSHIN_ANIME</i>"
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
    "💾 Download &amp; save immediately.</blockquote>"
)

# ══════════════════════════════════════════════════════════════════════════════
#  FILE DELIVERY  (forward — preserves thumbnail/cover)
# ══════════════════════════════════════════════════════════════════════════════
async def deliver_files(client: Client, user: User, encoded: str):
    bot_id      = client.me.id
    cfg         = await get_cfg(bot_id)
    delete_time = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
    uid         = user.id

    try:
        decoded = decode(encoded)
    except Exception:
        return await client.send_message(
            uid, "<b>❌ CORRUPTED LINK</b> — Payload tampered.",
            parse_mode=enums.ParseMode.HTML,
        )

    sent_ids: list[int] = []

    # 1️⃣ Pre-file message
    pre_text = cfg.get("pre_file_msg") or DEFAULT_PRE_FILE
    pre_text = fill(pre_text, user)
    try:
        pm = await client.send_message(uid, pre_text, parse_mode=enums.ParseMode.HTML)
        sent_ids.append(pm.id)
    except Exception as e:
        logger.warning(f"Pre-file msg error: {e}")

    # 2️⃣ Forward file(s) — preserves thumbnail
    if decoded.startswith("file_"):
        mid = int(decoded.split("_", 1)[1])
        try:
            fwds = await client.forward_messages(uid, Config.DB_CHANNEL, mid)
            if isinstance(fwds, list):
                sent_ids += [m.id for m in fwds]
            else:
                sent_ids.append(fwds.id)
        except Exception as e:
            return await client.send_message(
                uid, f"<b>❌ RETRIEVAL FAILED:</b>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )

    elif decoded.startswith("batch_"):
        parts = decoded.split("_")
        s_id, e_id = int(parts[1]), int(parts[2])
        msg_ids     = list(range(s_id, e_id + 1))
        count       = len(msg_ids)

        notice = await client.send_message(
            uid,
            f"<b>⚡ BATCH TRANSFER SEQUENCE</b>\n\n"
            f"<blockquote>[ MODE    : BATCH DELIVERY  ]\n"
            f"[ FILES   : {count:<20}]\n"
            f"[ STATUS  : TRANSMITTING... ]</blockquote>\n"
            f"<i>Forwarding files from the nexus...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        sent_ids.append(notice.id)

        # Forward in chunks of 200 (Telegram limit)
        chunk_size = 100
        for i in range(0, len(msg_ids), chunk_size):
            chunk = msg_ids[i:i + chunk_size]
            try:
                fwds = await client.forward_messages(uid, Config.DB_CHANNEL, chunk)
                if isinstance(fwds, list):
                    sent_ids += [m.id for m in fwds]
                else:
                    sent_ids.append(fwds.id)
                await asyncio.sleep(0.5)
            except Exception as ex:
                logger.warning(f"Batch forward chunk failed: {ex}")
    else:
        return await client.send_message(
            uid, "<b>❌ UNKNOWN LINK FORMAT.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    # 3️⃣ Auto-delete notice
    if sent_ids and delete_time > 0:
        mins, secs = divmod(delete_time, 60)
        t_str    = f"{mins}m {secs}s" if mins else f"{secs}s"
        del_text = cfg.get("auto_del_msg") or DEFAULT_AUTO_DEL
        del_text = fill(del_text, user, {"time": t_str})
        dm = await client.send_message(uid, del_text, parse_mode=enums.ParseMode.HTML)
        sent_ids.append(dm.id)
        asyncio.create_task(auto_delete(client, uid, sent_ids, delete_time))

# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS  (plain async functions — registered via setup_handlers)
# ══════════════════════════════════════════════════════════════════════════════

async def h_start(client: Client, message: Message):
    user   = message.from_user
    uid    = user.id
    bot_id = client.me.id
    await reg_user(bot_id, uid)
    cfg    = await get_cfg(bot_id)
    bn     = cfg.get("bot_name", "KENSHIN FILE NEXUS")

    if len(message.command) > 1:
        encoded    = message.command[1]
        violations = await get_fsub_violations(client, bot_id, uid)
        if violations:
            kb = await build_fsub_kb(client, violations, encoded)
            return await message.reply(
                "<b>🔐 SECURITY PROTOCOL — ACCESS RESTRICTED</b>\n\n"
                "<blockquote>[ CLEARANCE CHECK  : FAILED     ]\n"
                "[ FILE ACCESS      : LOCKED     ]\n"
                "[ ACTION REQUIRED  : JOIN BELOW ]</blockquote>\n\n"
                "⚠️ Subscribe to <b>ALL</b> channels below to unlock your files.",
                reply_markup=kb, parse_mode=enums.ParseMode.HTML,
            )
        return await deliver_files(client, user, encoded)

    welcome = cfg.get("start_message") or DEFAULT_START(bn)
    welcome = fill(welcome, user)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Kenshin Anime", url="https://t.me/KENSHIN_ANIME"),
            InlineKeyboardButton("📚 Help", callback_data="cb_help"),
        ],
        [InlineKeyboardButton("⚙️ Command Matrix", callback_data="cb_cmds")],
    ])

    # Send welcome message first
    images = cfg.get("start_images", [])
    try:
        if images:
            await client.send_photo(
                message.chat.id, photo=random.choice(images),
                caption=welcome, reply_markup=kb, parse_mode=enums.ParseMode.HTML,
            )
        else:
            await message.reply(welcome, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await message.reply(welcome, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    # Sticker AFTER welcome message
    if cfg.get("start_sticker"):
        try:
            sm = await client.send_sticker(message.chat.id, cfg["start_sticker"])
            asyncio.create_task(_del_after(sm, 5))
        except Exception:
            pass


async def h_help(client: Client, message: Message):
    bot_id = client.me.id
    cfg    = await get_cfg(bot_id)
    bn     = cfg.get("bot_name", "KENSHIN FILE NEXUS")
    await message.reply(
        f"<b>📡 {bn} — COMMAND MATRIX</b>\n\n"
        f"<blockquote>[ CENTRAL DATABASE  :  CONNECTED  ]\n"
        f"[ AUTHORIZATION     :  GRANTED    ]\n"
        f"[ LOADING REGISTRY  :  COMPLETE   ]</blockquote>\n\n"
        f"<b>━━━ 🌐 USER COMMANDS ━━━</b>\n"
        f"/start — Initialize the system protocol\n"
        f"/help — Display this command matrix\n"
        f"/ping — Check system latency &amp; status\n"
        f"/cancel — Terminate the current operation\n\n"
        f"<b>━━━ 🔗 LINK GENERATION (Admin) ━━━</b>\n"
        f"/genlink — Reply to a file → generate single link\n"
        f"/batch — Start multi-file batch capture session\n"
        f"/process — Seal batch &amp; generate access key\n\n"
        f"<b>━━━ 🛡️ ADMIN MATRIX ━━━</b>\n"
        f"/stats — View global system analytics\n"
        f"/broadcast — Transmit message to all users\n"
        f"/add_admin &lt;id&gt; — Authorize new administrator\n"
        f"/del_admin &lt;id&gt; — Revoke admin privileges\n\n"
        f"<b>━━━ 📡 FORCE SUBSCRIBE ━━━</b>\n"
        f"/add_fsub &lt;id&gt; &lt;type&gt; — Link security channel\n"
        f"  ↳ <i>type: public / private / request</i>\n"
        f"/del_fsub &lt;id&gt; — Remove security channel\n\n"
        f"<b>━━━ 🎨 INTERFACE CONFIG ━━━</b>\n"
        f"/set_bot_name &lt;name&gt; — Change bot name everywhere\n"
        f"/set_start &lt;HTML&gt; — Set welcome message\n"
        f"/del_start — Reset to default welcome\n"
        f"/add_img — Reply to photo → add to start rotation\n"
        f"/del_imgs — Clear all start images\n"
        f"/set_sticker — Reply to sticker → set greeting sticker\n"
        f"/del_sticker — Remove greeting sticker\n\n"
        f"<b>━━━ 📨 FILE DELIVERY CONFIG ━━━</b>\n"
        f"/set_pre_msg &lt;HTML&gt; — Message shown before files\n"
        f"/del_pre_msg — Reset to MADARA default\n"
        f"/set_auto_del_msg &lt;HTML&gt; — Auto-delete warning message\n"
        f"/del_auto_del_msg — Reset auto-delete message\n"
        f"/set_delete_time &lt;seconds&gt; — Set auto-purge timer\n\n"
        f"<b>━━━ 🤖 CLONE SYSTEM (Owner Only) ━━━</b>\n"
        f"/clone — Create a new bot clone with fresh token\n"
        f"/list_clones — List all active clones\n"
        f"/stop_clone &lt;bot_id&gt; — Stop a specific clone\n\n"
        f"<b>━━━ 📋 PLACEHOLDERS ━━━</b>\n"
        f"<blockquote>{{mention}}    → Clickable user mention\n"
        f"{{first_name}} → User's first name\n"
        f"{{last_name}}  → User's last name\n"
        f"{{username}}   → @username or first name\n"
        f"{{id}}         → Telegram user ID\n"
        f"{{channel}}    → @KENSHIN_ANIME\n"
        f"{{time}}       → Auto-delete countdown (del msg only)</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⚡ {bn} — Encrypted. Secured. Eternal.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_ping(client: Client, message: Message):
    bot_id = client.me.id
    cfg    = await get_cfg(bot_id)
    bn     = cfg.get("bot_name", "KENSHIN FILE NEXUS")
    t = time.monotonic()
    m = await message.reply("<b>⚡ Scanning core systems...</b>", parse_mode=enums.ParseMode.HTML)
    lat = (time.monotonic() - t) * 1000
    await m.edit(
        f"<b>🟢 SYSTEM STATUS — FULLY OPERATIONAL</b>\n\n"
        f"<blockquote>[ LATENCY    : {lat:.2f} ms   ]\n"
        f"[ API STATUS : CONNECTED  ]\n"
        f"[ DATABASE   : MONGODB    ]\n"
        f"[ BOT NAME   : {bn[:18]:<18}]\n"
        f"[ UPTIME     : STABLE     ]</blockquote>\n\n"
        f"<i>⚡ All systems nominal.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_cancel(client: Client, message: Message):
    bot_id = client.me.id
    uid    = message.from_user.id
    key    = (bot_id, uid)
    if key in batch_sessions:
        count = len(batch_sessions[key])
        del batch_sessions[key]
        await message.reply(
            f"<b>🛑 OPERATION TERMINATED</b>\n\n"
            f"<blockquote>[ BATCH SESSION  : CLEARED ]\n"
            f"[ FILES IN QUEUE : {count:<8}]\n"
            f"[ STATUS         : STANDBY ]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply(
            "<b>⚠️ NO ACTIVE OPERATION</b>\n<i>System already in standby.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


async def h_genlink(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    target = message.reply_to_message
    if not target:
        return await message.reply(
            "<b>⚠️ SYNTAX ERROR</b>\n\n"
            "Reply to any <b>file / media</b> with /genlink.\n\n"
            "<b>Steps:</b>\n"
            "1️⃣ Send or forward a file to the bot\n"
            "2️⃣ Reply to that file with /genlink\n"
            "3️⃣ Receive your encrypted access link ⚡",
            parse_mode=enums.ParseMode.HTML,
        )
    proc = await message.reply("<b>⚙️ Encrypting payload...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        fwds     = await target.forward(Config.DB_CHANNEL)
        fwd_id   = fwds.id if not isinstance(fwds, list) else fwds[0].id
        link_str = encode(f"file_{fwd_id}")
        me       = client.me
        link     = f"https://t.me/{me.username}?start={link_str}"
        cfg      = await get_cfg(bot_id)
        await upd(bot_id, "total_links", cfg.get("total_links", 0) + 1)
        await proc.edit(
            f"<b>🔗 LINK FORGED — ENCRYPTION COMPLETE</b>\n\n"
            f"<blockquote>[ STATUS  : SUCCESS      ]\n"
            f"[ TYPE    : SINGLE FILE  ]\n"
            f"[ MSG ID  : {fwd_id:<12}]\n"
            f"[ ENC     : BASE64-URL   ]</blockquote>\n\n"
            f"<b>🌐 Shareable Link:</b>\n<code>{link}</code>\n\n"
            f"<i>📋 Tap to copy. Share freely.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await proc.edit(f"<b>❌ FAILED:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


async def h_batch(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    key = (bot_id, message.from_user.id)
    batch_sessions[key] = []
    await message.reply(
        "<b>📦 BATCH SEQUENCE — INITIATED</b>\n\n"
        "<blockquote>[ MODE      : CAPTURE ACTIVE  ]\n"
        "[ QUEUE     : 0 FILES        ]\n"
        "[ RECEIVER  : LISTENING...   ]</blockquote>\n\n"
        "→ Forward files <b>one by one</b> to the bot\n"
        "→ Send /process when done\n"
        "→ Send /cancel to abort\n\n"
        "<i>⚡ The nexus is listening...</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_collect_batch(client: Client, message: Message):
    bot_id = client.me.id
    uid    = message.from_user.id
    key    = (bot_id, uid)
    try:
        fwds = await message.forward(Config.DB_CHANNEL)
        fwd_id = fwds.id if not isinstance(fwds, list) else fwds[0].id
        batch_sessions[key].append(fwd_id)
        n = len(batch_sessions[key])
        await message.reply(
            f"<b>✅ FILE #{n} QUEUED</b>\n"
            f"<i>Stored in nexus.</i>\n\n"
            f"📦 Queue: <code>{n}</code> file(s)\n"
            f"<i>/process to seal | /cancel to abort</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"<b>❌ QUEUE ERROR:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


async def h_process(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    uid  = message.from_user.id
    key  = (bot_id, uid)
    sess = batch_sessions.get(key)
    if not sess:
        return await message.reply(
            "<b>⚠️ NO ACTIVE BATCH SESSION</b>\n<i>Start with /batch first.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    s_id, e_id, count = min(sess), max(sess), len(sess)
    proc = await message.reply("<b>⚙️ Sealing batch...</b>", parse_mode=enums.ParseMode.HTML)
    link_str = encode(f"batch_{s_id}_{e_id}")
    me       = client.me
    link     = f"https://t.me/{me.username}?start={link_str}"
    cfg      = await get_cfg(bot_id)
    await upd(bot_id, "total_links", cfg.get("total_links", 0) + 1)
    del batch_sessions[key]
    await proc.edit(
        f"<b>🔗 BATCH SEALED — ACCESS KEY READY</b>\n\n"
        f"<blockquote>[ STATUS    : SUCCESS     ]\n"
        f"[ TYPE      : BATCH       ]\n"
        f"[ FILES     : {count:<10}]\n"
        f"[ RANGE     : {s_id} → {e_id} ]\n"
        f"[ ENCRYPTED : BASE64-URL  ]</blockquote>\n\n"
        f"<b>🌐 Batch Access Link:</b>\n<code>{link}</code>\n\n"
        f"<i>📋 Delivers all {count} file(s) at once.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_stats(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg    = await get_cfg(bot_id)
    bn     = cfg.get("bot_name", "KENSHIN FILE NEXUS")
    total  = await count_users(bot_id)
    dt     = cfg.get("auto_delete_time", 0)
    dt_str = f"{dt//60}m {dt%60}s" if dt else "DISABLED"
    await message.reply(
        f"<b>📊 {bn} — GLOBAL ANALYTICS</b>\n\n"
        f"<blockquote>[ USERS       : {total:<10}]\n"
        f"[ ADMINS      : {len(cfg.get('admins',[])):<10}]\n"
        f"[ TOTAL LINKS : {cfg.get('total_links',0):<10}]\n"
        f"[ FSUB CHANS  : {len(cfg.get('fsub_channels',[])):<10}]\n"
        f"[ START IMGS  : {len(cfg.get('start_images',[])):<10}]\n"
        f"[ AUTO-DELETE : {dt_str:<10}]\n"
        f"[ PRE-MSG     : {'CUSTOM' if cfg.get('pre_file_msg') else 'DEFAULT':<10}]\n"
        f"[ STICKER     : {'YES' if cfg.get('start_sticker') else 'NO':<10}]\n"
        f"[ DATABASE    : MONGODB    ]\n"
        f"[ STATUS      : ONLINE     ]</blockquote>\n\n"
        f"<i>⚡ Report — {bn}</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_broadcast(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not message.reply_to_message:
        return await message.reply(
            "<b>⚠️ Reply to a message with /broadcast.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    users = await get_users(bot_id)
    prog  = await message.reply(
        f"<b>📡 BROADCASTING TO {len(users)} NODES...</b>",
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
        f"<b>📡 BROADCAST COMPLETE</b>\n\n"
        f"<blockquote>[ SUCCESS : {ok} nodes    ]\n"
        f"[ FAILED  : {fail} nodes    ]\n"
        f"[ TOTAL   : {len(users)} registered ]</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_add_fsub(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split()
    if len(parts) < 3:
        return await message.reply(
            "<b>⚠️ Usage:</b> /add_fsub &lt;channel_id&gt; &lt;type&gt;\n\n"
            "<b>Types:</b>\n"
            "• <code>public</code> — Public channel (username link)\n"
            "• <code>private</code> — Private channel (fresh invite link)\n"
            "• <code>request</code> — Private channel (join request link)\n\n"
            "<b>Example:</b> <code>/add_fsub -1001234567890 private</code>\n\n"
            "<i>⚠️ Bot must be admin in the channel first!</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        ch_id = int(parts[1])
        raw   = parts[2].lower()
        if raw not in ("public", "private", "request"):
            return await message.reply(
                "<b>❌ Invalid type.</b> Use: <code>public</code>, <code>private</code>, or <code>request</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        ch_type = "private_request" if raw == "request" else raw
        chat    = await client.get_chat(ch_id)
        cfg     = await get_cfg(bot_id)
        if any(c["id"] == ch_id for c in cfg["fsub_channels"]):
            return await message.reply("<b>⚠️ Channel already linked.</b>", parse_mode=enums.ParseMode.HTML)
        cfg["fsub_channels"].append({"id": ch_id, "name": chat.title, "type": ch_type})
        await upd(bot_id, "fsub_channels", cfg["fsub_channels"])
        await message.reply(
            f"<b>✅ SECURITY CHANNEL LINKED</b>\n\n"
            f"<blockquote>[ CHANNEL : {chat.title[:20]:<20}]\n"
            f"[ ID      : {ch_id}      ]\n"
            f"[ TYPE    : {ch_type:<20}]\n"
            f"[ STATUS  : ACTIVE        ]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(
            f"<b>❌ FAILED:</b> <code>{e}</code>\n<i>Ensure bot is admin in that channel.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


async def h_del_fsub(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg  = await get_cfg(bot_id)
    chs  = cfg.get("fsub_channels", [])
    if not chs:
        return await message.reply("<b>⚠️ No security channels configured.</b>", parse_mode=enums.ParseMode.HTML)
    parts = message.text.split()
    if len(parts) < 2:
        lst = "\n".join(f"• <code>{c['id']}</code> — <b>{c['name']}</b> [{c['type']}]" for c in chs)
        return await message.reply(
            f"<b>📋 ACTIVE SECURITY CHANNELS:</b>\n\n{lst}\n\n<b>Usage:</b> /del_fsub &lt;channel_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        ch_id    = int(parts[1])
        new_list = [c for c in chs if c["id"] != ch_id]
        if len(new_list) == len(chs):
            return await message.reply("<b>❌ Channel ID not found.</b>", parse_mode=enums.ParseMode.HTML)
        await upd(bot_id, "fsub_channels", new_list)
        await message.reply(
            f"<b>🚫 SECURITY CHANNEL REMOVED</b>\n\n"
            f"<blockquote>[ ID     : {ch_id}   ]\n"
            f"[ STATUS : UNLINKED ]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"<b>❌ Error:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


async def h_set_bot_name(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        cfg = await get_cfg(bot_id)
        return await message.reply(
            f"<b>Current bot name:</b> <code>{cfg.get('bot_name','KENSHIN FILE NEXUS')}</code>\n\n"
            f"<b>Usage:</b> /set_bot_name &lt;new name&gt;\n\n"
            f"<i>This name appears in all messages, headers, and footers.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    new_name = parts[1].strip()
    await upd(bot_id, "bot_name", new_name)
    await message.reply(
        f"<b>✅ BOT NAME UPDATED</b>\n\n"
        f"<blockquote>New name: <b>{new_name}</b>\n"
        f"Appears in all messages going forward.</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_set_start(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ Usage:</b> /set_start &lt;HTML message&gt;\n\n"
            "<b>Supports:</b> &lt;b&gt; &lt;i&gt; &lt;code&gt; &lt;blockquote&gt;\n"
            "<b>Placeholders:</b> {mention} {first_name} {username} {id} {channel}\n\n"
            "/del_start → restore default",
            parse_mode=enums.ParseMode.HTML,
        )
    await upd(bot_id, "start_message", parts[1])
    await message.reply(
        "<b>✅ WELCOME MESSAGE UPDATED</b>\n<i>⚡ Active on next /start.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_start(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply("<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML)
    await upd(bot_id, "start_message", "")
    await message.reply("<b>✅ Start message reset to default.</b>", parse_mode=enums.ParseMode.HTML)


async def h_add_img(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    t = message.reply_to_message
    if not t or not t.photo:
        return await message.reply(
            "<b>⚠️ Reply to a PHOTO with /add_img</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg    = await get_cfg(bot_id)
    imgs   = cfg.get("start_images", [])
    imgs.append(t.photo.file_id)
    await upd(bot_id, "start_images", imgs)
    await message.reply(
        f"<b>✅ IMAGE ADDED</b>\n\n"
        f"<blockquote>[ IMAGES IN ROTATION : {len(imgs)} ]\n"
        f"[ STATUS             : ACTIVE   ]</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_imgs(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg   = await get_cfg(bot_id)
    count = len(cfg.get("start_images", []))
    await upd(bot_id, "start_images", [])
    await message.reply(
        f"<b>🗑️ IMAGES PURGED</b>\n\n"
        f"<blockquote>[ DELETED : {count} image(s) ]\n"
        f"[ STATUS  : CLEARED     ]</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_set_sticker(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    t = message.reply_to_message
    if not t or not t.sticker:
        return await message.reply(
            "<b>⚠️ Reply to a STICKER with /set_sticker</b>\n\n"
            "<i>Sticker appears AFTER welcome message and auto-deletes in 5 seconds.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await upd(bot_id, "start_sticker", t.sticker.file_id)
    await message.reply(
        "<b>✅ STICKER CONFIGURED</b>\n\n"
        "<i>Appears after welcome message, auto-deletes in 5s.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_sticker(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply("<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML)
    await upd(bot_id, "start_sticker", "")
    await message.reply("<b>✅ Sticker removed.</b>", parse_mode=enums.ParseMode.HTML)


async def h_set_pre_msg(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ Usage:</b> /set_pre_msg &lt;HTML message&gt;\n\n"
            "<i>This message appears BEFORE file delivery.</i>\n\n"
            "<b>Placeholders:</b>\n"
            "<blockquote>{mention} {first_name} {last_name}\n"
            "{username} {id} {channel}</blockquote>\n\n"
            "/del_pre_msg → restore MADARA default",
            parse_mode=enums.ParseMode.HTML,
        )
    await upd(bot_id, "pre_file_msg", parts[1])
    await message.reply(
        "<b>✅ PRE-FILE MESSAGE CONFIGURED</b>\n<i>⚡ Sent before every file delivery.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_pre_msg(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply("<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML)
    await upd(bot_id, "pre_file_msg", "")
    await message.reply("<b>✅ Pre-file message reset to MADARA default.</b>", parse_mode=enums.ParseMode.HTML)


async def h_set_auto_del_msg(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ Usage:</b> /set_auto_del_msg &lt;HTML message&gt;\n\n"
            "<i>Shown after file delivery with timer.</i>\n\n"
            "<b>Placeholders:</b>\n"
            "<blockquote>{time} → e.g. 10m 0s\n"
            "{mention} {first_name}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    await upd(bot_id, "auto_del_msg", parts[1])
    await message.reply(
        "<b>✅ AUTO-DELETE MESSAGE CONFIGURED</b>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_auto_del_msg(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply("<b>🔒 CLEARANCE DENIED</b>", parse_mode=enums.ParseMode.HTML)
    await upd(bot_id, "auto_del_msg", "")
    await message.reply("<b>✅ Auto-delete message reset to default.</b>", parse_mode=enums.ParseMode.HTML)


async def h_set_delete_time(client: Client, message: Message):
    bot_id = client.me.id
    if not await is_admin(bot_id, message.from_user.id):
        return await message.reply(
            "<b>🔒 CLEARANCE DENIED</b>\n<i>Administrator access required.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg   = await get_cfg(bot_id)
    parts = message.text.split()
    if len(parts) < 2:
        cur = cfg.get("auto_delete_time", 0)
        st  = f"{cur//60}m {cur%60}s" if cur else "DISABLED"
        return await message.reply(
            f"<b>⏳ AUTO-PURGE CONFIG</b>\n\n"
            f"Current: <code>{cur}s</code> → <b>{st}</b>\n\n"
            f"<b>Usage:</b> /set_delete_time &lt;seconds&gt;\n"
            f"Use <code>0</code> to disable.\n\n"
            f"<b>Presets:</b>\n"
            f"<blockquote>/set_delete_time 300  → 5 min\n"
            f"/set_delete_time 600  → 10 min\n"
            f"/set_delete_time 1800 → 30 min\n"
            f"/set_delete_time 3600 → 1 hour\n"
            f"/set_delete_time 0    → Disabled</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        secs = int(parts[1])
        if secs < 0:
            return await message.reply("<b>❌ Cannot be negative.</b>", parse_mode=enums.ParseMode.HTML)
        await upd(bot_id, "auto_delete_time", secs)
        st = f"{secs//60}m {secs%60}s" if secs else "DISABLED"
        await message.reply(
            f"<b>✅ AUTO-PURGE TIMER SET</b>\n\n"
            f"<blockquote>[ TIMER : {st:<20}]\n"
            f"[ SECS  : {secs:<20}]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except ValueError:
        await message.reply(
            "<b>❌ Invalid value.</b> Use whole seconds e.g. <code>600</code>",
            parse_mode=enums.ParseMode.HTML,
        )


async def h_add_admin(client: Client, message: Message):
    bot_id = client.me.id
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("<b>🔒 OWNER-ONLY COMMAND.</b>", parse_mode=enums.ParseMode.HTML)
    t     = message.reply_to_message
    parts = message.text.split()
    if t:
        new_id, name = t.from_user.id, t.from_user.first_name
    elif len(parts) > 1:
        try:
            new_id, name = int(parts[1]), f"ID#{parts[1]}"
        except ValueError:
            return await message.reply("<b>❌ Invalid user ID.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        return await message.reply(
            "<b>⚠️ Usage:</b>\n• Reply to user → /add_admin\n• Or: /add_admin &lt;user_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg    = await get_cfg(bot_id)
    admins = cfg.get("admins", [])
    if new_id in admins or new_id == Config.OWNER_ID:
        return await message.reply("<b>⚠️ Already an admin.</b>", parse_mode=enums.ParseMode.HTML)
    admins.append(new_id)
    await upd(bot_id, "admins", admins)
    await message.reply(
        f"<b>✅ ADMIN CLEARANCE GRANTED</b>\n\n"
        f"<blockquote>[ NAME   : {name[:20]:<20}]\n"
        f"[ ID     : {new_id}       ]\n"
        f"[ LEVEL  : ADMINISTRATOR  ]\n"
        f"[ STATUS : AUTHORIZED     ]</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_del_admin(client: Client, message: Message):
    bot_id = client.me.id
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("<b>🔒 OWNER-ONLY COMMAND.</b>", parse_mode=enums.ParseMode.HTML)
    parts = message.text.split()
    cfg   = await get_cfg(bot_id)
    if len(parts) < 2:
        lst = "\n".join(f"• <code>{a}</code>" for a in cfg.get("admins", [])) or "<i>None</i>"
        return await message.reply(
            f"<b>📋 ADMINS:</b>\n\n{lst}\n\n<b>Usage:</b> /del_admin &lt;user_id&gt;",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        rm_id  = int(parts[1])
        if rm_id == Config.OWNER_ID:
            return await message.reply("<b>❌ Cannot revoke owner.</b>", parse_mode=enums.ParseMode.HTML)
        admins = cfg.get("admins", [])
        if rm_id not in admins:
            return await message.reply("<b>❌ User not in admin list.</b>", parse_mode=enums.ParseMode.HTML)
        admins.remove(rm_id)
        await upd(bot_id, "admins", admins)
        await message.reply(
            f"<b>🚫 ADMIN REVOKED</b>\n\n"
            f"<blockquote>[ ID     : {rm_id}        ]\n"
            f"[ STATUS : UNAUTHORIZED ]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"<b>❌ Error:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
#  CLONE SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

async def set_bot_commands(cl: Client):
    try:
        await cl.set_bot_commands([
            BotCommand("start",           "Initialize the system protocol"),
            BotCommand("help",            "Display the central command manual"),
            BotCommand("ping",            "Check system latency and status"),
            BotCommand("cancel",          "Terminate the current operation"),
            BotCommand("genlink",         "Generate encrypted file link (reply to file)"),
            BotCommand("batch",           "Start multi-file batch capture session"),
            BotCommand("process",         "Seal batch and generate access key"),
            BotCommand("stats",           "View global system analytics"),
            BotCommand("broadcast",       "Transmit message to all users"),
            BotCommand("add_admin",       "Authorize a new administrator"),
            BotCommand("del_admin",       "Revoke admin privileges"),
            BotCommand("add_fsub",        "Link a force-subscribe channel"),
            BotCommand("del_fsub",        "Remove a force-subscribe channel"),
            BotCommand("set_bot_name",    "Change the bot name everywhere"),
            BotCommand("set_start",       "Configure the welcome message"),
            BotCommand("del_start",       "Reset welcome message to default"),
            BotCommand("add_img",         "Add image to start rotation (reply to photo)"),
            BotCommand("del_imgs",        "Clear all start images"),
            BotCommand("set_sticker",     "Set greeting sticker (reply to sticker)"),
            BotCommand("del_sticker",     "Remove greeting sticker"),
            BotCommand("set_pre_msg",     "Set message shown before file delivery"),
            BotCommand("del_pre_msg",     "Reset pre-file message to default"),
            BotCommand("set_auto_del_msg","Set custom auto-delete warning message"),
            BotCommand("del_auto_del_msg","Reset auto-delete message to default"),
            BotCommand("set_delete_time", "Configure auto-purge timer in seconds"),
            BotCommand("clone",           "Clone this bot with a new token"),
            BotCommand("list_clones",     "List all active clone bots"),
            BotCommand("stop_clone",      "Stop a specific clone bot"),
        ])
    except Exception as e:
        logger.warning(f"set_bot_commands failed: {e}")


async def h_clone(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("<b>🔒 OWNER-ONLY COMMAND.</b>", parse_mode=enums.ParseMode.HTML)
    bot_id = client.me.id
    clone_pending[Config.OWNER_ID] = bot_id
    await message.reply(
        "<b>🤖 CLONE PROTOCOL — INITIATED</b>\n\n"
        "<blockquote>[ STATUS : AWAITING TOKEN ]\n"
        "[ SECURE : TOKEN INPUT   ]\n"
        "[ OWNER  : VERIFIED      ]</blockquote>\n\n"
        "📨 Send the <b>Bot Token</b> for the new clone.\n"
        "<i>Get it from @BotFather → /newbot</i>\n\n"
        "Send /cancel to abort.",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_clone_token(client: Client, message: Message):
    uid = message.from_user.id
    if clone_pending.get(uid) != client.me.id:
        return  # not this bot's responsibility
    if message.text and message.text.startswith("/"):
        return  # ignore commands

    token = message.text.strip()
    del clone_pending[uid]

    proc = await message.reply(
        "<b>⚙️ CLONING IN PROGRESS...</b>\n<i>Connecting to Telegram servers...</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        bot_num  = token.split(":")[0]
        clone_cl = Client(
            f"clone_{bot_num}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=token,
            in_memory=True,
        )
        setup_handlers(clone_cl)
        await clone_cl.start()
        clone_me = await clone_cl.get_me()

        # Set BotFather commands
        await set_bot_commands(clone_cl)

        # Save to DB
        await _clones_col.update_one(
            {"token": token},
            {"$set": {
                "token":    token,
                "bot_id":   clone_me.id,
                "username": clone_me.username,
            }},
            upsert=True,
        )
        active_clones[clone_me.id] = clone_cl

        await proc.edit(
            f"<b>✅ CLONE ONLINE — SYSTEM ACTIVE</b>\n\n"
            f"<blockquote>[ NAME     : {clone_me.first_name[:20]:<20}]\n"
            f"[ USERNAME : @{clone_me.username:<19}]\n"
            f"[ BOT ID   : {clone_me.id:<20}]\n"
            f"[ STATUS   : ONLINE              ]</blockquote>\n\n"
            f"<i>⚡ Clone is live. Configure it via its own chat.</i>\n"
            f"<i>All settings are independent from this bot.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        logger.info(f"Clone @{clone_me.username} started successfully")

    except Exception as e:
        del_str = str(e)
        await proc.edit(
            f"<b>❌ CLONE FAILED</b>\n\n"
            f"<blockquote>{del_str[:200]}</blockquote>\n\n"
            f"<i>Check the token and try again with /clone</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        logger.error(f"Clone creation failed: {e}")


async def h_list_clones(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("<b>🔒 OWNER-ONLY COMMAND.</b>", parse_mode=enums.ParseMode.HTML)
    if not active_clones:
        return await message.reply(
            "<b>📋 NO ACTIVE CLONES</b>\n<i>Use /clone to create one.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    lines = []
    for bid, cl in active_clones.items():
        lines.append(f"• @{cl.me.username} — <code>{bid}</code>")
    await message.reply(
        f"<b>🤖 ACTIVE CLONES ({len(active_clones)})</b>\n\n"
        + "\n".join(lines) +
        "\n\n<i>Use /stop_clone &lt;bot_id&gt; to stop one.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def h_stop_clone(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("<b>🔒 OWNER-ONLY COMMAND.</b>", parse_mode=enums.ParseMode.HTML)
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply(
            "<b>⚠️ Usage:</b> /stop_clone &lt;bot_id&gt;\n\nUse /list_clones to see IDs.",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        target_id = int(parts[1])
        if target_id not in active_clones:
            return await message.reply(
                f"<b>❌ Clone <code>{target_id}</code> not found in active list.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        cl = active_clones[target_id]
        uname = cl.me.username
        await cl.stop()
        del active_clones[target_id]
        await _clones_col.delete_one({"bot_id": target_id})
        await message.reply(
            f"<b>🛑 CLONE STOPPED</b>\n\n"
            f"<blockquote>[ USERNAME : @{uname:<20}]\n"
            f"[ BOT ID   : {target_id:<20}]\n"
            f"[ STATUS   : OFFLINE             ]</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"<b>❌ Error:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK QUERY
# ══════════════════════════════════════════════════════════════════════════════

async def h_callback(client: Client, cq: CallbackQuery):
    data   = cq.data
    uid    = cq.from_user.id
    user   = cq.from_user
    bot_id = client.me.id

    if data.startswith("verify_"):
        encoded    = data[7:]
        violations = await get_fsub_violations(client, bot_id, uid)
        if violations:
            kb = await build_fsub_kb(client, violations, encoded)
            await cq.answer("⚠️ You haven't joined all required channels!", show_alert=True)
            try:
                await cq.message.edit_reply_markup(kb)
            except Exception:
                pass
            return
        await cq.answer("✅ Verified! Unlocking files...", show_alert=False)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await client.send_message(
            uid,
            "<b>✅ ACCESS GRANTED — INITIATING TRANSFER</b>\n\n"
            "<blockquote>[ SECURITY CHECK : PASSED   ]\n"
            "[ CLEARANCE     : GRANTED  ]\n"
            "[ FILE ACCESS   : UNLOCKED ]</blockquote>\n"
            "<i>Retrieving your files...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await deliver_files(client, user, encoded)
    elif data == "cb_help":
        await cq.answer("📚 Send /help for the full Command Matrix!", show_alert=True)
    elif data == "cb_cmds":
        await cq.answer("⚙️ Send /help to see all commands.", show_alert=True)
    else:
        await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  FILTERS
# ══════════════════════════════════════════════════════════════════════════════

async def _in_batch_check(flt, client, msg):
    try:
        return bool(msg.from_user and (client.me.id, msg.from_user.id) in batch_sessions)
    except Exception:
        return False

async def _clone_token_check(flt, client, msg):
    try:
        return (
            msg.from_user and
            msg.from_user.id == Config.OWNER_ID and
            clone_pending.get(Config.OWNER_ID) == client.me.id and
            bool(msg.text) and
            not msg.text.startswith("/")
        )
    except Exception:
        return False

in_batch_filter    = filters.create(_in_batch_check)
clone_token_filter = filters.create(_clone_token_check)

MEDIA_FILTER = (
    filters.document | filters.video | filters.audio |
    filters.photo | filters.voice | filters.video_note | filters.animation
)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

def setup_handlers(cl: Client):
    """Register all handlers on any client (main or clone)."""
    H = handlers.MessageHandler
    C = handlers.CallbackQueryHandler
    pv = filters.private

    cl.add_handler(H(h_start,           filters.command("start")            & pv))
    cl.add_handler(H(h_help,            filters.command("help")             & pv))
    cl.add_handler(H(h_ping,            filters.command("ping")             & pv))
    cl.add_handler(H(h_cancel,          filters.command("cancel")           & pv))
    cl.add_handler(H(h_genlink,         filters.command("genlink")          & pv))
    cl.add_handler(H(h_batch,           filters.command("batch")            & pv))
    cl.add_handler(H(h_process,         filters.command("process")          & pv))
    cl.add_handler(H(h_stats,           filters.command("stats")            & pv))
    cl.add_handler(H(h_broadcast,       filters.command("broadcast")        & pv))
    cl.add_handler(H(h_add_fsub,        filters.command("add_fsub")         & pv))
    cl.add_handler(H(h_del_fsub,        filters.command("del_fsub")         & pv))
    cl.add_handler(H(h_set_bot_name,    filters.command("set_bot_name")     & pv))
    cl.add_handler(H(h_set_start,       filters.command("set_start")        & pv))
    cl.add_handler(H(h_del_start,       filters.command("del_start")        & pv))
    cl.add_handler(H(h_add_img,         filters.command("add_img")          & pv))
    cl.add_handler(H(h_del_imgs,        filters.command("del_imgs")         & pv))
    cl.add_handler(H(h_set_sticker,     filters.command("set_sticker")      & pv))
    cl.add_handler(H(h_del_sticker,     filters.command("del_sticker")      & pv))
    cl.add_handler(H(h_set_pre_msg,     filters.command("set_pre_msg")      & pv))
    cl.add_handler(H(h_del_pre_msg,     filters.command("del_pre_msg")      & pv))
    cl.add_handler(H(h_set_auto_del_msg,filters.command("set_auto_del_msg") & pv))
    cl.add_handler(H(h_del_auto_del_msg,filters.command("del_auto_del_msg") & pv))
    cl.add_handler(H(h_set_delete_time, filters.command("set_delete_time")  & pv))
    cl.add_handler(H(h_add_admin,       filters.command("add_admin")        & pv))
    cl.add_handler(H(h_del_admin,       filters.command("del_admin")        & pv))
    cl.add_handler(H(h_clone,           filters.command("clone")            & pv))
    cl.add_handler(H(h_list_clones,     filters.command("list_clones")      & pv))
    cl.add_handler(H(h_stop_clone,      filters.command("stop_clone")       & pv))

    # Batch file collector
    cl.add_handler(H(h_collect_batch,   pv & in_batch_filter & MEDIA_FILTER))

    # Clone token receiver (high priority group -1)
    cl.add_handler(H(h_clone_token,     pv & clone_token_filter), group=-1)

    # Callbacks
    cl.add_handler(C(h_callback))


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    # Register handlers THEN start
    setup_handlers(app)
    await app.start()
    await set_bot_commands(app)
    me = app.me

    if not Config.DB_CHANNEL:
        logger.warning("⚠️  DB_CHANNEL not set — file storage will fail!")

    logger.info("=" * 60)
    logger.info("  MADARA UCHIHA FILE BOT — SYSTEM ONLINE")
    logger.info(f"  Bot      : @{me.username}")
    logger.info(f"  Name     : {me.first_name}")
    logger.info(f"  Owner ID : {Config.OWNER_ID}")
    logger.info(f"  DB Chan  : {Config.DB_CHANNEL}")
    logger.info(f"  Database : MongoDB ✓")
    logger.info("=" * 60)

    # Auto-start saved clones
    async for doc in _clones_col.find({}):
        token = doc.get("token", "")
        try:
            num      = token.split(":")[0]
            clone_cl = Client(
                f"clone_{num}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                bot_token=token,
                in_memory=True,
            )
            setup_handlers(clone_cl)
            await clone_cl.start()
            cme = clone_cl.me
            active_clones[cme.id] = clone_cl
            logger.info(f"  Clone    : @{cme.username} — ONLINE")
        except Exception as e:
            logger.error(f"  Clone startup failed [{token[:20]}...]: {e}")

    logger.info("=" * 60)
    await idle()

    # Graceful shutdown
    for clone_cl in active_clones.values():
        try:
            await clone_cl.stop()
        except Exception:
            pass
    await app.stop()
    logger.info("MADARA UCHIHA BOT — SHUTDOWN COMPLETE")

app.run(main())
