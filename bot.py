import os
import asyncio
import random
import string
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant

# Env Load
load_dotenv()
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL = int(os.environ.get("DUMP_CHANNEL"))
OWNER_ID = int(os.environ.get("ADMIN_ID")) # Main Owner

# Database Setup
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["DekuAdvancedDB"]
links_col = db["links"]
settings_col = db["settings"]

app = Client("DekuBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-Memory State for Queue and Admin Inputs
USER_STATE = {}
BATCH_QUEUE = {}

# --- DATABASE HELPER FUNCTIONS ---
async def get_settings():
    settings = await settings_col.find_one({"_id": "bot_settings"})
    if not settings:
        default = {
            "_id": "bot_settings",
            "admins": [OWNER_ID],
            "fsubs": [],
            "start_imgs": ["https://i.pinimg.com/736x/88/ab/9c/88ab9ca826647228bfb51fa1548e65bd.jpg"],
            "start_msg": "<b><blockquote>DEKU ⚡</blockquote></b>\n\n<b>Hello {mention},</b>\n\n<blockquote>I am the File Store bot.</blockquote>\n\n<b><i>Save files or lose them in 30 mins.</i></b>",
            "start_sticker": None
        }
        await settings_col.insert_one(default)
        return default
    return settings

def gen_hash(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def check_fsub(client, user_id, fsubs):
    not_joined = []
    for channel in fsubs:
        try:
            await client.get_chat_member(channel, user_id)
        except UserNotParticipant:
            not_joined.append(channel)
        except Exception:
            pass # Ignore if bot is not admin in that channel
    return not_joined

async def auto_delete_task(message, delay=1800):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

# --- ADMIN COMMANDS (Dynamic Settings) ---
def is_admin(user_id, settings):
    return user_id in settings["admins"] or user_id == OWNER_ID

@app.on_message(filters.command("set_start_img") & filters.private)
async def cmd_add_img(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    if len(message.command) > 1:
        img_url = message.command[1]
        await settings_col.update_one({"_id": "bot_settings"}, {"$push": {"start_imgs": img_url}})
        await message.reply_text("⚡ Image added to rotation.")
    else:
        await message.reply_text("Send image URL with command: `/set_start_img [URL]`")

@app.on_message(filters.command("set_start_msg") & filters.private)
async def cmd_set_msg(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    # Needs listener or direct extraction. For simplicity, extracting directly:
    text = message.text.split(None, 1)
    if len(text) > 1:
        await settings_col.update_one({"_id": "bot_settings"}, {"$set": {"start_msg": text[1]}})
        await message.reply_text("⚡ Start message updated.")
    else:
        await message.reply_text("Send message with command: `/set_start_msg [Your Message...]`")

@app.on_message(filters.command("set_start_sticker") & filters.private)
async def cmd_set_sticker(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    if message.reply_to_message and message.reply_to_message.sticker:
        sticker_id = message.reply_to_message.sticker.file_id
        await settings_col.update_one({"_id": "bot_settings"}, {"$set": {"start_sticker": sticker_id}})
        await message.reply_text("⚡ Start sticker updated.")
    else:
        await message.reply_text("Reply to a sticker with `/set_start_sticker`")

@app.on_message(filters.command("set_fsub") & filters.private)
async def cmd_add_fsub(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    if len(message.command) > 1:
        channel = message.command[1].replace("@", "")
        await settings_col.update_one({"_id": "bot_settings"}, {"$push": {"fsubs": channel}})
        await message.reply_text(f"⚡ Fsub added: @{channel}")
    else:
        await message.reply_text("Usage: `/set_fsub [channel_username]`")

@app.on_message(filters.command("del_fsub") & filters.private)
async def cmd_del_fsub(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    if len(message.command) > 1:
        channel = message.command[1].replace("@", "")
        await settings_col.update_one({"_id": "bot_settings"}, {"$pull": {"fsubs": channel}})
        await message.reply_text(f"⚡ Fsub removed: @{channel}")
    else:
        await message.reply_text("Usage: `/del_fsub [channel_username]`")

# --- FILE STORE LOGIC (Gen & Batch) ---

@app.on_message(filters.command("genlink") & filters.private)
async def gen_link_start(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    USER_STATE[message.from_user.id] = "WAITING_SINGLE"
    await message.reply_text("⚡ Send me the file/video to generate a link.")

@app.on_message(filters.command("batch") & filters.private)
async def batch_start(client, message):
    settings = await get_settings()
    if not is_admin(message.from_user.id, settings): return
    USER_STATE[message.from_user.id] = "WAITING_BATCH"
    BATCH_QUEUE[message.from_user.id] = []
    await message.reply_text("⚡ Send me files one by one. When done, send `/process`.")

@app.on_message(filters.command("process") & filters.private)
async def process_batch(client, message):
    user_id = message.from_user.id
    if USER_STATE.get(user_id) != "WAITING_BATCH" or not BATCH_QUEUE.get(user_id):
        return await message.reply_text("⚡ No batch in progress. Use /batch first.")
    
    msg = await message.reply_text("⚡ Forwarding to Dump Channel and creating link...")
    files = BATCH_QUEUE[user_id]
    msg_ids = []
    
    for file_msg in files:
        copied = await file_msg.copy(DUMP_CHANNEL)
        msg_ids.append(copied.id)
        await asyncio.sleep(0.5)
        
    link_hash = gen_hash(10)
    await links_col.insert_one({"_id": link_hash, "ids": msg_ids, "type": "batch"})
    
    # Cleanup
    USER_STATE.pop(user_id, None)
    BATCH_QUEUE.pop(user_id, None)
    
    bot_usr = client.me.username
    link = f"https://t.me/{bot_usr}?start={link_hash}"
    await msg.edit_text(f"<b>⚡ Batch Link Ready ({len(msg_ids)} files):</b>\n\n<code>{link}</code>", parse_mode=enums.ParseMode.HTML)

# Handle incoming files for admins
@app.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo), group=1)
async def handle_admin_files(client, message):
    user_id = message.from_user.id
    state = USER_STATE.get(user_id)
    
    if state == "WAITING_SINGLE":
        msg = await message.reply_text("⚡ Processing...")
        copied = await message.copy(DUMP_CHANNEL)
        link_hash = gen_hash(8)
        await links_col.insert_one({"_id": link_hash, "ids": [copied.id], "type": "single"})
        bot_usr = client.me.username
        link = f"https://t.me/{bot_usr}?start={link_hash}"
        USER_STATE.pop(user_id, None)
        await msg.edit_text(f"<b>⚡ Single Link Ready:</b>\n\n<code>{link}</code>", parse_mode=enums.ParseMode.HTML)
        
    elif state == "WAITING_BATCH":
        BATCH_QUEUE[user_id].append(message)
        await message.reply_text(f"⚡ Added to queue. Total: {len(BATCH_QUEUE[user_id])}", quote=True)

# --- START COMMAND (FSub, Links, UI) ---

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = message.from_user.id
    settings = await get_settings()
    
    # Check FSubs
    if settings["fsubs"]:
        not_joined = await check_fsub(client, user_id, settings["fsubs"])
        if not_joined:
            buttons = []
            for channel in not_joined:
                buttons.append([InlineKeyboardButton(f"Join @{channel}", url=f"https://t.me/{channel}")])
            
            payload = message.command[1] if len(message.command) > 1 else ""
            if payload:
                buttons.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.me.username}?start={payload}")])
            
            return await message.reply_text(
                "<b>⚠️ You must join our channels to use this bot.</b>", 
                reply_markup=InlineKeyboardMarkup(buttons), 
                parse_mode=enums.ParseMode.HTML
            )

    # If user clicked a file link
    if len(message.command) > 1:
        code = message.command[1]
        data = await links_col.find_one({"_id": code})
        
        if not data:
            return await message.reply_text("<b>❌ Link Expired or Invalid.</b>", parse_mode=enums.ParseMode.HTML)
        
        msg = await message.reply_text("<b>⚡ Sending files... Please wait.</b>", parse_mode=enums.ParseMode.HTML)
        
        for msg_id in data["ids"]:
            try:
                sent_msg = await client.copy_message(chat_id=user_id, from_chat_id=DUMP_CHANNEL, message_id=msg_id)
                asyncio.create_task(auto_delete_task(sent_msg, 1800)) # Auto delete in 30 mins
                await asyncio.sleep(0.5)
            except Exception:
                pass
        
        return await msg.edit_text("<b>⚡ Files delivered. They will be DELETED in 30 minutes to avoid copyright. Save them!</b>", parse_mode=enums.ParseMode.HTML)

        # Normal Start Message
    sticker_msg = None
    if settings.get("start_sticker"):
        try:
            sticker_msg = await message.reply_sticker(settings["start_sticker"])
        except Exception:
            pass

    caption = settings["start_msg"].format(
        mention=message.from_user.mention, 
        first_name=message.from_user.first_name, 
        last_name=message.from_user.last_name or ""
    )
    
    img = random.choice(settings["start_imgs"]) if settings["start_imgs"] else None
    
    # --- FIXED PART START ---
    try:
        if img:
            await message.reply_photo(photo=img, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        # Agar image fail ho jaye, toh bina image ke text bhej do
        print(f"Image Error: {e}")
        await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    # --- FIXED PART END ---

    if sticker_msg:
        await asyncio.sleep(1)
        await sticker_msg.delete()

print("⚡ Advanced Black Deku Bot Started!")
app.run()
