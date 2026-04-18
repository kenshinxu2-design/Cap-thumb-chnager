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
from pyrogram import Client, filters, idle
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
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
#  MONGODB DATABASE
# ══════════════════════════════════════════════════════════════════════════════

mongo_client = AsyncIOMotorClient(Config.MONGO_URI)
db_mongo     = mongo_client["kenshin_nexus"]
settings_col = db_mongo["settings"]
users_col    = db_mongo["users"]

DOC_ID = "main"

async def get_settings() -> dict:
    doc = await settings_col.find_one({"_id": DOC_ID})
    if doc:
        return doc
    default = {
        "_id": DOC_ID,
        "admins": [Config.OWNER_ID],
        "fsub_channels": [],
        "start_images": [],
        "start_message": "",
        "start_sticker": "",
        "total_links": 0,
        "auto_delete_time": Config.AUTO_DELETE_TIME,
    }
    await settings_col.insert_one(default)
    return default

async def update_settings(field: str, value):
    await settings_col.update_one({"_id": DOC_ID}, {"$set": {field: value}}, upsert=True)

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

async def build_fsub_markup(client: Client, violations: list, encoded: str) -> InlineKeyboardMarkup:
    buttons = []
    for ch in violations:
        try:
            if ch["type"] == "public":
                chat = await client.get_chat(ch["id"])
                url = f"https://t.me/{chat.username}"
            elif ch["type"] == "private_request":
                inv = await client.create_chat_invite_link(ch["id"], creates_join_request=True)
                url = inv.invite_link
            else:
                inv = await client.create_chat_invite_link(ch["id"])
                url = inv.invite_link
            buttons.append([InlineKeyboardButton(f"📡  Join — {ch['name']}", url=url)])
        except Exception as e:
            logger.error(f"Button gen failed [{ch['id']}]: {e}")
    buttons.append([InlineKeyboardButton("✅  I've Joined — Verify & Unlock", callback_data=f"verify_{encoded}")])
    return InlineKeyboardMarkup(buttons)

