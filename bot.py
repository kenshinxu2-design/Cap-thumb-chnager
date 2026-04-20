# ╔══════════════════════════════════════════════════════════╗
# ║   MADARA UCHIHA FILE BOT  •  @KENSHIN_ANIME             ║
# ║   Pyrofork • MongoDB • Multi-Clone • All Features        ║
# ╚══════════════════════════════════════════════════════════╝

import asyncio, base64, logging, random, time
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, enums, filters, handlers, idle
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import (
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat,
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User,
)
from config import Config

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("MadaraUchiha")

# ── Main client ───────────────────────────────────────────────────────────────
app = Client(
    "madara_main",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

# ══════════════════════════════════════════════════════════════════════════════
#  MONGODB
# ══════════════════════════════════════════════════════════════════════════════
_mc       = AsyncIOMotorClient(Config.MONGO_URI)
_db       = _mc["madara_bot"]
_sets     = _db["settings"]
_users    = _db["users"]
_clones   = _db["clones"]
_batches  = _db["batches"]

def _sid(bot_id: int) -> str:
    return f"bot_{bot_id}"

async def get_cfg(bot_id: int) -> dict:
    doc = await _sets.find_one({"_id": _sid(bot_id)})
    if doc:
        return doc
    d = {
        "_id": _sid(bot_id),
        "bot_name":        "KENSHIN FILE NEXUS",
        "admins":          [Config.OWNER_ID],
        "fsub_channels":   [],
        "start_images":    [],
        "start_message":   "",
        "start_sticker":   "",
        "pre_file_msg":    "",
        "auto_del_msg":    "",
        "total_links":     0,
        "fsub_message":    "",
        "auto_delete_time": Config.AUTO_DELETE_TIME,
    }
    await _sets.insert_one(d)
    return d

async def s(bot_id: int, field: str, val):
    await _sets.update_one({"_id": _sid(bot_id)}, {"$set": {field: val}}, upsert=True)

async def reg(bot_id: int, uid: int):
    await _users.update_one({"_id": f"{bot_id}_{uid}"},
                            {"$set": {"bot_id": bot_id, "user_id": uid}}, upsert=True)

async def get_users(bot_id: int) -> list:
    return [d["user_id"] async for d in _users.find({"bot_id": bot_id}, {"user_id": 1})]

async def cnt_users(bot_id: int) -> int:
    return await _users.count_documents({"bot_id": bot_id})

# ══════════════════════════════════════════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════════════════════════════════════════
batch_sessions: dict[tuple, list] = {}   # (bot_id, user_id) → [msg_ids]
clone_pending:  dict[int, int]    = {}   # owner_id → bot_id waiting for token
active_clones:  dict[int, Client] = {}   # bot_id → Client

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def enc(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def dec(s: str) -> str:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode()).decode()

async def is_admin(bot_id: int, uid: int) -> bool:
    if uid == Config.OWNER_ID: return True
    cfg = await get_cfg(bot_id)
    return uid in cfg.get("admins", [])

def bid(client: Client) -> int:
    return client.me.id

def fill(text: str, user: User, extra: dict = None) -> str:
    if not text: return text
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    r = (text
         .replace("{mention}",    mention)
         .replace("{first_name}", user.first_name or "")
         .replace("{last_name}",  user.last_name  or "")
         .replace("{username}",   f"@{user.username}" if user.username else user.first_name)
         .replace("{id}",         str(user.id))
         .replace("{channel}",    "@KENSHIN_ANIME"))
    if extra:
        for k, v in extra.items():
            r = r.replace(f"{{{k}}}", str(v))
    return r

H = enums.ParseMode.HTML

async def reply(message: Message, text: str, **kw):
    return await message.reply(text, parse_mode=H, **kw)

async def send(client: Client, chat_id: int, text: str, **kw):
    return await client.send_message(chat_id, text, parse_mode=H, **kw)

async def _del(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

async def auto_del(client: Client, chat_id: int, ids: list, delay: int):
    await asyncio.sleep(delay)
    for mid in ids:
        try: await client.delete_messages(chat_id, mid)
        except: pass

# ══════════════════════════════════════════════════════════════════════════════
#  DEFAULT MESSAGES
# ══════════════════════════════════════════════════════════════════════════════
def DEF_START(bn: str) -> str:
    return (
        f"<b>⚡ {bn} — SYSTEM ONLINE ⚡</b>\n\n"
        f"<blockquote>╔══════════════════════════╗\n"
        f"║  ENCRYPTION  : ACTIVE ✓  ║\n"
        f"║  STORAGE     : SECURED ✓ ║\n"
        f"║  FORCE-SUB   : ENABLED ✓ ║\n"
        f"║  STATUS      : ONLINE ✓  ║\n"
        f"╚══════════════════════════╝</blockquote>\n\n"
        f"🌑 <b>Welcome to the Shadow Archive.</b>\n"
        f"Files accessible only via verified encrypted links.\n\n"
        f"Use /help to see available commands.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⚡ @KENSHIN_ANIME</i>"
    )

DEF_PRE = (
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

DEF_AUTODEL = (
    "<b>⏳ AUTO-PURGE ACTIVE</b>\n\n"
    "<blockquote>🗑️ Files vanish in <b>{time}</b>\n"
    "💾 Save them immediately.</blockquote>"
)

async def get_fsub_msg(bot_id: int) -> str:
    cfg = await get_cfg(bot_id)
    custom = cfg.get("fsub_message", "")
    if custom:
        return custom
    return (
        "<b>🔐 ACCESS RESTRICTED</b>\n\n"
        "<blockquote>[ CLEARANCE : FAILED     ]\n"
        "[ FILES     : LOCKED     ]\n"
        "[ ACTION    : JOIN BELOW ]</blockquote>\n\n"
        "⚠️ Join <b>ALL</b> channels below to unlock your files."
    )

# ══════════════════════════════════════════════════════════════════════════════
#  FSUB
# ══════════════════════════════════════════════════════════════════════════════
async def check_fsub(client: Client, bot_id: int, uid: int) -> list:
    cfg = await get_cfg(bot_id)
    out = []
    for ch in cfg.get("fsub_channels", []):
        try:
            m = await client.get_chat_member(ch["id"], uid)
            if m.status.value in ("left", "kicked", "banned"):
                out.append(ch)
        except UserNotParticipant:
            out.append(ch)
        except Exception as e:
            log.warning(f"FSub [{ch['id']}]: {e}")
    return out

async def fsub_kb(client: Client, violations: list, encoded: str) -> InlineKeyboardMarkup:
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
            rows.append([InlineKeyboardButton(f"📡 Join — {ch['name']}", url=url)])
        except Exception as e:
            log.error(f"FSub KB [{ch['id']}]: {e}")
    rows.append([InlineKeyboardButton("✅ I've Joined — Verify & Unlock",
                                      callback_data=f"verify_{encoded}")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════════════
#  FILE DELIVERY  (forward → preserves thumbnail/cover)
# ══════════════════════════════════════════════════════════════════════════════
async def deliver(client: Client, user: User, encoded: str):
    bot_id = bid(client)
    cfg    = await get_cfg(bot_id)
    dt     = cfg.get("auto_delete_time", Config.AUTO_DELETE_TIME)
    uid    = user.id
    sent   = []

    try:    decoded = dec(encoded)
    except: return await send(client, uid, "<b>❌ CORRUPTED LINK — Payload tampered.</b>")

    # Pre-file message
    pre = cfg.get("pre_file_msg") or DEF_PRE
    try:
        pm = await send(client, uid, fill(pre, user))
        sent.append(pm.id)
    except Exception as e:
        log.warning(f"Pre msg: {e}")

    # Forward file(s) — hide_sender_name=True hides your identity
    if decoded.startswith("file_"):
        mid = int(decoded.split("_", 1)[1])
        try:
            fw = await client.forward_messages(
                uid, Config.DB_CHANNEL, mid,
                hide_sender_name=True
            )
            sent.append((fw if not isinstance(fw, list) else fw[0]).id)
        except Exception as e:
            return await send(client, uid, f"<b>❌ RETRIEVAL FAILED:</b>\n<code>{e}</code>")

    elif decoded.startswith("bdoc_"):
        # New batch system — fetch exact ordered IDs from MongoDB
        batch_doc_id = decoded[5:]
        doc = await _batches.find_one({"_id": batch_doc_id})
        if not doc:
            return await send(client, uid, "<b>❌ BATCH NOT FOUND — Link may have expired.</b>")

        msg_ids = doc.get("message_ids", [])
        count   = len(msg_ids)

        note = await send(client, uid,
            f"<b>⚡ BATCH TRANSFER INITIATED</b>\n\n"
            f"<blockquote>[ FILES  : {count:<10}]\n"
            f"[ ORDER  : SEQUENTIAL  ]\n"
            f"[ STATUS : TRANSMITTING ]</blockquote>")
        sent.append(note.id)

        # Forward ALL in one API call — fastest, preserves order, hides identity
        try:
            fw = await client.forward_messages(
                uid, Config.DB_CHANNEL, msg_ids,
                hide_sender_name=True
            )
            if isinstance(fw, list):
                sent += [m.id for m in fw]
            else:
                sent.append(fw.id)
        except Exception as ex:
            # Fallback: chunk by 100 if too many
            log.warning(f"Bulk forward failed, chunking: {ex}")
            for chunk in [msg_ids[i:i+100] for i in range(0, len(msg_ids), 100)]:
                try:
                    fw = await client.forward_messages(
                        uid, Config.DB_CHANNEL, chunk,
                        hide_sender_name=True
                    )
                    if isinstance(fw, list):
                        sent += [m.id for m in fw]
                    else:
                        sent.append(fw.id)
                    await asyncio.sleep(0.3)
                except Exception as ex2:
                    log.warning(f"Chunk failed: {ex2}")

    # Legacy batch support (old range-based links)
    elif decoded.startswith("batch_"):
        p = decoded.split("_")
        s_id, e_id = int(p[1]), int(p[2])
        msg_ids = list(range(s_id, e_id + 1))
        note = await send(client, uid,
            f"<b>⚡ BATCH TRANSFER</b>\n\n"
            f"<blockquote>[ FILES  : {len(msg_ids):<10}]\n"
            f"[ STATUS : TRANSMITTING ]</blockquote>")
        sent.append(note.id)
        try:
            fw = await client.forward_messages(
                uid, Config.DB_CHANNEL, msg_ids, hide_sender_name=True
            )
            if isinstance(fw, list):
                sent += [m.id for m in fw]
            else:
                sent.append(fw.id)
        except Exception as ex:
            log.warning(f"Legacy batch: {ex}")
    else:
        return await send(client, uid, "<b>❌ UNKNOWN LINK FORMAT.</b>")

    # Auto-delete notice
    if sent and dt > 0:
        mn, sc = divmod(dt, 60)
        t_str  = f"{mn}m {sc}s" if mn else f"{sc}s"
        del_t  = cfg.get("auto_del_msg") or DEF_AUTODEL
        del_t  = fill(del_t, user, {"time": t_str})
        dm = await send(client, uid, del_t)
        sent.append(dm.id)
        asyncio.create_task(auto_del(client, uid, sent, dt))

# ══════════════════════════════════════════════════════════════════════════════
#  BOTFATHER COMMANDS SETUP
# ══════════════════════════════════════════════════════════════════════════════
USER_CMDS = [
    BotCommand("start",  "Initialize the system protocol"),
    BotCommand("help",   "Display available commands"),
    BotCommand("ping",   "Check system latency and status"),
    BotCommand("cancel", "Terminate current operation"),
]

ADMIN_CMDS = USER_CMDS + [
    BotCommand("genlink",          "Generate encrypted link (reply to file)"),
    BotCommand("batch",            "Start multi-file batch session"),
    BotCommand("process",          "Seal batch and generate access key"),
    BotCommand("stats",            "View global system analytics"),
    BotCommand("broadcast",        "Transmit message to all users"),
    BotCommand("add_fsub",         "Link a force-subscribe channel"),
    BotCommand("del_fsub",         "Remove a force-subscribe channel"),
    BotCommand("set_bot_name",     "Change bot name everywhere"),
    BotCommand("set_start",        "Configure welcome message (HTML)"),
    BotCommand("del_start",        "Reset welcome to default"),
    BotCommand("add_img",          "Add image to start rotation"),
    BotCommand("del_imgs",         "Clear all start images"),
    BotCommand("set_sticker",      "Set greeting sticker"),
    BotCommand("del_sticker",      "Remove greeting sticker"),
    BotCommand("set_pre_msg",      "Set pre-delivery message (HTML)"),
    BotCommand("del_pre_msg",      "Reset pre-delivery message"),
    BotCommand("set_auto_del_msg", "Set auto-delete warning message"),
    BotCommand("del_auto_del_msg", "Reset auto-delete message"),
    BotCommand("set_delete_time",  "Set auto-purge timer (seconds)"),
    BotCommand("add_admin",        "Authorize a new administrator"),
    BotCommand("del_admin",        "Revoke admin privileges"),
    BotCommand("set_fsub_msg",     "Customize the force-subscribe message"),
    BotCommand("del_fsub_msg",     "Reset force-subscribe message to default"),
    BotCommand("clone",            "Clone this bot with a new token"),
    BotCommand("list_clones",      "List all active clone bots"),
    BotCommand("stop_clone",       "Stop a specific clone bot"),
]

async def set_cmds(client: Client, extra_admins: list = None):
    try:
        # Basic commands for all users
        await client.set_bot_commands(USER_CMDS, scope=BotCommandScopeAllPrivateChats())
        # Full commands for owner
        await client.set_bot_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=Config.OWNER_ID))
        # Full commands for each extra admin
        if extra_admins:
            for aid in extra_admins:
                try:
                    await client.set_bot_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=aid))
                except: pass
        log.info(f"Bot commands set for @{client.me.username}")
    except Exception as e:
        log.warning(f"set_cmds: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  FILTERS (SYNCHRONOUS — compatible with all pyrofork versions)
# ══════════════════════════════════════════════════════════════════════════════
def _in_batch(flt, client, msg):
    me = getattr(client, "me", None)
    if not me or not msg.from_user: return False
    return (me.id, msg.from_user.id) in batch_sessions

def _clone_tok(flt, client, msg):
    me = getattr(client, "me", None)
    if not me or not msg.from_user: return False
    if msg.from_user.id != Config.OWNER_ID: return False
    if clone_pending.get(Config.OWNER_ID) != me.id: return False
    return bool(msg.text) and not msg.text.startswith("/")

in_batch_f   = filters.create(_in_batch)
clone_tok_f  = filters.create(_clone_tok)
MEDIA_F      = (filters.document | filters.video | filters.audio |
                filters.photo | filters.voice | filters.video_note | filters.animation)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    user = message.from_user; uid = user.id
    b    = bid(client)
    await reg(b, uid)
    cfg  = await get_cfg(b)
    bn   = cfg.get("bot_name", "KENSHIN FILE NEXUS")
    log.info(f"/start from {uid} on bot {b}")

    if len(message.command) > 1:
        encoded = message.command[1]
        v = await check_fsub(client, b, uid)
        if v:
            kb = await fsub_kb(client, v, encoded)
            return await reply(message, await get_fsub_msg(bid(client)), reply_markup=kb)
        return await deliver(client, user, encoded)

    welcome = cfg.get("start_message") or DEF_START(bn)
    welcome = fill(welcome, user)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Kenshin Anime", url="https://t.me/KENSHIN_ANIME"),
        InlineKeyboardButton("📚 Help", callback_data="cb_help"),
    ]])
    imgs = cfg.get("start_images", [])
    try:
        if imgs:
            await client.send_photo(message.chat.id, photo=random.choice(imgs),
                                    caption=welcome, reply_markup=kb, parse_mode=H)
        else:
            await reply(message, welcome, reply_markup=kb)
    except:
        await reply(message, welcome, reply_markup=kb)

    if cfg.get("start_sticker"):
        try:
            sm = await client.send_sticker(message.chat.id, cfg["start_sticker"])
            asyncio.create_task(_del(sm, 5))
        except: pass


