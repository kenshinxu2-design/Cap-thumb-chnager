import os, asyncio, random, string, time
from datetime import datetime
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant, FloodWait, RPCError

# --- SYSTEM INITIALIZATION ---
load_dotenv()
START_TIME = time.time()

# Environment Validation
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_ID = int(os.environ.get("DUMP_CHANNEL"))
OWNER_ID = int(os.environ.get("ADMIN_ID"))

# Database Core
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["VOID_FILES_V3"]
links_db = db["vault"]
settings_db = db["system_config"]
users_db = db["authorized_users"]

app = Client(
    "VOID_BOT",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

# Volatile Memory
USER_STATE = {}
BATCH_DATA = {}

# --- CORE UTILITIES ---

async def get_config():
    conf = await settings_db.find_one({"_id": "GLOBAL_CONFIG"})
    if not conf:
        default_conf = {
            "_id": "GLOBAL_CONFIG",
            "admins": [OWNER_ID],
            "fsubs": [],
            "assets": [],
            "sticker": None,
            "welcome_text": (
                "<b>◈ SYTEM INITIALIZED ◈</b>\n\n"
                "<b>GREETINGS SUBJECT:</b> {mention}\n"
                "<b>ID:</b> <code>{id}</code>\n\n"
                "<i>I am the Void Sentinel. Send me a secure link to retrieve "
                "data from the encrypted vault.</i>\n\n"
                "<b>STATUS:</b> <code>OPERATIONAL</code>"
            )
        }
        await settings_db.insert_one(default_conf)
        return default_conf
    return conf

async def check_security_clearance(client, user_id, fsubs):
    """Checks if the user has fulfilled Force Subscription requirements."""
    restricted = []
    for channel in fsubs:
        try:
            await client.get_chat_member(channel["chat_id"], user_id)
        except UserNotParticipant:
            try:
                # Generate dynamic invite based on type (Link vs Request)
                invite = await client.create_chat_invite_link(
                    channel["chat_id"],
                    creates_join_request=(True if channel["type"] == "request" else False)
                )
                chat = await client.get_chat(channel["chat_id"])
                restricted.append({
                    "title": chat.title,
                    "url": invite.invite_link,
                    "mode": channel["type"]
                })
            except Exception as e:
                print(f"[!] System Error on Link Gen: {e}")
        except Exception:
            pass
    return restricted

# --- COMMAND HANDLERS ---

@app.on_message(filters.command("start") & filters.private)
async def protocol_start(client, message):
    user_id = message.from_user.id
    if not await users_db.find_one({"_id": user_id}):
        await users_db.insert_one({"_id": user_id, "join_date": datetime.now()})

    config = await get_config()
    
    # Security Protocol (Force Sub)
    if config["fsubs"]:
        barriers = await check_security_clearance(client, user_id, config["fsubs"])
        if barriers:
            markup = []
            for b in barriers:
                label = f"🔓 ACCESS {b['title'].upper()}" + (" (REQ)" if b['mode'] == "request" else "")
                markup.append([InlineKeyboardButton(label, url=b['url'])])
            
            payload = message.command[1] if len(message.command) > 1 else None
            if payload:
                markup.append([InlineKeyboardButton("🔄 RE-AUTHENTICATE", url=f"https://t.me/{client.me.username}?start={payload}")])
            
            return await message.reply_text(
                "<b>❌ ACCESS DENIED</b>\n\n"
                "Security clearance insufficient. You must synchronize with the "
                "required channels to access the encrypted data.",
                reply_markup=InlineKeyboardMarkup(markup)
            )

    # Vault Retrieval Logic
    if len(message.command) > 1:
        vault_id = message.command[1]
        data = await links_db.find_one({"_id": vault_id})
        if not data:
            return await message.reply_text("<b>🚫 INVALID ACCESS KEY</b>\nData not found in Void.")
        
        load_msg = await message.reply_text("<i>Decoding encrypted packets... 📡</i>")
        for file_id in data["file_ids"]:
            try:
                sent = await client.copy_message(user_id, DUMP_ID, file_id)
                # Auto-destruct task (30 minutes)
                asyncio.create_task((lambda s: (asyncio.sleep(1800), s.delete()))(sent))
                await asyncio.sleep(0.7)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except: pass
        await load_msg.edit_text("<b>✅ DATA TRANSFERRED</b>\nSecure files will auto-destruct in 30 minutes.")
        return

    # Visual Asset Deployment
    caption = config["welcome_text"].format(mention=message.from_user.mention, id=user_id)
    try:
        if config["sticker"]:
            stk = await message.reply_sticker(config["sticker"])
            asyncio.create_task((lambda s: (asyncio.sleep(4), s.delete()))(stk))
            
        if config["assets"]:
            await message.reply_photo(random.choice(config["assets"]), caption=caption)
        else:
            await message.reply_text(caption)
    except:
        await message.reply_text(caption)

@app.on_message(filters.command("help") & filters.private)
async def protocol_help(client, message):
    config = await get_config()
    is_admin = message.from_user.id in config["admins"]
    
    help_menu = (
        "<b>◈ CENTRAL COMMAND MANUAL ◈</b>\n\n"
        "<b>USER COMMANDS:</b>\n"
        "├ /start - Initialize system\n"
        "└ /help - View this manual\n\n"
    )
    if is_admin:
        help_menu += (
            "<b>ADMINISTRATIVE OVERRIDE:</b>\n"
            "├ /genlink - Encrypt single file\n"
            "├ /batch - Start multi-file sequence\n"
            "├ /process - Finalize and lock batch\n"
            "├ /stats - System diagnostics\n"
            "├ /add_fsub - Link security channel\n"
            "├ /add_img - Add visual asset\n"
            "├ /broadcast - Global transmission\n"
            "└ /cancel - Abort current process"
        )
    await message.reply_text(help_menu)

# --- ADMINISTRATIVE PROTOCOLS ---

@app.on_message(filters.command("genlink") & filters.private)
async def admin_gen(client, message):
    config = await get_config()
    if message.from_user.id not in config["admins"]: return
    USER_STATE[message.from_user.id] = "AWAIT_SINGLE"
    await message.reply_text("<b>SYSTEM:</b> Uplink ready. Send the file for encryption.")

@app.on_message(filters.command("batch") & filters.private)
async def admin_batch(client, message):
    config = await get_config()
    if message.from_user.id not in config["admins"]: return
    USER_STATE[message.from_user.id] = "AWAIT_BATCH"
    BATCH_DATA[message.from_user.id] = []
    await message.reply_text("<b>SYSTEM:</b> Batch sequence initiated. Send files. Use /process to finalize.")

@app.on_message(filters.command("process") & filters.private)
async def admin_finalize(client, message):
    uid = message.from_user.id
    if uid not in BATCH_DATA or not BATCH_DATA[uid]:
        return await message.reply_text("<b>SYSTEM ERROR:</b> No data in current buffer.")
    
    proc = await message.reply_text("<i>Securing data packets... 🔐</i>")
    f_ids = []
    for msg in BATCH_DATA[uid]:
        copy = await msg.copy(DUMP_ID)
        f_ids.append(copy.id)
    
    key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    await links_db.insert_one({"_id": key, "file_ids": f_ids, "created_at": datetime.now()})
    
    USER_STATE.pop(uid, None)
    BATCH_DATA.pop(uid, None)
    await proc.edit_text(f"<b>✅ ENCRYPTION COMPLETE</b>\n\n<b>Access Key:</b>\n<code>https://t.me/{client.me.username}?start={key}</code>")

@app.on_message(filters.command("add_fsub") & filters.private)
async def admin_fsub(client, message):
    config = await get_config()
    if message.from_user.id not in config["admins"]: return
    # Format: /add_fsub [ID] [link/request]
    if len(message.command) < 3:
        return await message.reply_text("<b>Usage:</b> <code>/add_fsub -100xxx link</code>")
    
    try:
        chat = await client.get_chat(message.command[1])
        f_type = message.command[2].lower()
        await settings_db.update_one(
            {"_id": "GLOBAL_CONFIG"},
            {"$push": {"fsubs": {"chat_id": chat.id, "type": f_type}}}
        )
        await message.reply_text(f"<b>SYSTEM:</b> Security barrier added for <b>{chat.title}</b>.")
    except Exception as e:
        await message.reply_text(f"<b>CRITICAL ERROR:</b> {e}")

@app.on_message(filters.command("stats") & filters.private)
async def admin_stats(client, message):
    config = await get_config()
    if message.from_user.id not in config["admins"]: return
    
    u_count = await users_db.count_documents({})
    l_count = await links_db.count_documents({})
    uptime = time.strftime("%Hh %Mm %Ss", time.gmtime(time.time() - START_TIME))
    
    await message.reply_text(
        "<b>◈ SYSTEM DIAGNOSTICS ◈</b>\n\n"
        f"<b>🛰 Uptime:</b> <code>{uptime}</code>\n"
        f"<b>👥 Total Subjects:</b> <code>{u_count}</code>\n"
        f"<b>📂 Encrypted Links:</b> <code>{l_count}</code>\n"
        f"<b>⚡ System Status:</b> <code>OPTIMAL</code>"
    )

@app.on_message(filters.command("ping") & filters.private)
async def system_ping(client, message):
    start = time.time()
    msg = await message.reply_text("<i>Pinging the Void...</i>")
    lat = (time.time() - start) * 1000
    await msg.edit_text(f"<b>🛰 LATENCY:</b> <code>{lat:.2f}ms</code>")

# --- FILE INTERCEPTOR ---

@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio), group=5)
async def file_catcher(client, message):
    uid = message.from_user.id
    state = USER_STATE.get(uid)
    
    if state == "AWAIT_SINGLE":
        copy = await message.copy(DUMP_ID)
        key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        await links_db.insert_one({"_id": key, "file_ids": [copy.id], "created_at": datetime.now()})
        USER_STATE.pop(uid)
        await message.reply_text(f"<b>✅ LINK GENERATED:</b>\n<code>https://t.me/{client.me.username}?start={key}</code>")
        
    elif state == "AWAIT_BATCH":
        BATCH_DATA[uid].append(message)
        await message.reply_text(f"<b>📦 BUFFERED:</b> Packet {len(BATCH_DATA[uid])} registered.")

# --- STARTUP ---
print("""
---------------------------------------
   VOID SENTINEL PROTOCOL ACTIVATED
   LANGUAGE: STRICT ENGLISH
   STATUS: SUPREME
---------------------------------------
""")
app.run()
