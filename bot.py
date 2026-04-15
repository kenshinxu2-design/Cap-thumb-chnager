"""
⚡ ULTIMATE FILE STORE BOT v3.0 ⚡
✅ PyroFork (Anti-Blockquote)
✅ Queue System
✅ Multiple Start Images
✅ Start Sticker
✅ Full Admin Controls
"""

import asyncio
import logging
import os
import random
import string
from datetime import datetime
from typing import List

from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant, FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

# ============== CONFIG ==============
class Config:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    API_ID = int(os.environ.get("API_ID", "0"))
    API_HASH = os.environ.get("API_HASH", "")
    MONGO_URI = os.environ.get("MONGO_URI", "")
    OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
    PRIVATE_CHANNEL = int(os.environ.get("PRIVATE_CHANNEL", "0"))
    
    BOT_USERNAME = None
    START_IMAGES = []
    START_MESSAGES = []
    FORCE_SUB_CHANNELS = []
    ADMINS = []
    START_STICKERS = []

config = Config()

# ============== LOGGING ==============
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============== DATABASE ==============
class Database:
    def __init__(self):
        self.client = MongoClient(config.MONGO_URI)
        self.db = self.client.ultimate_filestore
        
        self.files = self.db.files
        self.users = self.db.users
        self.banned = self.db.banned
        self.queue = self.db.queue
        self.settings = self.db.settings
        
        self.files.create_index("link_id", unique=True)
        self.users.create_index("user_id", unique=True)
        self.banned.create_index("user_id", unique=True)
        
    async def add_file(self, link_id: str, msg_ids: List[int], file_type: str, user_id: int, caption: str = "", protect: bool = False):
        try:
            self.files.insert_one({
                "link_id": link_id, "msg_ids": msg_ids, "file_type": file_type,
                "user_id": user_id, "caption": caption, "protect": protect,
                "clicks": 0, "created_at": datetime.now()
            })
            return True
        except DuplicateKeyError:
            return False
    
    async def get_file(self, link_id: str):
        return self.files.find_one({"link_id": link_id})
    
    async def delete_file(self, link_id: str):
        return self.files.delete_one({"link_id": link_id}).deleted_count > 0
    
    async def increment_clicks(self, link_id: str):
        self.files.update_one({"link_id": link_id}, {"$inc": {"clicks": 1}})
    
    async def add_to_queue(self, user_id: int, file_data: dict):
        self.queue.insert_one({
            "user_id": user_id, "file_id": file_data.get("file_id"),
            "file_type": file_data.get("type"), "caption": file_data.get("caption"),
            "added_at": datetime.now()
        })
    
    async def get_user_queue(self, user_id: int):
        return list(self.queue.find({"user_id": user_id}).sort("added_at", ASCENDING))
    
    async def clear_queue(self, user_id: int):
        self.queue.delete_many({"user_id": user_id})
    
    async def get_queue_count(self, user_id: int):
        return self.queue.count_documents({"user_id": user_id})
    
    async def add_user(self, user_id: int, username: str, first_name: str):
        try:
            self.users.insert_one({
                "user_id": user_id, "username": username, "first_name": first_name,
                "joined": datetime.now(), "last_active": datetime.now()
            })
        except DuplicateKeyError:
            self.users.update_one({"user_id": user_id}, {"$set": {"last_active": datetime.now(), "username": username}})
    
    async def ban_user(self, user_id: int, reason: str, banned_by: int):
        try:
            self.banned.insert_one({"user_id": user_id, "reason": reason, "banned_by": banned_by, "banned_at": datetime.now()})
            return True
        except DuplicateKeyError:
            return False
    
    async def unban_user(self, user_id: int):
        return self.banned.delete_one({"user_id": user_id}).deleted_count > 0
    
    async def is_banned(self, user_id: int):
        return self.banned.find_one({"user_id": user_id}) is not None
    
    async def get_stats(self):
        return {
            "users": self.users.count_documents({}),
            "files": self.files.count_documents({}),
            "banned": self.banned.count_documents({}),
            "queue": self.queue.count_documents({})
        }
    
    async def get_all_users(self):
        return list(self.users.find({}, {"user_id": 1}))
    
    async def load_settings(self):
        settings = self.settings.find_one({"_id": "main"}) or {}
        config.START_IMAGES = settings.get("start_images", [])
        config.START_MESSAGES = settings.get("start_messages", ["👋 Welcome!"])
        config.FORCE_SUB_CHANNELS = settings.get("force_sub_channels", [])
        config.ADMINS = settings.get("admins", [config.OWNER_ID])
        config.START_STICKERS = settings.get("start_stickers", [])
    
    async def save_settings(self):
        self.settings.update_one(
            {"_id": "main"},
            {"$set": {
                "start_images": config.START_IMAGES, "start_messages": config.START_MESSAGES,
                "force_sub_channels": config.FORCE_SUB_CHANNELS, "admins": config.ADMINS,
                "start_stickers": config.START_STICKERS
            }}, upsert=True
        )
    
    async def add_start_image(self, file_id: str):
        if file_id not in config.START_IMAGES:
            config.START_IMAGES.append(file_id)
            await self.save_settings()
            return True
        return False
    
    async def remove_start_image(self, file_id: str):
        if file_id in config.START_IMAGES:
            config.START_IMAGES.remove(file_id)
            await self.save_settings()
            return True
        return False
    
    async def add_start_message(self, text: str):
        if text not in config.START_MESSAGES:
            config.START_MESSAGES.append(text)
            await self.save_settings()
            return True
        return False
    
    async def add_sticker(self, file_id: str):
        if file_id not in config.START_STICKERS:
            config.START_STICKERS.append(file_id)
            await self.save_settings()
            return True
        return False
    
    async def add_force_sub(self, channel_id: int):
        if channel_id not in config.FORCE_SUB_CHANNELS:
            config.FORCE_SUB_CHANNELS.append(channel_id)
            await self.save_settings()
            return True
        return False
    
    async def remove_force_sub(self, channel_id: int):
        if channel_id in config.FORCE_SUB_CHANNELS:
            config.FORCE_SUB_CHANNELS.remove(channel_id)
            await self.save_settings()
            return True
        return False
    
    async def add_admin(self, user_id: int):
        if user_id not in config.ADMINS:
            config.ADMINS.append(user_id)
            await self.save_settings()
            return True
        return False
    
    async def remove_admin(self, user_id: int):
        if user_id in config.ADMINS and user_id != config.OWNER_ID:
            config.ADMINS.remove(user_id)
            await self.save_settings()
            return True
        return False