@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, message: Message):
    b  = bid(client)
    bn = (await get_cfg(b)).get("bot_name", "KENSHIN FILE NEXUS")
    adm = await is_admin(b, message.from_user.id)
    if adm:
        txt = (
            f"<b>📡 {bn} — COMMAND MATRIX</b>\n\n"
            f"<blockquote>[ ACCESS : ADMINISTRATOR ]\n[ STATUS : FULL MATRIX  ]</blockquote>\n\n"
            f"<b>👤 USER COMMANDS</b>\n"
            f"/start — Initialize system\n/help — This matrix\n/ping — Check latency\n/cancel — Abort operation\n\n"
            f"<b>🔗 LINK GENERATION</b>\n"
            f"/genlink — Reply to file → single link\n"
            f"/batch — Start batch session\n"
            f"/process — Seal batch → get link\n\n"
            f"<b>📡 FORCE SUBSCRIBE</b>\n"
            f"/add_fsub &lt;id&gt; &lt;public/private/request&gt;\n"
            f"/del_fsub &lt;id&gt;\n\n"
            f"<b>🎨 INTERFACE CONFIG</b>\n"
            f"/set_fsub_msg &lt;HTML&gt; — Customize force-subscribe message\n"
            f"/del_fsub_msg — Reset fsub message to default\n"
            f"/set_bot_name &lt;name&gt; — Change bot name everywhere\n"
            f"/set_start &lt;HTML&gt; — Custom welcome\n"
            f"/del_start — Reset welcome\n"
            f"/add_img — Reply to photo → add to rotation\n"
            f"/del_imgs — Clear all images\n"
            f"/set_sticker — Reply to sticker → set greeting\n"
            f"/del_sticker — Remove sticker\n\n"
            f"<b>📨 DELIVERY CONFIG</b>\n"
            f"/set_pre_msg &lt;HTML&gt; — Message before files\n"
            f"/del_pre_msg — Reset to MADARA default\n"
            f"/set_auto_del_msg &lt;HTML&gt; — Custom timer msg\n"
            f"/del_auto_del_msg — Reset timer msg\n"
            f"/set_delete_time &lt;seconds&gt; — Auto-purge timer\n\n"
            f"<b>🛡️ ADMIN</b>\n"
            f"/stats — Analytics\n"
            f"/broadcast — Reply to msg → send to all users\n"
            f"/add_admin &lt;id or reply&gt;\n"
            f"/del_admin &lt;id&gt;\n\n"
            f"<b>🤖 CLONE SYSTEM (Owner only)</b>\n"
            f"/clone — Create a new bot clone\n"
            f"/list_clones — See active clones\n"
            f"/stop_clone &lt;bot_id&gt; — Stop a clone\n\n"
            f"<b>📋 PLACEHOLDERS</b>\n"
            f"<blockquote>{{mention}} {{first_name}} {{last_name}}\n"
            f"{{username}} {{id}} {{channel}} {{time}}</blockquote>"
        )
    else:
        txt = (
            f"<b>📡 {bn} — HELP</b>\n\n"
            f"<blockquote>[ ACCESS : USER     ]\n[ STATUS : LIMITED  ]</blockquote>\n\n"
            f"/start — Initialize system\n"
            f"/help — Display this menu\n"
            f"/ping — Check bot status\n"
            f"/cancel — Cancel current operation\n\n"
            f"<i>⚡ @KENSHIN_ANIME</i>"
        )
    await reply(message, txt)