async def deliver_files(client: Client, user_id: int, encoded: str):
    cfg = await get_settings()
    delete_time = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
    try:
        decoded = decode(encoded)
    except Exception:
        await client.send_message(user_id, "**❌ CORRUPTED LINK** — Payload invalid or tampered.")
        return

    sent_ids = []

    if decoded.startswith("file_"):
        mid = int(decoded.split("_", 1)[1])
        try:
            m = await client.copy_message(user_id, Config.DB_CHANNEL, mid)
            sent_ids.append(m.id)
        except Exception as e:
            await client.send_message(user_id, f"**❌ RETRIEVAL FAILED:**\n`{e}`")
            return

    elif decoded.startswith("batch_"):
        parts = decoded.split("_")
        s_id, e_id = int(parts[1]), int(parts[2])
        count = e_id - s_id + 1
        notice = await client.send_message(
            user_id,
            f"**⚡ BATCH TRANSFER SEQUENCE**\n\n"
            f"```\n"
            f"[ MODE    : BATCH DELIVERY     ]\n"
            f"[ FILES   : {count:<22}]\n"
            f"[ STATUS  : TRANSMITTING...    ]\n"
            f"```\n"
            f"_Copying files from the nexus..._",
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
        await client.send_message(user_id, "**❌ UNKNOWN LINK FORMAT.**")
        return

    if sent_ids and delete_time > 0:
        mins, secs = divmod(delete_time, 60)
        d = await client.send_message(
            user_id,
            f"**⏳ AUTO-PURGE SEQUENCE ACTIVE**\n\n"
            f"🗑️ Files self-destruct in **{mins}m {secs}s**\n"
            f"💾 _Download & save immediately before they vanish._",
        )
        sent_ids.append(d.id)
        asyncio.create_task(auto_delete(client, user_id, sent_ids, delete_time))

# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    uid = message.from_user.id
    await register_user(uid)
    cfg = await get_settings()

    if len(message.command) > 1:
        encoded = message.command[1]
        violations = await get_fsub_violations(client, uid)
        if violations:
            markup = await build_fsub_markup(client, violations, encoded)
            await message.reply(
                "**🔐 SECURITY PROTOCOL — ACCESS RESTRICTED**\n\n"
                "```\n"
                "[ CLEARANCE CHECK  : FAILED     ]\n"
                "[ FILE ACCESS      : LOCKED     ]\n"
                "[ ACTION REQUIRED  : JOIN BELOW ]\n"
                "```\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ Subscribe to **ALL** channels below to unlock\n"
                "your transmission from the KENSHIN FILE NEXUS.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=markup,
            )
            return
        await deliver_files(client, uid, encoded)
        return

    sticker_msg = None
    if cfg.get("start_sticker"):
        try:
            sticker_msg = await client.send_sticker(message.chat.id, cfg["start_sticker"])
        except Exception:
            pass

    welcome = cfg.get("start_message") or (
        "**⚡ KENSHIN FILE NEXUS — SYSTEM ONLINE ⚡**\n\n"
        "```\n"
        "╔══════════════════════════════╗\n"
        "║   INITIALIZING SYSTEM...     ║\n"
        "║   ENCRYPTION   :  ACTIVE  ✓  ║\n"
        "║   STORAGE      :  SECURED ✓  ║\n"
        "║   FORCE-SUB    :  ENABLED ✓  ║\n"
        "║   STATUS       :  ONLINE  ✓  ║\n"
        "╚══════════════════════════════╝\n"
        "```\n\n"
        "🌑 **Welcome to the Shadow Archive.**\n"
        "Files stored here are encrypted, permanent,\n"
        "and accessible only through verified access links.\n\n"
        "🔗 Use `/help` to access the **Command Matrix**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ _Powered by Kenshin Anime_"
    )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Kenshin Anime", url="https://t.me/KENSHIN_ANIME"),
            InlineKeyboardButton("📚 Help", callback_data="cb_help"),
        ],
        [InlineKeyboardButton("⚙️ Command Matrix", callback_data="cb_cmds")],
    ])

    images = cfg.get("start_images", [])
    if images:
        try:
            await client.send_photo(
                message.chat.id,
                photo=random.choice(images),
                caption=welcome,
                reply_markup=buttons,
            )
        except Exception:
            await message.reply(welcome, reply_markup=buttons)
    else:
        await message.reply(welcome, reply_markup=buttons)

    if sticker_msg:
        asyncio.create_task(_delete_after(sticker_msg, 5))


@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, message: Message):
    await message.reply(
        "**📡 KENSHIN FILE NEXUS — COMMAND MATRIX**\n\n"
        "```\n"
        "[ CENTRAL DATABASE  :  CONNECTED  ]\n"
        "[ AUTHORIZATION     :  GRANTED    ]\n"
        "[ LOADING REGISTRY  :  COMPLETE   ]\n"
        "```\n\n"
        "**━━━ 🌐 USER PROTOCOLS ━━━**\n"
        "`/start` → Initialize the system protocol\n"
        "`/help` → Display this central command manual\n"
        "`/ping` → Check system latency & status\n"
        "`/cancel` → Terminate the current operation\n\n"
        "**━━━ 🔗 LINK GENERATION ━━━**\n"
        "`/genlink` → Generate a singular encrypted file link\n"
        "  ↳ _Reply to any file/media_\n"
        "`/batch` → Initiate a multi-file batch sequence\n"
        "  ↳ _Forward files one by one after this_\n"
        "`/process` → Finalize batch & generate access key\n\n"
        "**━━━ 🛡️ ADMIN MATRIX ━━━**\n"
        "`/stats` → View global system analytics\n"
        "`/broadcast` → Transmit a global frequency message\n"
        "`/add_admin` → Authorize a new system administrator\n"
        "`/del_admin` → Revoke administrative privileges\n\n"
        "**━━━ 📡 SECURITY CHANNELS ━━━**\n"
        "`/add_fsub` → Link a security requirement channel\n"
        "`/del_fsub` → Remove a security requirement channel\n\n"
        "**━━━ 🎨 INTERFACE CONFIG ━━━**\n"
        "`/set_start` → Configure the primary greeting message\n"
        "`/del_start` → Reset greeting to default\n"
        "`/add_img` → Insert a visual asset into start rotation\n"
        "`/del_imgs` → Wipe all visual assets from memory\n"
        "`/set_sticker` → Set the system greeting signature\n"
        "`/del_sticker` → Remove the greeting sticker\n"
        "`/set_delete_time` → Configure auto-purge timer\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ _KENSHIN FILE NEXUS — Encrypted. Secured. Eternal._",
    )