db = Database()

# ============== BOT ==============
app = Client("ultimate_filestore", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN, workers=200, parse_mode=enums.ParseMode.HTML)

# ============== HELPERS ==============
def generate_link_id(length: int = 10) -> str:
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(length))

def is_admin(user_id: int) -> bool:
    return user_id == config.OWNER_ID or user_id in config.ADMINS

async def check_force_sub(bot: Client, user_id: int) -> bool:
    if not config.FORCE_SUB_CHANNELS:
        return True
    for channel in config.FORCE_SUB_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]:
                return False
        except UserNotParticipant:
            return False
        except Exception:
            continue
    return True

def get_force_sub_markup():
    buttons = []
    for i, ch in enumerate(config.FORCE_SUB_CHANNELS, 1):
        buttons.append([InlineKeyboardButton(f"📢 Join Channel {i}", url=f"https://t.me/c/{str(ch)[4:]}" if str(ch).startswith('-100') else f"https://t.me/{ch}")])
    buttons.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)

async def delete_after(msg, seconds: int):
    if seconds <= 0:
        return
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except:
        pass

# ============== START ==============
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(bot: Client, msg):
    user_id = msg.from_user.id
    
    if not config.BOT_USERNAME:
        me = await bot.get_me()
        config.BOT_USERNAME = me.username
        await db.load_settings()
    
    if await db.is_banned(user_id):
        return await msg.reply("🚫 Banned!")
    
    await db.add_user(user_id, msg.from_user.username, msg.from_user.first_name)
    
    if not await check_force_sub(bot, user_id):
        text = "⚠️ <b>Join Required Channels!</b>"
        if config.START_IMAGES:
            return await msg.reply_photo(photo=random.choice(config.START_IMAGES), caption=text, reply_markup=get_force_sub_markup())
        return await msg.reply(text, reply_markup=get_force_sub_markup())
    
    if len(msg.command) > 1:
        await send_stored_file(bot, msg, msg.command[1])
        return
    
    # Sticker
    sticker_msg = None
    if config.START_STICKERS:
        try:
            sticker_msg = await bot.send_sticker(user_id, random.choice(config.START_STICKERS))
        except:
            pass
    
    # Start message
    start_text = random.choice(config.START_MESSAGES) if config.START_MESSAGES else "👋 Welcome!"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Updates", url="https://t.me/updates")],
        [InlineKeyboardButton("❓ Help", callback_data="help"), InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
    ])
    
    if config.START_IMAGES:
        main_msg = await msg.reply_photo(photo=random.choice(config.START_IMAGES), caption=start_text, reply_markup=buttons)
    else:
        main_msg = await msg.reply(start_text, reply_markup=buttons)
    
    if sticker_msg:
        asyncio.create_task(delete_after(sticker_msg, 3))