@app.on_message(filters.command("ping") & filters.private)
async def cmd_ping(client: Client, message: Message):
    t  = time.monotonic()
    m  = await reply(message, "<b>⚡ Scanning systems...</b>")
    lt = (time.monotonic() - t) * 1000
    bn = (await get_cfg(bid(client))).get("bot_name", "KENSHIN FILE NEXUS")
    await m.edit(
        f"<b>🟢 SYSTEM OPERATIONAL</b>\n\n"
        f"<blockquote>[ LATENCY : {lt:.2f} ms   ]\n"
        f"[ DATABASE: MONGODB    ]\n"
        f"[ BOT     : {bn[:16]:<16}]\n"
        f"[ STATUS  : ONLINE     ]</blockquote>",
        parse_mode=H)


@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, message: Message):
    key = (bid(client), message.from_user.id)
    if key in batch_sessions:
        n = len(batch_sessions.pop(key))
        await reply(message,
            f"<b>🛑 OPERATION TERMINATED</b>\n\n"
            f"<blockquote>[ QUEUE  : {n} files cleared ]\n"
            f"[ STATUS : STANDBY           ]</blockquote>")
    else:
        await reply(message, "<b>⚠️ No active operation to cancel.</b>")


@app.on_message(filters.command("genlink") & filters.private)
async def cmd_genlink(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    t = message.reply_to_message
    if not t:
        return await reply(message,
            "<b>⚠️ Reply to a file with /genlink</b>\n\n"
            "1️⃣ Send/forward a file to bot\n2️⃣ Reply to it with /genlink\n3️⃣ Get encrypted link ⚡")
    proc = await reply(message, "<b>⚙️ Encrypting payload...</b>")
    try:
        fw = await t.forward(Config.DB_CHANNEL)
        fid = (fw if not isinstance(fw, list) else fw[0]).id
        ls  = enc(f"file_{fid}")
        lnk = f"https://t.me/{client.me.username}?start={ls}"
        cfg = await get_cfg(b)
        await s(b, "total_links", cfg.get("total_links", 0) + 1)
        await proc.edit(
            f"<b>🔗 LINK FORGED</b>\n\n"
            f"<blockquote>[ TYPE   : SINGLE FILE ]\n[ STATUS : SUCCESS    ]\n[ ID     : {fid}       ]</blockquote>\n\n"
            f"<b>🌐 Access Link:</b>\n<code>{lnk}</code>", parse_mode=H)
    except Exception as e:
        await proc.edit(f"<b>❌ FAILED:</b> <code>{e}</code>", parse_mode=H)


@app.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    batch_sessions[(b, message.from_user.id)] = []
    await reply(message,
        "<b>📦 BATCH SESSION STARTED</b>\n\n"
        "<blockquote>[ MODE   : CAPTURE ACTIVE ]\n[ QUEUE  : 0 FILES       ]\n[ STATUS : LISTENING...  ]</blockquote>\n\n"
        "→ Send/forward files one by one\n→ /process when done\n→ /cancel to abort\n\n<i>⚡ Nexus is listening...</i>")


@app.on_message(filters.private & in_batch_f & MEDIA_F)
async def cmd_collect(client: Client, message: Message):
    b   = bid(client); uid = message.from_user.id; key = (b, uid)
    try:
        fw  = await message.forward(Config.DB_CHANNEL)
        fid = (fw if not isinstance(fw, list) else fw[0]).id
        batch_sessions[key].append(fid)
        n   = len(batch_sessions[key])
        await reply(message,
            f"<b>✅ FILE #{n} QUEUED</b>\n<i>Stored in nexus.</i>\n\n"
            f"📦 Queue: <code>{n}</code> file(s) | /process to seal | /cancel to abort")
    except Exception as e:
        await reply(message, f"<b>❌ Queue error:</b> <code>{e}</code>")


@app.on_message(filters.command("process") & filters.private)
async def cmd_process(client: Client, message: Message):
    b = bid(client); uid = message.from_user.id
    if not await is_admin(b, uid):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    key  = (b, uid)
    sess = batch_sessions.get(key)
    if not sess:
        return await reply(message, "<b>⚠️ No active batch session. Start with /batch</b>")

    cnt  = len(sess)
    proc = await reply(message, "<b>⚙️ Sealing batch & generating encrypted key...</b>")

    # Store exact ordered IDs in MongoDB — no range guessing
    import time as _time
    batch_id = f"b_{b}_{uid}_{int(_time.time())}"
    await _batches.insert_one({
        "_id":         batch_id,
        "message_ids": sess,          # exact IDs in exact order user sent
        "bot_id":      b,
        "count":       cnt,
    })

    ls  = enc(f"bdoc_{batch_id}")
    lnk = f"https://t.me/{client.me.username}?start={ls}"
    cfg = await get_cfg(b)
    await s(b, "total_links", cfg.get("total_links", 0) + 1)
    del batch_sessions[key]

    await proc.edit(
        f"<b>🔗 BATCH SEALED — ACCESS KEY READY</b>\n\n"
        f"<blockquote>[ FILES     : {cnt:<10}]\n"
        f"[ ORDER     : SEQUENTIAL  ]\n"
        f"[ IDENTITY  : HIDDEN      ]\n"
        f"[ STATUS    : SUCCESS     ]</blockquote>\n\n"
        f"<b>🌐 Batch Link:</b>\n<code>{lnk}</code>\n\n"
        f"<i>📋 Delivers all {cnt} files in exact order, instantly.</i>", parse_mode=H)


@app.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    cfg  = await get_cfg(b)
    bn   = cfg.get("bot_name", "KENSHIN FILE NEXUS")
    tot  = await cnt_users(b)
    dt   = cfg.get("auto_delete_time", 0)
    dts  = f"{dt//60}m {dt%60}s" if dt else "DISABLED"
    await reply(message,
        f"<b>📊 {bn} — ANALYTICS</b>\n\n"
        f"<blockquote>"
        f"[ USERS        : {tot:<8}]\n"
        f"[ ADMINS       : {len(cfg.get('admins',[])):<8}]\n"
        f"[ LINKS GEN    : {cfg.get('total_links',0):<8}]\n"
        f"[ FSUB CHANS   : {len(cfg.get('fsub_channels',[])):<8}]\n"
        f"[ START IMGS   : {len(cfg.get('start_images',[])):<8}]\n"
        f"[ AUTO DELETE  : {dts:<8}]\n"
        f"[ PRE-MSG      : {'CUSTOM' if cfg.get('pre_file_msg') else 'DEFAULT':<8}]\n"
        f"[ STICKER      : {'ON' if cfg.get('start_sticker') else 'OFF':<8}]\n"
        f"[ CLONES LIVE  : {len(active_clones):<8}]\n"
        f"[ STATUS       : ONLINE   ]</blockquote>")


@app.on_message(filters.command("broadcast") & filters.private)
async def cmd_broadcast(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    if not message.reply_to_message:
        return await reply(message, "<b>⚠️ Reply to a message with /broadcast</b>")
    users = await get_users(b)
    prog  = await reply(message, f"<b>📡 Broadcasting to {len(users)} users...</b>")
    ok = fail = 0
    for uid in users:
        try:
            await message.reply_to_message.forward(uid); ok += 1
            await asyncio.sleep(0.05)
        except FloodWait as e: await asyncio.sleep(e.value + 1)
        except: fail += 1
    await prog.edit(
        f"<b>📡 BROADCAST COMPLETE</b>\n\n"
        f"<blockquote>[ SUCCESS : {ok}   ]\n[ FAILED  : {fail} ]\n[ TOTAL   : {len(users)} ]</blockquote>", parse_mode=H)


@app.on_message(filters.command("add_fsub") & filters.private)
async def cmd_add_fsub(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split()
    if len(p) < 3:
        return await reply(message,
            "<b>⚠️ Usage:</b> /add_fsub &lt;channel_id&gt; &lt;type&gt;\n\n"
            "<b>Types:</b>\n• <code>public</code> — public channel\n"
            "• <code>private</code> — private (invite link)\n"
            "• <code>request</code> — private (join request)\n\n"
            "<b>Example:</b> <code>/add_fsub -1001234567890 private</code>\n\n"
            "<i>⚠️ Make bot admin in that channel first!</i>")
    try:
        ch_id = int(p[1]); raw = p[2].lower()
        if raw not in ("public","private","request"):
            return await reply(message, "<b>❌ Type must be: public / private / request</b>")
        ch_type = "private_request" if raw == "request" else raw
        chat    = await client.get_chat(ch_id)
        cfg     = await get_cfg(b)
        if any(c["id"] == ch_id for c in cfg["fsub_channels"]):
            return await reply(message, "<b>⚠️ Channel already linked.</b>")
        cfg["fsub_channels"].append({"id": ch_id, "name": chat.title, "type": ch_type})
        await s(b, "fsub_channels", cfg["fsub_channels"])
        await reply(message,
            f"<b>✅ SECURITY CHANNEL LINKED</b>\n\n"
            f"<blockquote>[ CHANNEL : {chat.title[:20]:<20}]\n[ ID      : {ch_id}       ]\n[ TYPE    : {ch_type:<20}]</blockquote>")
    except Exception as e:
        await reply(message, f"<b>❌ Failed:</b> <code>{e}</code>\n<i>Make bot admin in the channel.</i>")


@app.on_message(filters.command("del_fsub") & filters.private)
async def cmd_del_fsub(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    cfg = await get_cfg(b); chs = cfg.get("fsub_channels", [])
    if not chs: return await reply(message, "<b>⚠️ No channels configured.</b>")
    p = message.text.split()
    if len(p) < 2:
        lst = "\n".join(f"• <code>{c['id']}</code> — <b>{c['name']}</b> [{c['type']}]" for c in chs)
        return await reply(message, f"<b>📋 ACTIVE FSub CHANNELS:</b>\n\n{lst}\n\n<b>Usage:</b> /del_fsub &lt;id&gt;")
    try:
        ch_id = int(p[1])
        new   = [c for c in chs if c["id"] != ch_id]
        if len(new) == len(chs): return await reply(message, "<b>❌ Channel ID not found.</b>")
        await s(b, "fsub_channels", new)
        await reply(message, f"<b>🚫 CHANNEL REMOVED</b>\n\n<blockquote>[ ID : {ch_id} ]\n[ STATUS : UNLINKED ]</blockquote>")
    except Exception as e:
        await reply(message, f"<b>❌ Error:</b> <code>{e}</code>")


@app.on_message(filters.command("set_bot_name") & filters.private)
async def cmd_set_bot_name(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split(None, 1)
    if len(p) < 2:
        cfg = await get_cfg(b)
        return await reply(message,
            f"<b>Current name:</b> <code>{cfg.get('bot_name','KENSHIN FILE NEXUS')}</code>\n\n"
            f"<b>Usage:</b> /set_bot_name &lt;new name&gt;\n<i>Appears in all messages, headers, footers.</i>")
    await s(b, "bot_name", p[1].strip())
    await reply(message, f"<b>✅ BOT NAME UPDATED</b>\n\n<blockquote>New: <b>{p[1].strip()}</b></blockquote>")


@app.on_message(filters.command("set_start") & filters.private)
async def cmd_set_start(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split(None, 1)
    if len(p) < 2:
        return await reply(message,
            "<b>⚠️ Usage:</b> /set_start &lt;HTML message&gt;\n\n"
            "<b>Supported tags:</b> &lt;b&gt; &lt;i&gt; &lt;code&gt; &lt;blockquote&gt; &lt;a&gt;\n"
            "<b>Placeholders:</b> {mention} {first_name} {last_name} {username} {id} {channel}\n\n"
            "/del_start → reset to default")
    await s(b, "start_message", p[1])
    await reply(message, "<b>✅ WELCOME MESSAGE UPDATED</b>\n<i>Active on next /start</i>")


@app.on_message(filters.command("del_start") & filters.private)
async def cmd_del_start(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    await s(b, "start_message", "")
    await reply(message, "<b>✅ Welcome message reset to default.</b>")


@app.on_message(filters.command("add_img") & filters.private)
async def cmd_add_img(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    t = message.reply_to_message
    if not t or not t.photo: return await reply(message, "<b>⚠️ Reply to a PHOTO with /add_img</b>")
    cfg = await get_cfg(b); imgs = cfg.get("start_images", [])
    imgs.append(t.photo.file_id); await s(b, "start_images", imgs)
    await reply(message, f"<b>✅ IMAGE ADDED</b>\n\n<blockquote>[ ROTATION : {len(imgs)} images ]\n[ STATUS   : ACTIVE   ]</blockquote>")


@app.on_message(filters.command("del_imgs") & filters.private)
async def cmd_del_imgs(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    cfg = await get_cfg(b); n = len(cfg.get("start_images", []))
    await s(b, "start_images", [])
    await reply(message, f"<b>🗑️ {n} IMAGE(S) PURGED</b>")


@app.on_message(filters.command("set_sticker") & filters.private)
async def cmd_set_sticker(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    t = message.reply_to_message
    if not t or not t.sticker:
        return await reply(message, "<b>⚠️ Reply to a STICKER with /set_sticker</b>\n<i>Appears after welcome, auto-deletes in 5s</i>")
    await s(b, "start_sticker", t.sticker.file_id)
    await reply(message, "<b>✅ GREETING STICKER SET</b>\n<i>Appears after welcome, auto-deletes in 5 seconds.</i>")


@app.on_message(filters.command("del_sticker") & filters.private)
async def cmd_del_sticker(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    await s(b, "start_sticker", "")
    await reply(message, "<b>✅ Sticker removed.</b>")


@app.on_message(filters.command("set_pre_msg") & filters.private)
async def cmd_set_pre_msg(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split(None, 1)
    if len(p) < 2:
        return await reply(message,
            "<b>⚠️ Usage:</b> /set_pre_msg &lt;HTML message&gt;\n\n"
            "<i>Sent BEFORE file delivery.</i>\n\n"
            "<b>Placeholders:</b>\n"
            "<blockquote>{mention} {first_name} {last_name} {username} {id} {channel}</blockquote>\n\n"
            "/del_pre_msg → reset to MADARA default")
    await s(b, "pre_file_msg", p[1])
    await reply(message, "<b>✅ PRE-FILE MESSAGE SET</b>\n<i>Sent before every file delivery.</i>")


@app.on_message(filters.command("del_pre_msg") & filters.private)
async def cmd_del_pre_msg(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    await s(b, "pre_file_msg", "")
    await reply(message, "<b>✅ Pre-file message reset to MADARA default.</b>")


@app.on_message(filters.command("set_auto_del_msg") & filters.private)
async def cmd_set_adm(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split(None, 1)
    if len(p) < 2:
        return await reply(message,
            "<b>⚠️ Usage:</b> /set_auto_del_msg &lt;HTML message&gt;\n\n"
            "<i>Shown after file delivery.</i>\n\n"
            "<b>Placeholders:</b>\n<blockquote>{time} {mention} {first_name}</blockquote>\n\n"
            "/del_auto_del_msg → reset to default")
    await s(b, "auto_del_msg", p[1])
    await reply(message, "<b>✅ AUTO-DELETE MESSAGE SET</b>")


@app.on_message(filters.command("del_auto_del_msg") & filters.private)
async def cmd_del_adm(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    await s(b, "auto_del_msg", "")
    await reply(message, "<b>✅ Auto-delete message reset to default.</b>")


@app.on_message(filters.command("set_delete_time") & filters.private)
async def cmd_set_dt(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id): return await reply(message, "<b>🔒 Admin access required.</b>")
    cfg = await get_cfg(b); p = message.text.split()
    if len(p) < 2:
        cur = cfg.get("auto_delete_time", 0)
        st  = f"{cur//60}m {cur%60}s" if cur else "DISABLED"
        return await reply(message,
            f"<b>⏳ AUTO-PURGE CONFIG</b>\n\nCurrent: <code>{cur}s</code> → <b>{st}</b>\n\n"
            f"<b>Usage:</b> /set_delete_time &lt;seconds&gt; | <code>0</code> to disable\n\n"
            f"<blockquote>/set_delete_time 300  → 5 min\n/set_delete_time 600  → 10 min\n"
            f"/set_delete_time 1800 → 30 min\n/set_delete_time 0    → Disabled</blockquote>")
    try:
        secs = int(p[1])
        if secs < 0: return await reply(message, "<b>❌ Cannot be negative.</b>")
        await s(b, "auto_delete_time", secs)
        st = f"{secs//60}m {secs%60}s" if secs else "DISABLED"
        await reply(message, f"<b>✅ AUTO-PURGE SET</b>\n\n<blockquote>[ TIMER : {st} ]</blockquote>")
    except ValueError:
        await reply(message, "<b>❌ Invalid. Use whole seconds e.g.</b> <code>600</code>")


@app.on_message(filters.command("add_admin") & filters.private)
async def cmd_add_admin(client: Client, message: Message):
    b = bid(client)
    if message.from_user.id != Config.OWNER_ID:
        return await reply(message, "<b>🔒 OWNER-ONLY COMMAND.</b>")
    t = message.reply_to_message; p = message.text.split()
    if t: new_id, name = t.from_user.id, t.from_user.first_name
    elif len(p) > 1:
        try: new_id, name = int(p[1]), f"ID#{p[1]}"
        except: return await reply(message, "<b>❌ Invalid user ID.</b>")
    else:
        return await reply(message, "<b>⚠️ Reply to user or send /add_admin &lt;user_id&gt;</b>")
    cfg = await get_cfg(b); admins = cfg.get("admins", [])
    if new_id in admins or new_id == Config.OWNER_ID:
        return await reply(message, "<b>⚠️ Already an admin.</b>")
    admins.append(new_id); await s(b, "admins", admins)
    try: await client.set_bot_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=new_id))
    except: pass
    await reply(message,
        f"<b>✅ ADMIN GRANTED</b>\n\n<blockquote>[ NAME   : {name[:20]:<20}]\n[ ID     : {new_id}       ]\n[ STATUS : AUTHORIZED     ]</blockquote>")


@app.on_message(filters.command("del_admin") & filters.private)
async def cmd_del_admin(client: Client, message: Message):
    b = bid(client)
    if message.from_user.id != Config.OWNER_ID:
        return await reply(message, "<b>🔒 OWNER-ONLY COMMAND.</b>")
    cfg = await get_cfg(b); p = message.text.split()
    if len(p) < 2:
        lst = "\n".join(f"• <code>{a}</code>" for a in cfg.get("admins",[])) or "<i>None</i>"
        return await reply(message, f"<b>📋 ADMINS:</b>\n\n{lst}\n\n<b>Usage:</b> /del_admin &lt;user_id&gt;")
    try:
        rm = int(p[1])
        if rm == Config.OWNER_ID: return await reply(message, "<b>❌ Cannot remove owner.</b>")
        admins = cfg.get("admins", [])
        if rm not in admins: return await reply(message, "<b>❌ Not in admin list.</b>")
        admins.remove(rm); await s(b, "admins", admins)
        try: await client.set_bot_commands(USER_CMDS, scope=BotCommandScopeChat(chat_id=rm))
        except: pass
        await reply(message, f"<b>🚫 ADMIN REVOKED</b>\n\n<blockquote>[ ID : {rm} ]\n[ STATUS : UNAUTHORIZED ]</blockquote>")
    except Exception as e:
        await reply(message, f"<b>❌ Error:</b> <code>{e}</code>")


# ══════════════════════════════════════════════════════════════════════════════
#  CLONE SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def setup_handlers(cl: Client):
    """Register all handlers on a clone client."""
    H = handlers.MessageHandler
    CB = handlers.CallbackQueryHandler
    pv = filters.private
    cl.add_handler(H(cmd_start,       filters.command("start")            & pv))
    cl.add_handler(H(cmd_help,        filters.command("help")             & pv))
    cl.add_handler(H(cmd_ping,        filters.command("ping")             & pv))
    cl.add_handler(H(cmd_cancel,      filters.command("cancel")           & pv))
    cl.add_handler(H(cmd_genlink,     filters.command("genlink")          & pv))
    cl.add_handler(H(cmd_batch,       filters.command("batch")            & pv))
    cl.add_handler(H(cmd_process,     filters.command("process")          & pv))
    cl.add_handler(H(cmd_stats,       filters.command("stats")            & pv))
    cl.add_handler(H(cmd_broadcast,   filters.command("broadcast")        & pv))
    cl.add_handler(H(cmd_add_fsub,    filters.command("add_fsub")         & pv))
    cl.add_handler(H(cmd_del_fsub,    filters.command("del_fsub")         & pv))
    cl.add_handler(H(cmd_set_bot_name,filters.command("set_bot_name")     & pv))
    cl.add_handler(H(cmd_set_start,   filters.command("set_start")        & pv))
    cl.add_handler(H(cmd_del_start,   filters.command("del_start")        & pv))
    cl.add_handler(H(cmd_add_img,     filters.command("add_img")          & pv))
    cl.add_handler(H(cmd_del_imgs,    filters.command("del_imgs")         & pv))
    cl.add_handler(H(cmd_set_sticker, filters.command("set_sticker")      & pv))
    cl.add_handler(H(cmd_del_sticker, filters.command("del_sticker")      & pv))
    cl.add_handler(H(cmd_set_pre_msg, filters.command("set_pre_msg")      & pv))
    cl.add_handler(H(cmd_del_pre_msg, filters.command("del_pre_msg")      & pv))
    cl.add_handler(H(cmd_set_adm,     filters.command("set_auto_del_msg") & pv))
    cl.add_handler(H(cmd_del_adm,     filters.command("del_auto_del_msg") & pv))
    cl.add_handler(H(cmd_set_dt,      filters.command("set_delete_time")  & pv))
    cl.add_handler(H(cmd_add_admin,   filters.command("add_admin")        & pv))
    cl.add_handler(H(cmd_del_admin,   filters.command("del_admin")        & pv))
    cl.add_handler(H(cmd_set_fsub_msg,filters.command("set_fsub_msg")    & pv))
    cl.add_handler(H(cmd_del_fsub_msg,filters.command("del_fsub_msg")    & pv))
    cl.add_handler(H(cmd_clone,       filters.command("clone")            & pv))
    cl.add_handler(H(cmd_list_clones, filters.command("list_clones")      & pv))
    cl.add_handler(H(cmd_stop_clone,  filters.command("stop_clone")       & pv))
    cl.add_handler(H(cmd_collect,     pv & in_batch_f & MEDIA_F))
    cl.add_handler(H(cmd_clone_token, pv & clone_tok_f), group=-1)
    cl.add_handler(CB(on_callback))


@app.on_message(filters.command("set_fsub_msg") & filters.private)
async def cmd_set_fsub_msg(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    p = message.text.split(None, 1)
    if len(p) < 2:
        cfg = await get_cfg(b)
        cur = cfg.get("fsub_message", "") or "<i>Default MADARA message</i>"
        return await reply(message,
            f"<b>⚠️ Usage:</b> /set_fsub_msg &lt;HTML message&gt;\n\n"
            f"<b>Current:</b>\n{cur[:300]}\n\n"
            f"<i>This message appears when user has NOT joined required channels.</i>\n\n"
            f"<b>Tips:</b>\n"
            f"• Use &lt;b&gt; &lt;i&gt; &lt;blockquote&gt; tags\n"
            f"• Keep it short and clear\n"
            f"• Explain what channels to join\n\n"
            f"/del_fsub_msg → reset to default MADARA message")
    await s(b, "fsub_message", p[1])
    await reply(message,
        "<b>✅ FSUB MESSAGE UPDATED</b>\n\n"
        "<blockquote>[ STATUS : ACTIVE ]\n"
        "[ SHOWN  : When user hasn't joined channels ]</blockquote>\n\n"
        "<i>Preview it by trying to access a link without joining.</i>")


@app.on_message(filters.command("del_fsub_msg") & filters.private)
async def cmd_del_fsub_msg(client: Client, message: Message):
    b = bid(client)
    if not await is_admin(b, message.from_user.id):
        return await reply(message, "<b>🔒 Admin access required.</b>")
    await s(b, "fsub_message", "")
    await reply(message, "<b>✅ FSub message reset to default MADARA style.</b>")


@app.on_message(filters.command("clone") & filters.private)
async def cmd_clone(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await reply(message, "<b>🔒 OWNER-ONLY COMMAND.</b>")
    clone_pending[Config.OWNER_ID] = bid(client)
    await reply(message,
        "<b>🤖 CLONE PROTOCOL — INITIATED</b>\n\n"
        "<blockquote>[ STATUS : AWAITING BOT TOKEN ]\n[ OWNER  : VERIFIED          ]</blockquote>\n\n"
        "📨 Send the <b>Bot Token</b> for the new clone.\n"
        "<i>Get it: @BotFather → /newbot → copy token</i>\n\n"
        "Send /cancel to abort.")


@app.on_message(filters.private & clone_tok_f)
async def cmd_clone_token(client: Client, message: Message):
    token = message.text.strip()
    clone_pending.pop(Config.OWNER_ID, None)
    proc  = await reply(message, "<b>⚙️ Cloning in progress...</b>")
    try:
        num  = token.split(":")[0]
        cl   = Client(f"clone_{num}", api_id=Config.API_ID, api_hash=Config.API_HASH,
                      bot_token=token, in_memory=True)
        setup_handlers(cl)
        await cl.start()
        cme  = cl.me
        await set_cmds(cl)
        await _clones.update_one({"token": token},
                                 {"$set": {"token": token, "bot_id": cme.id, "username": cme.username}},
                                 upsert=True)
        active_clones[cme.id] = cl
        await proc.edit(
            f"<b>✅ CLONE ONLINE</b>\n\n"
            f"<blockquote>[ NAME     : {cme.first_name[:20]:<20}]\n"
            f"[ USERNAME : @{cme.username:<19}]\n"
            f"[ BOT ID   : {cme.id:<20}]\n"
            f"[ STATUS   : ONLINE              ]</blockquote>\n\n"
            f"<i>⚡ Configure via its own chat. Settings are independent.</i>", parse_mode=H)
        log.info(f"Clone @{cme.username} started")
    except Exception as e:
        await proc.edit(f"<b>❌ CLONE FAILED:</b>\n<code>{e}</code>\n\n<i>Check token and retry with /clone</i>", parse_mode=H)
        log.error(f"Clone failed: {e}")


@app.on_message(filters.command("list_clones") & filters.private)
async def cmd_list_clones(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await reply(message, "<b>🔒 OWNER-ONLY COMMAND.</b>")
    if not active_clones:
        return await reply(message, "<b>📋 NO ACTIVE CLONES</b>\n<i>Use /clone to create one.</i>")
    lines = [f"• @{cl.me.username} — <code>{bid_}</code>" for bid_, cl in active_clones.items()]
    await reply(message,
        f"<b>🤖 ACTIVE CLONES ({len(active_clones)})</b>\n\n" +
        "\n".join(lines) + "\n\n<i>/stop_clone &lt;bot_id&gt; to stop one.</i>")


@app.on_message(filters.command("stop_clone") & filters.private)
async def cmd_stop_clone(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        return await reply(message, "<b>🔒 OWNER-ONLY COMMAND.</b>")
    p = message.text.split()
    if len(p) < 2:
        return await reply(message, "<b>⚠️ Usage:</b> /stop_clone &lt;bot_id&gt;\n\nSee /list_clones for IDs.")
    try:
        tid = int(p[1])
        if tid not in active_clones:
            return await reply(message, f"<b>❌ Clone <code>{tid}</code> not found.</b>")
        cl = active_clones.pop(tid); un = cl.me.username
        await cl.stop()
        await _clones.delete_one({"bot_id": tid})
        await reply(message,
            f"<b>🛑 CLONE STOPPED</b>\n\n"
            f"<blockquote>[ USERNAME : @{un:<20}]\n[ STATUS   : OFFLINE             ]</blockquote>")
    except Exception as e:
        await reply(message, f"<b>❌ Error:</b> <code>{e}</code>")


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK QUERY
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query()
async def on_callback(client: Client, cq: CallbackQuery):
    data = cq.data; uid = cq.from_user.id; user = cq.from_user; b = bid(client)

    if data.startswith("verify_"):
        encoded = data[7:]
        v = await check_fsub(client, b, uid)
        if v:
            kb = await fsub_kb(client, v, encoded)
            await cq.answer("⚠️ Join all required channels first!", show_alert=True)
            try: await cq.message.edit_reply_markup(kb)
            except: pass
            return
        await cq.answer("✅ Verified! Unlocking files...", show_alert=False)
        try: await cq.message.delete()
        except: pass
        await send(client, uid,
            "<b>✅ ACCESS GRANTED</b>\n\n"
            "<blockquote>[ CLEARANCE : GRANTED  ]\n[ FILES     : UNLOCKED ]</blockquote>\n"
            "<i>Retrieving your files...</i>")
        await deliver(client, user, encoded)

    elif data == "cb_help":
        await cq.answer("📚 Send /help for commands!", show_alert=True)
    else:
        await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    await app.start()
    me = app.me
    if not Config.DB_CHANNEL:
        log.warning("⚠️  DB_CHANNEL not set!")

    cfg = await get_cfg(me.id)
    await set_cmds(app, extra_admins=cfg.get("admins", []))

    log.info("=" * 55)
    log.info("  MADARA UCHIHA BOT — SYSTEM ONLINE")
    log.info(f"  Bot      : @{me.username}")
    log.info(f"  Name     : {me.first_name}")
    log.info(f"  Owner ID : {Config.OWNER_ID}")
    log.info(f"  DB Chan  : {Config.DB_CHANNEL}")
    log.info(f"  Database : MongoDB ✓")
    log.info("=" * 55)

    # Auto-restart saved clones
    async for doc in _clones.find({}):
        token = doc.get("token", "")
        try:
            num = token.split(":")[0]
            cl  = Client(f"clone_{num}", api_id=Config.API_ID, api_hash=Config.API_HASH,
                         bot_token=token, in_memory=True)
            setup_handlers(cl)
            await cl.start()
            cme = cl.me
            active_clones[cme.id] = cl
            log.info(f"  Clone    : @{cme.username} — ONLINE")
        except Exception as e:
            log.error(f"  Clone failed [{token[:20]}]: {e}")

    log.info("=" * 55)
    await idle()

    for cl in active_clones.values():
        try: await cl.stop()
        except: pass
    await app.stop()
    log.info("MADARA UCHIHA BOT — SHUTDOWN COMPLETE")

app.run(main())