@app.on_message(filters.command("ping") & filters.private)
async def cmd_ping(client: Client, message: Message):
    t = time.monotonic()
    m = await message.reply("**⚡ Scanning core systems...**")
    lat = (time.monotonic() - t) * 1000
    await m.edit(
        f"**🟢 SYSTEM STATUS — FULLY OPERATIONAL**\n\n"
        f"```\n"
        f"[ LATENCY    : {lat:.2f} ms            ]\n"
        f"[ API STATUS : CONNECTED               ]\n"
        f"[ ENCRYPTION : ACTIVE                  ]\n"
        f"[ DATABASE   : MONGODB                 ]\n"
        f"[ UPTIME     : STABLE                  ]\n"
        f"```\n\n"
        f"⚡ _All systems nominal. Nexus is alive._",
    )


@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, message: Message):
    uid = message.from_user.id
    if uid in batch_sessions:
        count = len(batch_sessions[uid])
        del batch_sessions[uid]
        await message.reply(
            f"**🛑 OPERATION TERMINATED**\n\n"
            f"```\n"
            f"[ BATCH SESSION  : CLEARED    ]\n"
            f"[ FILES IN QUEUE : {count:<10}]\n"
            f"[ STATUS         : STANDBY    ]\n"
            f"```\n"
            f"_All queued data purged from memory._",
        )
    else:
        await message.reply(
            "**⚠️ NO ACTIVE OPERATION**\n"
            "_System is already in standby mode._",
        )


@app.on_message(filters.command("genlink") & filters.private)
async def cmd_genlink(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    target = message.reply_to_message
    if not target:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "Reply to a **file/media/document** with `/genlink`.\n\n"
            "**Steps:**\n"
            "1️⃣ Send or forward a file to the bot\n"
            "2️⃣ Reply to that file with `/genlink`\n"
            "3️⃣ Get your shareable encrypted link ⚡",
        )

    proc = await message.reply("**⚙️ Encrypting payload — storing in nexus...**")
    try:
        fwd = await target.forward(Config.DB_CHANNEL)
        link_str = encode(f"file_{fwd.id}")
        me = await client.get_me()
        link = f"https://t.me/{me.username}?start={link_str}"

        cfg = await get_settings()
        await update_settings("total_links", cfg.get("total_links", 0) + 1)

        await proc.edit(
            f"**🔗 ENCRYPTION COMPLETE — LINK FORGED**\n\n"
            f"```\n"
            f"[ STATUS     : SUCCESS              ]\n"
            f"[ TYPE       : SINGLE FILE          ]\n"
            f"[ MSG ID     : {fwd.id:<20}]\n"
            f"[ ENCRYPTED  : BASE64-URL           ]\n"
            f"```\n\n"
            f"**🌐 Shareable Access Link:**\n"
            f"`{link}`\n\n"
            f"📋 _Tap the link above to copy. Share freely._\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ _KENSHIN FILE NEXUS_",
        )
    except Exception as e:
        await proc.edit(f"**❌ OPERATION FAILED:**\n`{e}`")


