import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Fetching from Railway Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MAIN_OWNER = int(os.getenv("OWNER_ID", "6728678197"))
DEFAULT_DELETE_TIME = int(os.getenv("AUTO_DELETE_TIME", "900"))
DB_CHANNEL = os.getenv("DB_CHANNEL")

# Connect to MongoDB
if not MONGO_URI:
    print("CRITICAL ERROR: MONGO_URI is missing!")
    exit()

client = AsyncIOMotorClient(MONGO_URI)
db = client["anime_bot_database"]
col_settings = db["settings"]
col_admins = db["admins"]
col_animes = db["animes"]

# Initialize Database Settings if empty
async def init_db():
    if not await col_settings.find_one({"_id": "config"}):
        await col_settings.insert_one({
            "_id": "config",
            "channel": DB_CHANNEL,
            "force_join": False,
            "delete_time": DEFAULT_DELETE_TIME,
            "auto_delete": True,
            "upload_msg": "⏳ **Uploading your files, please wait...**",
            "delete_msg": "⚠️ **Warning:** These files will be automatically deleted in {time} seconds. Please forward them to your Saved Messages!"
        })
    if not await col_admins.find_one({"user_id": MAIN_OWNER}):
        await col_admins.insert_one({"user_id": MAIN_OWNER})

# HELPER: CHECK IF ADMIN
async def is_admin(user_id):
    admin = await col_admins.find_one({"user_id": user_id})
    return bool(admin)

