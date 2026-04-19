# ╔══════════════════════════════════════════════════════════════╗
# ║          KENSHIN FILE NEXUS — FILE STORE BOT                ║
# ║          by @KENSHIN_ANIME  |  Powered by Pyrofork          ║
# ╚══════════════════════════════════════════════════════════════╝

import asyncio
import base64
import json
import logging
import os
import random
import time

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
#  DATABASE  (JSON — lightweight, no external dependency)
# ══════════════════════════════════════════════════════════════════════════════

DB_FILE = "data.json"

def _default_db() -> dict:
    return {
        "admins": [Config.OWNER_ID],
        "fsub_channels": [],          # [{id, name, type}]
        "start_images": [],           # [file_id, ...]
        "start_message": "",
        "start_sticker": "",
        "users": [],
        "total_links": 0,
        "auto_delete_time": Config.AUTO_DELETE_TIME,
    }

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    db = _default_db()
    save_db(db)
    return db

def save_db(data: dict):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# In-memory batch sessions  {user_id: [stored_msg_ids]}
batch_sessions: dict[int, list[int]] = {}


def encode(s: str) -> str:
    """Base64-URL encode a string (no padding)."""
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def decode(s: str) -> str:
    """Decode a Base64-URL string (re-add padding)."""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode()).decode()


def is_admin(uid: int) -> bool:
    db = load_db()
    return uid == Config.OWNER_ID or uid in db.get("admins", [])


async def auto_delete(client: Client, chat_id: int, msg_ids: list[int], delay: int):
    """Delete messages after `delay` seconds."""
    await asyncio.sleep(delay)
    for mid in msg_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass


async def get_fsub_violations(client: Client, user_id: int) -> list[dict]:
    """Return list of channels the user has NOT joined."""
    db = load_db()
    violations = []
    for ch in db.get("fsub_channels", []):
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
    client: Client, violations: list[dict], encoded: str
) -> InlineKeyboardMarkup:
    """Build inline keyboard with join buttons + verify button."""
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
            else:  # private — normal invite link
                inv = await client.create_chat_invite_link(ch["id"])
                url = inv.invite_link
            buttons.append([InlineKeyboardButton(f"📡  Join — {ch['name']}", url=url)])
        except Exception as e:
            logger.error(f"Button gen failed [{ch['id']}]: {e}")

    buttons.append(
        [InlineKeyboardButton("✅  I've Joined — Verify & Unlock", callback_data=f"verify_{encoded}")]
    )
    return InlineKeyboardMarkup(buttons)


async def deliver_files(client: Client, user_id: int, encoded: str):
    """Decode link and deliver file(s) to user."""
    db = load_db()
    delete_time = db.get("auto_delete_time", Config.AUTO_DELETE_TIME)

    try:
        decoded = decode(encoded)
    except Exception:
        await client.send_message(user_id, "**❌ CORRUPTED LINK** — Payload invalid or tampered.")
        return

    sent_ids: list[int] = []

    # ── Single file ──────────────────────────────────────────────────────────
    if decoded.startswith("file_"):
        mid = int(decoded.split("_", 1)[1])
        try:
            m = await client.copy_message(user_id, Config.DB_CHANNEL, mid)
            sent_ids.append(m.id)
        except Exception as e:
            await client.send_message(user_id, f"**❌ RETRIEVAL FAILED:**\n`{e}`")
            return

    # ── Batch ────────────────────────────────────────────────────────────────
    elif decoded.startswith("batch_"):
        parts = decoded.split("_")   # ["batch", "start", "end"]
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

    # ── Auto-delete notice ───────────────────────────────────────────────────
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

# ── /start ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    db = load_db()
    uid = message.from_user.id

    # Register user
    if uid not in db["users"]:
        db["users"].append(uid)
        save_db(db)

    # ── Deep-link access ─────────────────────────────────────────────────────
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

    # ── Normal /start — show greeting ────────────────────────────────────────
    # 1. Sticker (auto-deletes after welcome)
    sticker_msg = None
    if db.get("start_sticker"):
        try:
            sticker_msg = await client.send_sticker(message.chat.id, db["start_sticker"])
        except Exception:
            pass

    # 2. Welcome text
    welcome = db.get("start_message") or (
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

    # 3. Send with random start image (if any)
    images = db.get("start_images", [])
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

    # 4. Auto-delete sticker after 5 s
    if sticker_msg:
        asyncio.create_task(_delete_after(sticker_msg, 5))


async def _delete_after(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


# ── /help ─────────────────────────────────────────────────────────────────────

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
        "`/add_img` → Insert a visual asset into start rotation\n"
        "`/del_imgs` → Wipe all visual assets from memory\n"
        "`/set_sticker` → Set the system greeting signature\n"
        "`/set_delete_time` → Configure auto-purge timer\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ _KENSHIN FILE NEXUS — Encrypted. Secured. Eternal._",
    )


# ── /ping ─────────────────────────────────────────────────────────────────────

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
        f"[ CORE NODE  : ONLINE                  ]\n"
        f"[ UPTIME     : STABLE                  ]\n"
        f"```\n\n"
        f"⚡ _All systems nominal. Nexus is alive._",
    )