@app.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    uid = message.from_user.id
    batch_sessions[uid] = []
    await message.reply(
        "**📦 BATCH SEQUENCE — INITIATED**\n\n"
        "```\n"
        "[ MODE      : CAPTURE ACTIVE     ]\n"
        "[ QUEUE     : 0 FILES            ]\n"
        "[ RECEIVER  : LISTENING...       ]\n"
        "```\n\n"
        "**📤 HOW TO USE:**\n"
        "→ Forward or send files **one by one** to the bot\n"
        "→ Each file gets queued into the nexus automatically\n"
        "→ Send `/process` when **all files** are uploaded\n"
        "→ Send `/cancel` to abort the operation\n\n"
        "⚡ _Begin transmission now — the nexus is listening..._",
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
            f"**✅ FILE #{n} QUEUED**\n"
            f"_Stored securely in the nexus._\n\n"
            f"📦 **Queue size:** `{n}` file(s)\n"
            f"💡 Send `/process` when done | `/cancel` to abort.",
        )
    except Exception as e:
        await message.reply(f"**❌ QUEUE ERROR:** `{e}`")


@app.on_message(filters.command("process") & filters.private)
async def cmd_process(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    uid = message.from_user.id
    session = batch_sessions.get(uid)
    if not session:
        return await message.reply(
            "**⚠️ NO ACTIVE BATCH SESSION**\n"
            "_Start one with `/batch` first, then send files._",
        )

    start_id = min(session)
    end_id = max(session)
    count = len(session)
    proc = await message.reply("**⚙️ Sealing batch — generating encrypted access key...**")

    link_str = encode(f"batch_{start_id}_{end_id}")
    me = await client.get_me()
    link = f"https://t.me/{me.username}?start={link_str}"

    cfg = await get_settings()
    await update_settings("total_links", cfg.get("total_links", 0) + 1)
    del batch_sessions[uid]

    await proc.edit(
        f"**🔗 BATCH SEALED — ACCESS KEY GENERATED**\n\n"
        f"```\n"
        f"[ STATUS     : SUCCESS              ]\n"
        f"[ TYPE       : BATCH SEQUENCE       ]\n"
        f"[ FILES      : {count:<20}]\n"
        f"[ MSG RANGE  : {start_id} → {end_id}   ]\n"
        f"[ ENCRYPTED  : BASE64-URL           ]\n"
        f"```\n\n"
        f"**🌐 Batch Access Link:**\n"
        f"`{link}`\n\n"
        f"📋 _Share this link — delivers all {count} file(s) at once._\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ _KENSHIN FILE NEXUS_",
    )


@app.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    cfg = await get_settings()
    total_users = await get_total_users()
    del_time = cfg.get("auto_delete_time", 0)
    del_str = f"{del_time // 60}m {del_time % 60}s" if del_time else "DISABLED"

    await message.reply(
        f"**📊 SYSTEM ANALYTICS — GLOBAL REPORT**\n\n"
        f"```\n"
        f"[ USERS REGISTERED  : {total_users:<10}]\n"
        f"[ ADMINS ACTIVE     : {len(cfg.get('admins', [])):<10}]\n"
        f"[ TOTAL LINKS GEN.  : {cfg.get('total_links', 0):<10}]\n"
        f"[ FSUB CHANNELS     : {len(cfg.get('fsub_channels', [])):<10}]\n"
        f"[ START IMAGES      : {len(cfg.get('start_images', [])):<10}]\n"
        f"[ AUTO-DELETE TIMER : {del_str:<10}]\n"
        f"[ STICKER ACTIVE    : {'YES' if cfg.get('start_sticker') else 'NO':<10}]\n"
        f"[ DATABASE          : MONGODB    ]\n"
        f"[ SYSTEM STATUS     : ONLINE     ]\n"
        f"```\n\n"
        f"⚡ _Report generated — KENSHIN FILE NEXUS_",
    )


@app.on_message(filters.command("broadcast") & filters.private)
async def cmd_broadcast(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    if not message.reply_to_message:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\nReply to any message with `/broadcast`."
        )

    users = await get_all_users()
    prog = await message.reply(
        f"**📡 INITIATING GLOBAL BROADCAST**\n"
        f"_Transmitting to `{len(users)}` registered nodes..._",
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
        f"**📡 BROADCAST — TRANSMISSION COMPLETE**\n\n"
        f"```\n"
        f"[ SUCCESS  : {ok} nodes reached  ]\n"
        f"[ FAILED   : {fail} nodes offline ]\n"
        f"[ TOTAL    : {len(users)} registered    ]\n"
        f"```\n"
        f"⚡ _Global frequency transmission complete._",
    )


@app.on_message(filters.command("add_fsub") & filters.private)
async def cmd_add_fsub(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    parts = message.text.split()
    if len(parts) < 3:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "**Usage:** `/add_fsub <channel_id> <type>`\n\n"
            "**Types:**\n"
            "• `public` → Public channel\n"
            "• `private` → Private channel (invite link)\n"
            "• `request` → Private channel (join request)\n\n"
            "**Example:** `/add_fsub -1001234567890 private`\n\n"
            "⚠️ _Bot must be admin in the channel first!_",
        )

    try:
        ch_id = int(parts[1])
        ch_type_raw = parts[2].lower()
        if ch_type_raw not in ("public", "private", "request"):
            return await message.reply("**❌ Invalid type.** Use: `public`, `private`, or `request`")

        ch_type = "private_request" if ch_type_raw == "request" else ch_type_raw
        chat = await client.get_chat(ch_id)
        ch_name = chat.title

        cfg = await get_settings()
        if any(c["id"] == ch_id for c in cfg["fsub_channels"]):
            return await message.reply("**⚠️ Channel already linked in security protocol.**")

        cfg["fsub_channels"].append({"id": ch_id, "name": ch_name, "type": ch_type})
        await update_settings("fsub_channels", cfg["fsub_channels"])

        await message.reply(
            f"**✅ SECURITY CHANNEL LINKED**\n\n"
            f"```\n"
            f"[ CHANNEL  : {ch_name[:22]:<22}]\n"
            f"[ ID       : {ch_id}          ]\n"
            f"[ TYPE     : {ch_type:<22}    ]\n"
            f"[ STATUS   : ACTIVE            ]\n"
            f"```\n"
            f"⚡ _Users must join this channel to access any file._",
        )
    except Exception as e:
        await message.reply(f"**❌ OPERATION FAILED:**\n`{e}`\n\n_Ensure bot is admin in the channel._")


@app.on_message(filters.command("del_fsub") & filters.private)
async def cmd_del_fsub(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    cfg = await get_settings()
    channels = cfg.get("fsub_channels", [])

    if not channels:
        return await message.reply("**⚠️ No security channels currently configured.**")

    parts = message.text.split()
    if len(parts) < 2:
        ch_list = "\n".join([f"• `{c['id']}` — **{c['name']}** `[{c['type']}]`" for c in channels])
        return await message.reply(
            f"**📋 ACTIVE SECURITY CHANNELS:**\n\n{ch_list}\n\n"
            f"**Usage:** `/del_fsub <channel_id>`",
        )

    try:
        ch_id = int(parts[1])
        new_list = [c for c in channels if c["id"] != ch_id]
        if len(new_list) == len(channels):
            return await message.reply("**❌ Channel ID not found in security registry.**")

        await update_settings("fsub_channels", new_list)
        await message.reply(
            f"**🚫 SECURITY CHANNEL REMOVED**\n\n"
            f"```\n"
            f"[ CHANNEL ID  : {ch_id}   ]\n"
            f"[ STATUS      : UNLINKED  ]\n"
            f"```\n"
            f"⚡ _Channel removed from force-sub protocol._",
        )
    except Exception as e:
        await message.reply(f"**❌ Error:** `{e}`")


@app.on_message(filters.command("set_start") & filters.private)
async def cmd_set_start(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "**Usage:** `/set_start <your custom welcome message>`\n\n"
            "Supports **bold**, _italic_, `code` formatting.\n"
            "Use `/del_start` to restore the default message.",
        )

    await update_settings("start_message", parts[1])
    await message.reply(
        "**✅ PRIMARY GREETING RECONFIGURED**\n"
        "⚡ _New welcome message will appear on next /start._",
    )


@app.on_message(filters.command("del_start") & filters.private)
async def cmd_del_start(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**")
    await update_settings("start_message", "")
    await message.reply("**✅ Start message reset to default.**")


@app.on_message(filters.command("add_img") & filters.private)
async def cmd_add_img(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    target = message.reply_to_message
    if not target or not target.photo:
        return await message.reply(
            "**⚠️ Reply to a PHOTO with `/add_img` to add it to the start image rotation.**"
        )

    cfg = await get_settings()
    images = cfg.get("start_images", [])
    images.append(target.photo.file_id)
    await update_settings("start_images", images)

    await message.reply(
        f"**✅ VISUAL ASSET INTEGRATED**\n\n"
        f"```\n"
        f"[ IMAGES IN ROTATION : {len(images)} ]\n"
        f"[ STATUS             : ACTIVE   ]\n"
        f"```\n"
        f"⚡ _Bot will randomly select from available images on each /start._",
    )


@app.on_message(filters.command("del_imgs") & filters.private)
async def cmd_del_imgs(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    cfg = await get_settings()
    count = len(cfg.get("start_images", []))
    await update_settings("start_images", [])
    await message.reply(
        f"**🗑️ VISUAL MEMORY PURGED**\n\n"
        f"```\n"
        f"[ DELETED : {count} image(s) wiped  ]\n"
        f"[ STATUS  : MEMORY CLEARED         ]\n"
        f"```",
    )


@app.on_message(filters.command("set_sticker") & filters.private)
async def cmd_set_sticker(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    target = message.reply_to_message
    if not target or not target.sticker:
        return await message.reply(
            "**⚠️ Reply to a STICKER with `/set_sticker`.**\n\n"
            "_The sticker will appear on each /start and auto-delete after 5 seconds._",
        )

    await update_settings("start_sticker", target.sticker.file_id)
    await message.reply(
        "**✅ GREETING SIGNATURE CONFIGURED**\n\n"
        "The sticker will appear on every `/start`\n"
        "and automatically self-destruct after **5 seconds**.\n"
        "⚡ _Signature is now active._",
    )


@app.on_message(filters.command("del_sticker") & filters.private)
async def cmd_del_sticker(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**")
    await update_settings("start_sticker", "")
    await message.reply("**✅ Greeting sticker removed.**")


@app.on_message(filters.command("add_admin") & filters.private)
async def cmd_add_admin(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("**🔒 OWNER-ONLY COMMAND.**")

    target = message.reply_to_message
    parts = message.text.split()

    if target:
        new_id = target.from_user.id
        name = target.from_user.first_name
    elif len(parts) > 1:
        try:
            new_id = int(parts[1])
            name = f"ID#{new_id}"
        except ValueError:
            return await message.reply("**❌ Invalid user ID.**")
    else:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "• Reply to a user's message → `/add_admin`\n"
            "• Or: `/add_admin <user_id>`",
        )

    cfg = await get_settings()
    if new_id == Config.OWNER_ID or new_id in cfg.get("admins", []):
        return await message.reply("**⚠️ User already has admin clearance.**")

    admins = cfg.get("admins", [])
    admins.append(new_id)
    await update_settings("admins", admins)

    await message.reply(
        f"**✅ ADMIN CLEARANCE GRANTED**\n\n"
        f"```\n"
        f"[ OPERATOR  : {name[:22]:<22}]\n"
        f"[ USER ID   : {new_id}         ]\n"
        f"[ LEVEL     : ADMINISTRATOR    ]\n"
        f"[ STATUS    : AUTHORIZED       ]\n"
        f"```\n"
        f"⚡ _Access to admin command matrix has been granted._",
    )


@app.on_message(filters.command("del_admin") & filters.private)
async def cmd_del_admin(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("**🔒 OWNER-ONLY COMMAND.**")

    parts = message.text.split()
    if len(parts) < 2:
        cfg = await get_settings()
        admins = cfg.get("admins", [])
        admin_list = "\n".join([f"• `{a}`" for a in admins]) or "_None_"
        return await message.reply(
            f"**📋 ACTIVE ADMINISTRATORS:**\n\n{admin_list}\n\n"
            f"**Usage:** `/del_admin <user_id>`",
        )

    try:
        rm_id = int(parts[1])
        if rm_id == Config.OWNER_ID:
            return await message.reply("**❌ Cannot revoke owner clearance.**")

        cfg = await get_settings()
        admins = cfg.get("admins", [])
        if rm_id not in admins:
            return await message.reply("**❌ User is not in the admin registry.**")

        admins.remove(rm_id)
        await update_settings("admins", admins)

        await message.reply(
            f"**🚫 ADMIN CLEARANCE REVOKED**\n\n"
            f"```\n"
            f"[ USER ID  : {rm_id}        ]\n"
            f"[ STATUS   : UNAUTHORIZED   ]\n"
            f"```\n"
            f"⚡ _Access to admin matrix has been terminated._",
        )
    except Exception as e:
        await message.reply(f"**❌ Error:** `{e}`")


@app.on_message(filters.command("set_delete_time") & filters.private)
async def cmd_set_delete_time(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**\n_Administrator access required._")

    cfg = await get_settings()
    parts = message.text.split()

    if len(parts) < 2:
        current = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
        status = f"{current // 60}m {current % 60}s" if current else "DISABLED"
        return await message.reply(
            f"**⏳ AUTO-PURGE CONFIGURATION**\n\n"
            f"Current timer: `{current}s` → **{status}**\n\n"
            f"**Usage:** `/set_delete_time <seconds>`\n"
            f"Set `0` to **disable** auto-delete.\n\n"
            f"**Presets:**\n"
            f"• `/set_delete_time 300` → 5 min\n"
            f"• `/set_delete_time 600` → 10 min\n"
            f"• `/set_delete_time 1800` → 30 min\n"
            f"• `/set_delete_time 3600` → 1 hour\n"
            f"• `/set_delete_time 0` → Disabled",
        )

    try:
        secs = int(parts[1])
        if secs < 0:
            return await message.reply("**❌ Value cannot be negative.**")
        await update_settings("auto_delete_time", secs)
        status = f"{secs // 60}m {secs % 60}s" if secs else "DISABLED"
        await message.reply(
            f"**✅ AUTO-PURGE TIMER UPDATED**\n\n"
            f"```\n"
            f"[ TIMER   : {status:<28}]\n"
            f"[ SECONDS : {secs:<28}]\n"
            f"```\n"
            f"⚡ _Files will auto-delete after delivery._",
        )
    except ValueError:
        await message.reply("**❌ Invalid value.** Provide time in whole seconds (e.g. `600`).")


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query()
async def on_callback(client: Client, cq: CallbackQuery):
    data = cq.data
    uid = cq.from_user.id

    if data.startswith("verify_"):
        encoded = data[7:]
        violations = await get_fsub_violations(client, uid)

        if violations:
            markup = await build_fsub_markup(client, violations, encoded)
            await cq.answer("⚠️ You haven't joined all required channels yet!", show_alert=True)
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
            "**✅ ACCESS GRANTED — INITIATING TRANSFER**\n\n"
            "```\n"
            "[ SECURITY CHECK  : PASSED    ]\n"
            "[ CLEARANCE       : GRANTED   ]\n"
            "[ FILE ACCESS     : UNLOCKED  ]\n"
            "```\n"
            "_Retrieving your files from the nexus..._",
        )
        await deliver_files(client, uid, encoded)

    elif data == "cb_help":
        await cq.answer("📚 Send /help for the full Command Matrix!", show_alert=True)
    elif data == "cb_cmds":
        await cq.answer("⚙️ Send /help to see all available commands.", show_alert=True)
    else:
        await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT  — app.run() fixes the Railway/asyncio issue
# ══════════════════════════════════════════════════════════════════════════════

async def startup():
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
    logger.info("=" * 54)

app.run(startup())