# HELPER: CHECK JOIN
async def is_joined(user_id, context):
    config = await col_settings.find_one({"_id": "config"})
    channel = config.get("channel")
    if not channel or not config.get("force_join"):
        return True
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# COMMAND: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = await col_settings.find_one({"_id": "config"})
    
    if not await is_joined(user_id, context):
        channel = config.get("channel", "")
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel.replace('@', '')}")],
            [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]
        ]
        await update.message.reply_text(
            "❗ **Access Denied!**\nYou must join our channel to use this bot.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # Fetch all animes from DB
    cursor = col_animes.find({})
    animes = await cursor.to_list(length=None)
    
    keyboard = [[InlineKeyboardButton(anime["name"], callback_data=f"get_{anime['name']}")] for anime in animes]
    keyboard.append([InlineKeyboardButton("🔍 Search Anime", callback_data="search")])

    await update.message.reply_text(
        "🎬 **Welcome to the Anime Bot!**\nSelect an anime from the list below or search for it:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# COMMAND: /settings
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    s = await col_settings.find_one({"_id": "config"})
    
    text = (
        "⚙️ **Current Bot Settings (Saved in Database):**\n\n"
        f"📢 **Force Sub Channel:** {s['channel'] if s.get('channel') else 'None'} (Status: {'ON' if s['force_join'] else 'OFF'})\n"
        f"⏱ **Auto Delete Time:** {s['delete_time']} seconds\n"
        f"🗑 **Auto Delete Status:** {'ON' if s['auto_delete'] else 'OFF'}\n\n"
        f"📝 **Upload Message:** {s['upload_msg']}\n"
        f"📝 **Delete Message:** {s['delete_msg']}\n\n"
        "💡 *Use commands to change these.*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ADMIN COMMANDS
async def set_fsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/set_fsub @yourchannel` or `/set_fsub off`", parse_mode="Markdown")
        return
    val = context.args[0]
    if val.lower() == "off":
        await col_settings.update_one({"_id": "config"}, {"$set": {"force_join": False, "channel": None}})
        await update.message.reply_text("✅ Force Sub Disabled.")
    else:
        await col_settings.update_one({"_id": "config"}, {"$set": {"force_join": True, "channel": val}})
        await update.message.reply_text(f"✅ Force Sub Enabled for {val}.")

async def set_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Usage: `/set_time 900`")
        return
    await col_settings.update_one({"_id": "config"}, {"$set": {"delete_time": int(context.args[0])}})
    await update.message.reply_text(f"✅ Auto-delete time set to {context.args[0]} seconds.")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER: return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Usage: `/add_admin UserID`")
        return
    await col_admins.update_one({"user_id": int(context.args[0])}, {"$set": {"user_id": int(context.args[0])}}, upsert=True)
    await update.message.reply_text(f"✅ Admin {context.args[0]} added.")

async def del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER: return
    if not context.args or not context.args[0].isdigit(): return
    await col_admins.delete_one({"user_id": int(context.args[0])})
    await update.message.reply_text("✅ Admin removed.")

# ANIME UPLOAD SYSTEM
async def add_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    await update.message.reply_text("✍️ Send me the Name of the Anime you want to add:")
    context.user_data["awaiting_anime_name"] = True

async def done_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    if "uploading_anime" in context.user_data:
        anime = context.user_data["uploading_anime"]
        del context.user_data["uploading_anime"]
        await update.message.reply_text(f"✅ Finished uploading files for **{anime}**! You can test it by sending /start", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ You are not uploading anything right now.")

# GLOBAL MESSAGE HANDLER
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # 1. Handle Search
    if context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False
        text = update.message.text.lower() if update.message.text else ""
        
        cursor = col_animes.find({"name": {"$regex": text, "$options": "i"}})
        results_db = await cursor.to_list(length=None)
        
        results = [[InlineKeyboardButton(a["name"], callback_data=f"get_{a['name']}")] for a in results_db]
        
        if results:
            await update.message.reply_text("🔍 **Search Results:**", reply_markup=InlineKeyboardMarkup(results), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ No results found.")
        return

    # Admin Only below
    if not await is_admin(user_id): return

    # 2. Handle Anime Name Input
    if context.user_data.get("awaiting_anime_name") and update.message.text:
        anime_name = update.message.text
        # Create empty anime record in DB
        await col_animes.update_one({"name": anime_name}, {"$set": {"name": anime_name, "files": []}}, upsert=True)
        context.user_data["awaiting_anime_name"] = False
        context.user_data["uploading_anime"] = anime_name
        await update.message.reply_text(f"✅ Pack created: **{anime_name}**.\n\nNow FORWARD all files from your DB Channel here. When finished, type /done", parse_mode="Markdown")
        return

    # 3. Handle File Uploads
    if context.user_data.get("uploading_anime"):
        anime_name = context.user_data["uploading_anime"]
        new_file = {"chat_id": update.message.chat_id, "msg_id": update.message.message_id}
        await col_animes.update_one({"name": anime_name}, {"$push": {"files": new_file}})

# BUTTON HANDLER & FILE SENDER
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    config = await col_settings.find_one({"_id": "config"})
    await query.answer()

    if query.data == "check_join":
        if await is_joined(user_id, context):
            await query.edit_message_text("✅ Access Granted! Use /start to continue.")
        else:
            await query.answer("❌ You haven't joined the channel yet!", show_alert=True)

    elif query.data == "search":
        await query.message.reply_text("🔍 Send me the name of the Anime:")
        context.user_data["search_mode"] = True

    elif query.data.startswith("get_"):
        anime_name = query.data.replace("get_", "")
        anime_data = await col_animes.find_one({"name": anime_name})
        
        if not anime_data or not anime_data.get("files"):
            await query.answer("No files found!", show_alert=True)
            return

        files = anime_data["files"]

        # 1. Send "Uploading..." message
        upload_msg = await context.bot.send_message(chat_id=user_id, text=config["upload_msg"], parse_mode="Markdown")
        sent_messages = []
        
        # 2. Forward all files (Using forward_message as requested)
        for file_data in files:
            try:
                forwarded_msg = await context.bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=file_data["chat_id"],
                    message_id=file_data["msg_id"]
                )
                sent_messages.append(forwarded_msg.message_id)
            except Exception as e:
                print(f"Error forwarding message: {e}")

        # 3. Delete "Uploading..." message
        try: await upload_msg.delete()
        except: pass

        # 4. Send "Delete Warning" message
        if config.get("auto_delete"):
            del_time = config["delete_time"]
            warning_text = config["delete_msg"].replace("{time}", str(del_time))
            warning_msg = await context.bot.send_message(chat_id=user_id, text=warning_text, parse_mode="Markdown")
            
            sent_messages.append(warning_msg.message_id)
            asyncio.create_task(delete_later(context, user_id, sent_messages, del_time))

# AUTO-DELETE BACKGROUND TASK
async def delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list, delay: int):
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass

# RUN THE BOT
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("set_fsub", set_fsub))
    app.add_handler(CommandHandler("set_time", set_time))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("del_admin", del_admin))
    app.add_handler(CommandHandler("add_anime", add_anime))
    app.add_handler(CommandHandler("done", done_anime))
    
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    
    print("Database Bot is running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep bot running
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