@app.on_callback_query(filters.regex("check_sub"))
async def check_sub_cb(bot: Client, query: CallbackQuery):
    if await check_force_sub(bot, query.from_user.id):
        await query.answer("✅ Verified!", show_alert=True)
        await query.message.delete()
        await start_cmd(bot, query.message)
    else:
        await query.answer("❌ Join all channels!", show_alert=True)

# ============== FILE SENDING ==============
async def send_stored_file(bot: Client, msg: Message, link_id: str):
    user_id = msg.from_user.id
    
    if await db.is_banned(user_id):
        return await msg.reply("🚫 Banned!")
    
    if not await check_force_sub(bot, user_id):
        return await msg.reply("⚠️ Join channels!", reply_markup=get_force_sub_markup())
    
    file_data = await db.get_file(link_id)
    if not file_data:
        return await msg.reply("❌ Invalid link!")
    
    await db.increment_clicks(link_id)
    msg_ids = file_data.get("msg_ids", [])
    
    if not msg_ids:
        return await msg.reply("❌ File not found!")
    
    sent = []
    for msg_id in msg_ids:
        try:
            copied = await bot.copy_message(chat_id=user_id, from_chat_id=config.PRIVATE_CHANNEL, message_id=msg_id)
            sent.append(copied)
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.error(f"Send error: {e}")
            continue
    
    if sent:
        await msg.reply(f"✅ Sent {len(sent)} file(s)!")
    else:
        await msg.reply("❌ Could not send files!")

# ============== GENLINK ==============
@app.on_message(filters.command("genlink") & filters.private)
async def genlink_cmd(bot: Client, msg: Message):
    user_id = msg.from_user.id
    
    if not msg.reply_to_message:
        return await msg.reply("📎 Reply to a file with /genlink")
    
    reply = msg.reply_to_message
    if not (reply.video or reply.document or reply.photo or reply.audio):
        return await msg.reply("❌ No file found!")
    
    processing = await msg.reply("⏳ Processing...")
    
    try:
        forwarded = await reply.forward(config.PRIVATE_CHANNEL)
        link_id = generate_link_id()
        await db.add_file(link_id, [forwarded.message_id], "single", user_id, reply.caption)
        
        link = f"https://t.me/{config.BOT_USERNAME}?start={link_id}"
        
        await processing.edit_text(
            f"✅ <b>Link Generated!</b>\n\n🔗 <code>{link}</code>\n\n📊 Type: Single File",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open", url=link)],
                [InlineKeyboardButton("📤 Share", url=f"https://t.me/share/url?url={link}")]
            ])
        )
    except Exception as e:
        await processing.edit_text(f"❌ Error: {e}")

