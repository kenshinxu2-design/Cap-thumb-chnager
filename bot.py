import os
import asyncio
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Railway Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MAIN_OWNER = int(os.getenv("OWNER_ID", "6728678197"))
DB_CHANNEL = os.getenv("DB_CHANNEL") 

client = AsyncIOMotorClient(MONGO_URI)
db = client["anime_pro_db"]
col_settings = db["settings"]
col_admins = db["admins"]
col_animes = db["animes"]

async def init_db():
    if not await col_settings.find_one({"_id": "config"}):
        await col_settings.insert_one({
            "_id": "config",
            "channel": DB_CHANNEL,
            "force_join": False,
            "delete_time": 600,
            "auto_delete": True,
            "upload_msg": "⏳ **Uploading your files, please wait...**",
            "delete_msg": "⚠️ **Warning:** These files will be automatically deleted in {time} seconds. Forward them now!"
        })
    if not await col_admins.find_one({"user_id": MAIN_OWNER}):
        await col_admins.insert_one({"user_id": MAIN_OWNER})

async def is_admin(user_id):
    admin = await col_admins.find_one({"user_id": user_id})
    return bool(admin)

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

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = await col_settings.find_one({"_id": "config"})

    if not await is_joined(user_id, context):
        channel = str(config.get("channel", ""))
        link = f"https://t.me/{channel.replace('@','')}" if "@" in channel else "Join Channel"
        keyboard = [[InlineKeyboardButton("📢 Join Channel", url=link)],
                    [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]]
        await update.message.reply_text("⛔ **Access Restricted!**\nPlease join our channel to use this bot.", 
                                       reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    await update.message.reply_text(f"👋 **Hi {update.effective_user.first_name}!**\nSend me any Anime Name and I'll find it.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **Bot Help Guide**\n\n"
        "• Just type an Anime Name to search.\n"
        "• Files are forwarded from our secure DB.\n"
        "• Files auto-delete based on admin settings."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    s = await col_settings.find_one({"_id": "config"})
    text = (
        "⚙️ **Admin Settings:**\n"
        f"• **F-Sub:** `{s['channel']}` ({'ON' if s['force_join'] else 'OFF'})\n"
        f"• **Timer:** `{s['delete_time']}s`\n"
        f"• **Upload Msg:** {s['upload_msg']}\n"
        f"• **Delete Msg:** {s['delete_msg']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# --- SETTINGS MODIFICATION ---

async def set_fsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("Usage: `/set_fsub @channel`")
    val = context.args[0]
    await col_settings.update_one({"_id": "config"}, {"$set": {"channel": val, "force_join": True}})
    await update.message.reply_text(f"✅ F-Sub set to {val}")

async def set_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("Usage: `/set_time 600`")
    await col_settings.update_one({"_id": "config"}, {"$set": {"delete_time": int(context.args[0])}})
    await update.message.reply_text(f"✅ Delete time set to {context.args[0]}s")

async def set_upload_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    msg = " ".join(context.args)
    if not msg: return await update.message.reply_text("Usage: `/set_upload_msg Your Text`")
    await col_settings.update_one({"_id": "config"}, {"$set": {"upload_msg": msg}})
    await update.message.reply_text("✅ Upload message updated.")

async def set_delete_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    msg = " ".join(context.args)
    if not msg: return await update.message.reply_text("Usage: `/set_delete_msg Text with {time}`")
    await col_settings.update_one({"_id": "config"}, {"$set": {"delete_msg": msg}})
    await update.message.reply_text("✅ Delete message updated.")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER: return
    if not context.args: return await update.message.reply_text("Usage: `/add_admin ID`")
    await col_admins.update_one({"user_id": int(context.args[0])}, {"$set": {"user_id": int(context.args[0])}}, upsert=True)
    await update.message.reply_text("✅ Admin Added.")

async def del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER: return
    if not context.args: return await update.message.reply_text("Usage: `/del_admin ID`")
    await col_admins.delete_one({"user_id": int(context.args[0])})
    await update.message.reply_text("✅ Admin Removed.")

# --- ANIME LOGIC ---

async def add_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    await update.message.reply_text("✍️ Send Anime Name:")
    context.user_data["awaiting_name"] = True

async def done_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    context.user_data.clear()
    await update.message.reply_text("✅ Upload Process Finished.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if context.user_data.get("awaiting_name"):
        await col_animes.update_one({"name": text}, {"$set": {"name": text, "files": []}}, upsert=True)
        context.user_data["awaiting_name"] = False
        context.user_data["uploading_now"] = text
        await update.message.reply_text(f"✅ Adding to **{text}**. Now forward files. Type /done when finished.")
        return

    if context.user_data.get("uploading_now"):
        anime_name = context.user_data["uploading_now"]
        # Forwarding to DB_CHANNEL to keep user ID secret
        fwd = await update.message.forward(chat_id=DB_CHANNEL)
        await col_animes.update_one({"name": anime_name}, {"$push": {"files": {"chat_id": fwd.chat_id, "msg_id": fwd.message_id}}})
        return

    # Direct Search Logic
    if text and not text.startswith('/'):
        config = await col_settings.find_one({"_id": "config"})
        anime = await col_animes.find_one({"name": {"$regex": f"^{text}$", "$options": "i"}})
        if not anime: anime = await col_animes.find_one({"name": {"$regex": text, "$options": "i"}})
        
        if not anime:
            return await update.message.reply_text("❌ Anime not found.")

        status = await update.message.reply_text(config["upload_msg"], parse_mode="Markdown")
        sent_ids = []
        for f in anime["files"]:
            try:
                m = await context.bot.forward_message(chat_id=user_id, from_chat_id=f["chat_id"], message_id=f["msg_id"])
                sent_ids.append(m.message_id)
            except: continue
        
        await status.delete()
        if config["auto_delete"]:
            w = await update.message.reply_text(config["delete_msg"].replace("{time}", str(config["delete_time"])), parse_mode="Markdown")
            sent_ids.append(w.message_id)
            asyncio.create_task(delete_task(context, user_id, sent_ids, config["delete_time"]))

async def delete_task(context, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    for mid in msg_ids:
        try: await context.bot.delete_message(chat_id, mid)
        except: pass

async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("add_anime", add_anime))
    app.add_handler(CommandHandler("done", done_anime))
    app.add_handler(CommandHandler("set_fsub", set_fsub))
    app.add_handler(CommandHandler("set_time", set_time))
    app.add_handler(CommandHandler("set_upload_msg", set_upload_msg))
    app.add_handler(CommandHandler("set_delete_msg", set_delete_msg))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("del_admin", del_admin))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(lambda u,c: start(u,c), pattern="check_join"))

    print("Bot is running with full commands...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True: await asyncio.sleep(1000)

if __name__ == '__main__':
    asyncio.run(main())