# ── /cancel ───────────────────────────────────────────────────────────────────

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
            f"_All queued data purged from memory. System on standby._",
        )
    else:
        await message.reply(
            "**⚠️ NO ACTIVE OPERATION**\n"
            "_System is already in standby mode._\n"
            "Start a batch session with `/batch` first.",
        )


# ── /genlink ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("genlink") & filters.private)
async def cmd_genlink(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

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

        db = load_db()
        db["total_links"] += 1
        save_db(db)

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


# ── /batch ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

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


# ── Batch file collector (media only) ─────────────────────────────────────────

def _in_batch(_, __, msg: Message) -> bool:
    return bool(msg.from_user and msg.from_user.id in batch_sessions)

in_batch = filters.create(_in_batch)

@app.on_message(
    filters.private
    & in_batch
    & (
        filters.document
        | filters.video
        | filters.audio
        | filters.photo
        | filters.voice
        | filters.video_note
        | filters.animation
    )
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


# ── /process ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("process") & filters.private)
async def cmd_process(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

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

    db = load_db()
    db["total_links"] += 1
    save_db(db)

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


# ── /stats ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    db = load_db()
    del_time = db.get("auto_delete_time", 0)
    del_str = f"{del_time // 60}m {del_time % 60}s" if del_time else "DISABLED"

    await message.reply(
        f"**📊 SYSTEM ANALYTICS — GLOBAL REPORT**\n\n"
        f"```\n"
        f"[ USERS REGISTERED  : {len(db.get('users', [])):<10}]\n"
        f"[ ADMINS ACTIVE     : {len(db.get('admins', [])):<10}]\n"
        f"[ TOTAL LINKS GEN.  : {db.get('total_links', 0):<10}]\n"
        f"[ FSUB CHANNELS     : {len(db.get('fsub_channels', [])):<10}]\n"
        f"[ START IMAGES      : {len(db.get('start_images', [])):<10}]\n"
        f"[ AUTO-DELETE TIMER : {del_str:<10}]\n"
        f"[ STICKER ACTIVE    : {'YES' if db.get('start_sticker') else 'NO':<10}]\n"
        f"[ SYSTEM STATUS     : ONLINE    ]\n"
        f"```\n\n"
        f"⚡ _Report generated — KENSHIN FILE NEXUS_",
    )


# ── /broadcast ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("broadcast") & filters.private)
async def cmd_broadcast(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    if not message.reply_to_message:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "Reply to any message with `/broadcast` to\n"
            "transmit it globally to all registered nodes.",
        )

    db = load_db()
    users = db.get("users", [])
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


# ── /add_fsub ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("add_fsub") & filters.private)
async def cmd_add_fsub(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    parts = message.text.split()
    if len(parts) < 3:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "**Usage:**\n"
            "`/add_fsub <channel_id> <type>`\n\n"
            "**Channel Types:**\n"
            "• `public` → Public channel _(join via username)_\n"
            "• `private` → Private channel _(fresh invite link)_\n"
            "• `request` → Private channel _(join request link)_\n\n"
            "**Example:**\n"
            "`/add_fsub -1001234567890 private`\n\n"
            "⚠️ _Bot must be admin in the channel first!_",
        )

    try:
        ch_id = int(parts[1])
        ch_type_raw = parts[2].lower()

        if ch_type_raw not in ("public", "private", "request"):
            return await message.reply(
                "**❌ Invalid type.**\nUse: `public`, `private`, or `request`"
            )

        ch_type = "private_request" if ch_type_raw == "request" else ch_type_raw
        chat = await client.get_chat(ch_id)
        ch_name = chat.title

        db = load_db()
        if any(c["id"] == ch_id for c in db["fsub_channels"]):
            return await message.reply(
                "**⚠️ Channel already linked in security protocol.**"
            )

        db["fsub_channels"].append({"id": ch_id, "name": ch_name, "type": ch_type})
        save_db(db)

        await message.reply(
            f"**✅ SECURITY CHANNEL LINKED**\n\n"
            f"```\n"
            f"[ CHANNEL  : {ch_name[:22]:<22}]\n"
            f"[ ID       : {ch_id}        ]\n"
            f"[ TYPE     : {ch_type:<22}  ]\n"
            f"[ STATUS   : ACTIVE          ]\n"
            f"```\n"
            f"⚡ _Users must join this channel to access any file._",
        )
    except Exception as e:
        await message.reply(
            f"**❌ OPERATION FAILED:**\n`{e}`\n\n"
            "_Ensure the bot is an admin in the channel with invite link permission._",
        )


# ── /del_fsub ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("del_fsub") & filters.private)
async def cmd_del_fsub(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    db = load_db()
    channels = db.get("fsub_channels", [])

    if not channels:
        return await message.reply(
            "**⚠️ No security channels currently configured.**"
        )

    parts = message.text.split()
    if len(parts) < 2:
        ch_list = "\n".join([f"• `{c['id']}` — **{c['name']}** `[{c['type']}]`" for c in channels])
        return await message.reply(
            f"**📋 ACTIVE SECURITY CHANNELS:**\n\n{ch_list}\n\n"
            f"**Usage:** `/del_fsub <channel_id>`",
        )

    try:
        ch_id = int(parts[1])
        before = len(channels)
        db["fsub_channels"] = [c for c in channels if c["id"] != ch_id]

        if len(db["fsub_channels"]) == before:
            return await message.reply(
                "**❌ Channel ID not found in security registry.**"
            )

        save_db(db)
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


# ── /set_start ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("set_start") & filters.private)
async def cmd_set_start(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply(
            "**⚠️ SYNTAX ERROR**\n\n"
            "**Usage:** `/set_start <your custom welcome message>`\n\n"
            "Supports **bold**, _italic_, `code`, and all Telegram markdown.\n"
            "Use `/del_start` to restore the default message.",
        )

    db = load_db()
    db["start_message"] = parts[1]
    save_db(db)
    await message.reply(
        "**✅ PRIMARY GREETING RECONFIGURED**\n"
        "⚡ _New welcome message will appear on next /start._",
    )


@app.on_message(filters.command("del_start") & filters.private)
async def cmd_del_start(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**")
    db = load_db()
    db["start_message"] = ""
    save_db(db)
    await message.reply("**✅ Start message reset to default.**")


# ── /add_img ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("add_img") & filters.private)
async def cmd_add_img(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    target = message.reply_to_message
    if not target or not target.photo:
        return await message.reply(
            "**⚠️ Reply to a PHOTO with `/add_img` to add it to the start image rotation.**"
        )

    db = load_db()
    db.setdefault("start_images", []).append(target.photo.file_id)
    save_db(db)

    await message.reply(
        f"**✅ VISUAL ASSET INTEGRATED**\n\n"
        f"```\n"
        f"[ IMAGES IN ROTATION : {len(db['start_images'])} ]\n"
        f"[ STATUS             : ACTIVE   ]\n"
        f"```\n"
        f"⚡ _Bot will randomly select from available images on each /start._",
    )


# ── /del_imgs ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("del_imgs") & filters.private)
async def cmd_del_imgs(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    db = load_db()
    count = len(db.get("start_images", []))
    db["start_images"] = []
    save_db(db)

    await message.reply(
        f"**🗑️ VISUAL MEMORY PURGED**\n\n"
        f"```\n"
        f"[ DELETED : {count} image(s) wiped  ]\n"
        f"[ STATUS  : MEMORY CLEARED         ]\n"
        f"```\n"
        f"_Bot will display text-only start message going forward._",
    )


# ── /set_sticker ──────────────────────────────────────────────────────────────

@app.on_message(filters.command("set_sticker") & filters.private)
async def cmd_set_sticker(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    target = message.reply_to_message
    if not target or not target.sticker:
        return await message.reply(
            "**⚠️ Reply to a STICKER with `/set_sticker` to set it as the greeting signature.**\n\n"
            "_The sticker will appear on each /start and auto-delete after the welcome message._",
        )

    db = load_db()
    db["start_sticker"] = target.sticker.file_id
    save_db(db)

    await message.reply(
        "**✅ GREETING SIGNATURE CONFIGURED**\n\n"
        "The sticker will appear on every `/start`\n"
        "and automatically self-destruct after **5 seconds**.\n"
        "⚡ _Signature is now active._",
    )


@app.on_message(filters.command("del_sticker") & filters.private)
async def cmd_del_sticker(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply("**🔒 CLEARANCE DENIED**")
    db = load_db()
    db["start_sticker"] = ""
    save_db(db)
    await message.reply("**✅ Greeting sticker removed.**")


# ── /add_admin ────────────────────────────────────────────────────────────────

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
            "**Methods:**\n"
            "• Reply to a user's message → `/add_admin`\n"
            "• Or: `/add_admin <user_id>`",
        )

    db = load_db()
    if new_id == Config.OWNER_ID or new_id in db.get("admins", []):
        return await message.reply("**⚠️ User already has admin clearance.**")

    db["admins"].append(new_id)
    save_db(db)

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


# ── /del_admin ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("del_admin") & filters.private)
async def cmd_del_admin(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply("**🔒 OWNER-ONLY COMMAND.**")

    parts = message.text.split()
    if len(parts) < 2:
        db = load_db()
        admins = db.get("admins", [])
        admin_list = "\n".join([f"• `{a}`" for a in admins]) or "_None_"
        return await message.reply(
            f"**📋 ACTIVE ADMINISTRATORS:**\n\n{admin_list}\n\n"
            f"**Usage:** `/del_admin <user_id>`",
        )

    try:
        rm_id = int(parts[1])
        if rm_id == Config.OWNER_ID:
            return await message.reply("**❌ Cannot revoke owner clearance.**")

        db = load_db()
        if rm_id not in db.get("admins", []):
            return await message.reply("**❌ User is not in the admin registry.**")

        db["admins"].remove(rm_id)
        save_db(db)

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


# ── /set_delete_time ──────────────────────────────────────────────────────────

@app.on_message(filters.command("set_delete_time") & filters.private)
async def cmd_set_delete_time(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply(
            "**🔒 CLEARANCE DENIED**\n_Administrator access required._"
        )

    db = load_db()
    parts = message.text.split()

    if len(parts) < 2:
        current = db.get("auto_delete_time", Config.AUTO_DELETE_TIME)
        status = f"{current // 60}m {current % 60}s" if current else "DISABLED"
        return await message.reply(
            f"**⏳ AUTO-PURGE CONFIGURATION**\n\n"
            f"Current timer: `{current}s` → **{status}**\n\n"
            f"**Usage:** `/set_delete_time <seconds>`\n"
            f"Set `0` to **disable** auto-delete.\n\n"
            f"**Quick Presets:**\n"
            f"• `/set_delete_time 300` → 5 minutes\n"
            f"• `/set_delete_time 600` → 10 minutes\n"
            f"• `/set_delete_time 1800` → 30 minutes\n"
            f"• `/set_delete_time 3600` → 1 hour\n"
            f"• `/set_delete_time 0` → Disabled (files stay forever)",
        )

    try:
        secs = int(parts[1])
        if secs < 0:
            return await message.reply("**❌ Value cannot be negative.**")

        db["auto_delete_time"] = secs
        save_db(db)

        status = f"{secs // 60}m {secs % 60}s" if secs else "DISABLED"
        await message.reply(
            f"**✅ AUTO-PURGE TIMER UPDATED**\n\n"
            f"```\n"
            f"[ TIMER    : {status:<28}]\n"
            f"[ SECONDS  : {secs:<28}]\n"
            f"[ STATUS   : CONFIGURED              ]\n"
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

    # ── Verify & unlock ──────────────────────────────────────────────────────
    if data.startswith("verify_"):
        encoded = data[7:]
        violations = await get_fsub_violations(client, uid)

        if violations:
            # Re-generate fresh invite links and show updated markup
            markup = await build_fsub_markup(client, violations, encoded)
            await cq.answer(
                "⚠️ You haven't joined all required channels yet!",
                show_alert=True,
            )
            # Update buttons with fresh links
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

    # ── Help / cmd popups ────────────────────────────────────────────────────
    elif data == "cb_help":
        await cq.answer(
            "📚 Send /help for the full Command Matrix!", show_alert=True
        )
    elif data == "cb_cmds":
        await cq.answer(
            "⚙️ Send /help to see all available commands.", show_alert=True
        )
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
    logger.info("  ██╗  ██╗███████╗███╗   ██╗███████╗██╗  ██╗██╗███╗  ")
    logger.info("  KENSHIN FILE NEXUS — SYSTEM ONLINE                  ")
    logger.info(f"  Bot      :  @{me.username}")
    logger.info(f"  Name     :  {me.first_name}")
    logger.info(f"  Owner ID :  {Config.OWNER_ID}")
    logger.info(f"  DB Chan  :  {Config.DB_CHANNEL}")
    logger.info("=" * 54)

    await idle()
    await app.stop()
    logger.info("KENSHIN FILE NEXUS — SHUTDOWN COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