# ============== QUEUE SYSTEM ==============
@app.on_message((filters.video | filters.document | filters.photo | filters.audio) & filters.private)
async def add_to_queue_handler(bot: Client, msg: Message):
    user_id = msg.from_user.id
    
    if not is_admin(user_id):
        return
    
    if not await check_force_sub(bot, user_id):
        return
    
    file_type = "document"
    if msg.video:
        file_type = "video"
    elif msg.photo:
        file_type = "photo"
    elif msg.audio:
        file_type = "audio"
    
    await db.add_to_queue(user_id, {"file_id": msg.id, "type": file_type, "caption": msg.caption or ""})
    count = await db.get_queue_count(user_id)
    
    asyncio.create_task(delete_after(msg, 2))
    confirm = await msg.reply(f"📥 Added! Total: {count}")
    asyncio.create_task(delete_after(confirm, 3))

@app.on_message(filters.command("process") & filters.private)
async def process_queue(bot: Client, msg: Message):
    user_id = msg.from_user.id
    
    if not is_admin(user_id):
        return await msg.reply("⚠️ Admins only!")
    
    queue_items = await db.get_user_queue(user_id)
    if not queue_items:
        return await msg.reply("📭 Queue empty!")
    
    if len(queue_items) < 2:
        return await msg.reply("⚠️ Need 2+ files!")
    
    processing = await msg.reply(f"⏳ Processing {len(queue_items)} files...")
    
    try:
        msg_ids = []
        for item in queue_items:
            try:
                forwarded = await bot.copy_message(chat_id=config.PRIVATE_CHANNEL, from_chat_id=user_id, message_id=item.get("file_id", 0))
                if forwarded:
                    msg_ids.append(forwarded.message_id)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Queue process error: {e}")
                continue
        
        if not msg_ids:
            return await processing.edit_text("❌ No files processed!")
        
        batch_id = generate_link_id(12)
        await db.add_file(batch_id, msg_ids, "batch", user_id, f"Batch of {len(msg_ids)} files", protect=True)
        await db.clear_queue(user_id)
        
        link = f"https://t.me/{config.BOT_USERNAME}?start={batch_id}"
        
        await processing.edit_text(
            f"✅ <b>Batch Created!</b>\n\n📊 Files: {len(msg_ids)}\n🔗 <code>{link}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Batch", url=link)]])
        )
    except Exception as e:
        await processing.edit_text(f"❌ Error: {e}")

@app.on_message(filters.command("clearqueue") & filters.private)
async def clear_queue_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await db.clear_queue(msg.from_user.id)
    await msg.reply("✅ Queue cleared!")

