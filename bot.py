import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Railway Variables se data uthane ke liye
BOT_TOKEN = os.getenv("BOT_TOKEN")
# OWNER_ID ko list mein convert karne ke liye (e.g., "123,456" -> [123, 456])
owner_raw = os.getenv("OWNER_ID", "")
OWNER_IDS = [int(i.strip()) for i in owner_raw.split(",") if i.strip().isdigit()]

# SETTINGS
settings = {
    "channel": None,
    "force_join": False,
    "delete_time": 900,
    "auto_delete": True,
    "search": True
}

# DATA STORAGE (Note: Railway restart hone par ye khali ho jayega)
data = {}

# CHECK JOIN
async def is_joined(user_id, context):
    if not settings["channel"]:
        return True
    try:
        member = await context.bot.get_chat_member(settings["channel"], user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if settings["force_join"] and settings["channel"]:
        if not await is_joined(user_id, context):
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{settings['channel'].replace('@','')}")],
                [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]
            ]
            await update.message.reply_text("❗ Pehle channel join karein.", reply_markup=InlineKeyboardMarkup(keyboard))
            return

    keyboard = []
    for anime in data:
        keyboard.append([InlineKeyboardButton(anime, callback_data=anime)])

    if settings["search"]:
        keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search")])

    if user_id in OWNER_IDS:
        keyboard.append([InlineKeyboardButton("➕ Add Anime", callback_data="add")])
        keyboard.append([InlineKeyboardButton("⚙️ Settings", callback_data="settings")])

    await update.message.reply_text("🎬 **Select Anime ya Search karein:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# BUTTON HANDLER
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "check_join":
        if await is_joined(user_id, context):
            await query.edit_message_text("✅ Access Granted! /start press karein.")
        else:
            await query.answer("❌ Abhi tak join nahi kiya!", show_alert=True)

    elif query.data == "search":
        await query.message.reply_text("🔍 Anime ka naam bhejein:")
        context.user_data["search_mode"] = True

    elif query.data == "add" and user_id in OWNER_IDS:
        await query.message.reply_text("Anime ka Naam likhein:")
        context.user_data["new_anime"] = True

    elif query.data == "settings" and user_id in OWNER_IDS:
        keyboard = [
            [InlineKeyboardButton("⏱ Delete Time", callback_data="time"), InlineKeyboardButton("📢 Set Channel", callback_data="channel")],
            [InlineKeyboardButton("🔗 Force Join ON/OFF", callback_data="force")],
            [InlineKeyboardButton("🗑 Auto Delete ON/OFF", callback_data="autodel")],
            [InlineKeyboardButton("🔍 Search ON/OFF", callback_data="search_toggle")],
            [InlineKeyboardButton("❌ Remove Channel", callback_data="remove_channel")]
        ]
        await query.message.reply_text("⚙️ **Settings Panel**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "search_toggle":
        settings["search"] = not settings["search"]
        await query.answer(f"Search: {'ON' if settings['search'] else 'OFF'}")

    elif query.data == "force":
        settings["force_join"] = not settings["force_join"]
        await query.answer(f"Force Join: {settings['force_join']}")

    elif query.data == "autodel":
        settings["auto_delete"] = not settings["auto_delete"]
        await query.answer(f"Auto Delete: {settings['auto_delete']}")

    elif query.data in data:
        files = data[query.data]
        if not files:
            await query.answer("Isme koi files nahi hain.", show_alert=True)
            return
            
        for file_id in files:
            msg = await query.message.reply_document(file_id)
            if settings["auto_delete"]:
                asyncio.create_task(delete_later(msg, settings["delete_time"]))

# DELETE FUNCTION
async def delete_later(msg, time):
    await asyncio.sleep(time)
    try:
        await msg.delete()
    except:
        pass

# MESSAGE HANDLER
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False
        results = [[InlineKeyboardButton(a, callback_data=a)] for a in data if text.lower() in a.lower()]
        if results:
            await update.message.reply_text("🔍 Results mil gaye:", reply_markup=InlineKeyboardMarkup(results))
        else:
            await update.message.reply_text("❌ Kuch nahi mila.")
        return

    if user_id not in OWNER_IDS:
        return

    if context.user_data.get("new_anime"):
        data[text] = []
        context.user_data["anime"] = text
        context.user_data["new_anime"] = False
        context.user_data["upload"] = True
        await update.message.reply_text(f"✅ **{text}** add ho gaya. Ab files bhejein. Jab khatam ho jaye toh /start dabayein.")

    elif context.user_data.get("upload"):
        anime = context.user_data["anime"]
        file_id = None
        if update.message.document: file_id = update.message.document.file_id
        elif update.message.video: file_id = update.message.video.file_id
        elif update.message.photo: file_id = update.message.photo[-1].file_id

        if file_id:
            data[anime].append(file_id)
            await update.message.reply_text("✅ File Added!")

# RUN
if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN variable missing in Railway!")
    else:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button))
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
        print("Bot is running...")
        app.run_polling()