@app.on_message(filters.command("queue") & filters.private)
async def show_queue(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    count = await db.get_queue_count(msg.from_user.id)
    await msg.reply(f"📥 Queue: {count} files\n\nUse /process to create batch")

# ============== ADMIN COMMANDS ==============
@app.on_message(filters.command("set_start_img") & filters.private)
async def set_start_img(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Owner only!")
    
    if not msg.reply_to_message or not msg.reply_to_message.photo:
        return await msg.reply("📷 Reply to a photo!")
    
    file_id = msg.reply_to_message.photo.file_id
    
    if await db.add_start_image(file_id):
        await msg.reply(f"✅ Added! Total: {len(config.START_IMAGES)}")
    else:
        await msg.reply("⚠️ Already exists!")

@app.on_message(filters.command("del_start_img") & filters.private)
async def del_start_img(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /del_start_img [file_id]")
    
    if await db.remove_start_image(msg.command[1]):
        await msg.reply("✅ Removed!")
    else:
        await msg.reply("❌ Not found!")

@app.on_message(filters.command("list_start_img") & filters.private)
async def list_start_img(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if not config.START_IMAGES:
        return await msg.reply("📭 No images!")
    
    text = f"📷 <b>Images ({len(config.START_IMAGES)}):</b>\n\n"
    for i, img in enumerate(config.START_IMAGES, 1):
        text += f"{i}. <code>{img[:30]}...</code>\n"
    
    await msg.reply(text)

@app.on_message(filters.command("set_start_msg") & filters.private)
async def set_start_msg(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /set_start_msg [text]")
    
    text = " ".join(msg.command[1:])
    
    if await db.add_start_message(text):
        await msg.reply(f"✅ Added! Total: {len(config.START_MESSAGES)}")
    else:
        await msg.reply("⚠️ Already exists!")

@app.on_message(filters.command("add_sticker") & filters.private)
async def add_sticker(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if not msg.reply_to_message or not msg.reply_to_message.sticker:
        return await msg.reply("😊 Reply to a sticker!")
    
    if await db.add_sticker(msg.reply_to_message.sticker.file_id):
        await msg.reply(f"✅ Added! Total: {len(config.START_STICKERS)}")
    else:
        await msg.reply("⚠️ Already exists!")

@app.on_message(filters.command("set_force_sub") & filters.private)
async def set_force_sub(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /set_force_sub [channel_id/username]")
    
    try:
        chat = await bot.get_chat(msg.command[1])
        if await db.add_force_sub(chat.id):
            await msg.reply(f"✅ Added! Total: {len(config.FORCE_SUB_CHANNELS)}")
        else:
            await msg.reply("⚠️ Already exists!")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

@app.on_message(filters.command("del_force_sub") & filters.private)
async def del_force_sub(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /del_force_sub [channel_id]")
    
    try:
        channel_id = int(msg.command[1])
        if await db.remove_force_sub(channel_id):
            await msg.reply("✅ Removed!")
        else:
            await msg.reply("❌ Not found!")
    except:
        await msg.reply("❌ Invalid ID!")

@app.on_message(filters.command("list_force_sub") & filters.private)
async def list_force_sub(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if not config.FORCE_SUB_CHANNELS:
        return await msg.reply("📭 No channels!")
    
    text = f"📢 <b>Channels ({len(config.FORCE_SUB_CHANNELS)}):</b>\n\n"
    for i, ch in enumerate(config.FORCE_SUB_CHANNELS, 1):
        text += f"{i}. <code>{ch}</code>\n"
    
    await msg.reply(text)

@app.on_message(filters.command("add_admin") & filters.private)
async def add_admin(bot: Client, msg: Message):
    if msg.from_user.id != config.OWNER_ID:
        return await msg.reply("🚫 Owner only!")
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /add_admin [user_id]")
    
    try:
        user_id = int(msg.command[1])
        if await db.add_admin(user_id):
            await msg.reply(f"✅ Added admin: <code>{user_id}</code>")
        else:
            await msg.reply("⚠️ Already admin!")
    except:
        await msg.reply("❌ Invalid ID!")

@app.on_message(filters.command("remove_admin") & filters.private)
async def remove_admin(bot: Client, msg: Message):
    if msg.from_user.id != config.OWNER_ID:
        return await msg.reply("🚫 Owner only!")
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /remove_admin [user_id]")
    
    try:
        user_id = int(msg.command[1])
        if user_id == config.OWNER_ID:
            return await msg.reply("❌ Cannot remove owner!")
        
        if await db.remove_admin(user_id):
            await msg.reply(f"✅ Removed: <code>{user_id}</code>")
        else:
            await msg.reply("❌ Not found!")
    except:
        await msg.reply("❌ Invalid ID!")

@app.on_message(filters.command("list_admins") & filters.private)
async def list_admins(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    text = f"👑 <b>Owner:</b> <code>{config.OWNER_ID}</code>\n\n👥 <b>Admins ({len(config.ADMINS)}):</b>\n"
    for admin in config.ADMINS:
        text += f"• <code>{admin}</code> {'👑' if admin == config.OWNER_ID else ''}\n"
    
    await msg.reply(text)

# ============== OTHER COMMANDS ==============
@app.on_message(filters.command("genbatch") & filters.private)
async def genbatch_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Admins only!")
    
    if len(msg.command) < 4:
        return await msg.reply("Usage: /genbatch [channel] [start_id] [end_id]")
    
    try:
        channel = msg.command[1]
        start_id = int(msg.command[2])
        end_id = int(msg.command[3])
        
        if end_id - start_id > 200:
            return await msg.reply("⚠️ Max 200!")
        
        processing = await msg.reply("⏳ Processing...")
        
        msg_ids = []
        for msg_id in range(start_id, end_id + 1):
            try:
                copied = await bot.copy_message(chat_id=config.PRIVATE_CHANNEL, from_chat_id=channel, message_id=msg_id)
                if copied:
                    msg_ids.append(copied.message_id)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Batch error {msg_id}: {e}")
                continue
        
        if not msg_ids:
            return await processing.edit_text("❌ No files!")
        
        batch_id = generate_link_id(12)
        await db.add_file(batch_id, msg_ids, "batch", msg.from_user.id, f"Batch of {len(msg_ids)} files")
        
        link = f"https://t.me/{config.BOT_USERNAME}?start={batch_id}"
        
        await processing.edit_text(f"✅ <b>Batch Created!</b>\n\n📊 Files: {len(msg_ids)}\n🔗 <code>{link}</code>")
        
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if not msg.reply_to_message:
        return await msg.reply("Reply to a message!")
    
    reply = await msg.reply("⏳ Broadcasting...")
    
    users = await db.get_all_users()
    success = failed = 0
    
    for user in users:
        try:
            await msg.reply_to_message.copy(user["user_id"])
            success += 1
            await asyncio.sleep(0.1)
        except:
            failed += 1
    
    await reply.edit_text(f"✅ Done!\n\nSuccess: {success}\nFailed: {failed}")

@app.on_message(filters.command("ban") & filters.private)
async def ban_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /ban [user_id] [reason]")
    
    try:
        user_id = int(msg.command[1])
        reason = " ".join(msg.command[2:]) if len(msg.command) > 2 else "No reason"
        
        if await db.ban_user(user_id, reason, msg.from_user.id):
            await msg.reply(f"✅ Banned: <code>{user_id}</code>")
        else:
            await msg.reply("⚠️ Already banned!")
    except:
        await msg.reply("❌ Invalid ID!")

@app.on_message(filters.command("unban") & filters.private)
async def unban_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    if len(msg.command) < 2:
        return await msg.reply("Usage: /unban [user_id]")
    
    try:
        user_id = int(msg.command[1])
        if await db.unban_user(user_id):
            await msg.reply(f"✅ Unbanned: <code>{user_id}</code>")
        else:
            await msg.reply("❌ Not found!")
    except:
        await msg.reply("❌ Invalid ID!")

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(bot: Client, msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    stats = await db.get_stats()
    await msg.reply(f"📊 <b>STATS</b>\n\n👥 Users: {stats['users']:,}\n📁 Files: {stats['files']:,}\n📥 Queue: {stats['queue']:,}\n🚫 Banned: {stats['banned']:,}")

@app.on_message(filters.command("help") & filters.private)
async def help_cmd(bot: Client, msg: Message):
    help_text = """
<b>📚 COMMANDS</b>

<b>User:</b>
/start - Start bot / Access files
/help - This help

<b>Admin:</b>
/genlink - Store single file
/genbatch - Store from channel
/queue - View queue
/process - Process queue
/clearqueue - Clear queue

<b>Settings:</b>
/set_start_img - Add start image
/set_start_msg - Add message
/add_sticker - Add sticker
/set_force_sub - Add force sub
/del_force_sub - Remove force sub
/add_admin - Add admin
/remove_admin - Remove admin

<b>Management:</b>
/broadcast - Broadcast
/ban /unban - User control
/stats - Statistics
"""
    await msg.reply(help_text)

@app.on_callback_query(filters.regex("help"))
async def help_cb(bot: Client, query: CallbackQuery):
    await help_cmd(bot, query.message)

@app.on_callback_query(filters.regex("settings"))
async def settings_cb(bot: Client, query: CallbackQuery):
    if not is_admin(query.from_user.id):
        return await query.answer("Admins only!", show_alert=True)
    
    text = f"⚙️ <b>SETTINGS</b>\n\n📷 Images: {len(config.START_IMAGES)}\n💬 Messages: {len(config.START_MESSAGES)}\n😊 Stickers: {len(config.START_STICKERS)}\n📢 Force Subs: {len(config.FORCE_SUB_CHANNELS)}\n👥 Admins: {len(config.ADMINS)}"
    await query.message.edit_text(text)

if __name__ == "__main__":
    logger.info("🚀 Starting Ultimate File Store Bot v3.0")
    app.run()
