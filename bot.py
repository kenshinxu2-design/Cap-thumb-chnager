from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import asyncio

TOKEN = "8780999113:AAHsYS_e6MoWfEq1LOwkqoJIW-cvIYTSTcI"
OWNER_ID = "6728678197"

# SETTINGS
settings = {
    "channel": None,
    "force_join": False,
    "delete_time": 900,
    "auto_delete": True,
    "search": True   # 🔍 Search ON/OFF
}

# DATA STORAGE
data = {}

# CHECK JOIN
async def is_joined(user_id, context):
    if not settings["channel"]:
        return True
    try:
        member = await context.bot.get_chat_member(settings["channel"], user_id)
        return member.status in ["member","administrator","creator"]
    except:
        return False

# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    # Force Join
    if settings["force_join"] and settings["channel"]:
        if not await is_joined(user_id, context):
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{settings['channel'].replace('@','')}")],
                [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]
            ]
            await update.message.reply_text("❗ पहले चैनल जॉइन करो", reply_markup=InlineKeyboardMarkup(keyboard))
            return

    keyboard = []

    # Show Anime buttons
    for anime in data:
        keyboard.append([InlineKeyboardButton(anime, callback_data=anime)])

    # Search button
    if settings["search"]:
        keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search")])

    # Owner panel
    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton("➕ Add Anime", callback_data="add")])
        keyboard.append([InlineKeyboardButton("⚙️ Settings", callback_data="settings")])

    await update.message.reply_text("🎬 Select Anime", reply_markup=InlineKeyboardMarkup(keyboard))

# BUTTON HANDLER
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    # Check join
    if query.data == "check_join":
        if await is_joined(user_id, context):
            await query.message.reply_text("✅ Access Granted! /start")
        else:
            await query.message.reply_text("❌ Join first")

    # Search
    elif query.data == "search":
        await query.message.reply_text("🔍 Send anime name to search")
        context.user_data["search_mode"] = True

    # Add anime
    elif query.data == "add" and user_id == OWNER_ID:
        await query.message.reply_text("Send Anime Name")
        context.user_data["new_anime"] = True

    # Settings
    elif query.data == "settings" and user_id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("⏱ Change Time", callback_data="time")],
            [InlineKeyboardButton("📢 Set Channel", callback_data="channel")],
            [InlineKeyboardButton("❌ Remove Channel", callback_data="remove_channel")],
            [InlineKeyboardButton("🔗 Force Join ON/OFF", callback_data="force")],
            [InlineKeyboardButton("🗑 Auto Delete ON/OFF", callback_data="autodel")],
            [InlineKeyboardButton("🔍 Search ON/OFF", callback_data="search_toggle")]
        ]
        await query.message.reply_text("⚙️ Settings Panel", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "search_toggle":
        settings["search"] = not settings["search"]
        await query.message.reply_text(f"Search: {settings['search']}")

    elif query.data == "time":
        await query.message.reply_text("Send time in seconds")
        context.user_data["time"] = True

    elif query.data == "channel":
        await query.message.reply_text("Send channel username (@name)")
        context.user_data["channel"] = True

    elif query.data == "remove_channel":
        settings["channel"] = None
        settings["force_join"] = False
        await query.message.reply_text("✅ Channel Removed")

    elif query.data == "force":
        settings["force_join"] = not settings["force_join"]
        await query.message.reply_text(f"Force Join: {settings['force_join']}")

    elif query.data == "autodel":
        settings["auto_delete"] = not settings["auto_delete"]
        await query.message.reply_text(f"Auto Delete: {settings['auto_delete']}")

    # Anime open
    elif query.data in data:
        files = data[query.data]

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
    user_id = update.message.from_user.id
    text = update.message.text

    # SEARCH SYSTEM
    if context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False

        results = []
        for anime in data:
            if text.lower() in anime.lower():
                results.append([InlineKeyboardButton(anime, callback_data=anime)])

        if results:
            await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(results))
        else:
            await update.message.reply_text("❌ No results found")

        return

    if user_id != OWNER_ID:
        return

    # Add anime
    if context.user_data.get("new_anime"):
        data[text] = []
        context.user_data["anime"] = text
        context.user_data["new_anime"] = False
        context.user_data["upload"] = True
        await update.message.reply_text("Now send files")

    # Upload files
    elif context.user_data.get("upload"):
        anime = context.user_data["anime"]

        if update.message.document:
            data[anime].append(update.message.document.file_id)
        elif update.message.video:
            data[anime].append(update.message.video.file_id)
        elif update.message.photo:
            data[anime].append(update.message.photo[-1].file_id)

        await update.message.reply_text("✅ File Added")

    # Time
    elif context.user_data.get("time"):
        settings["delete_time"] = int(text)
        await update.message.reply_text("✅ Time Updated")
        context.user_data["time"] = False

    # Channel
    elif context.user_data.get("channel"):
        settings["channel"] = text
        await update.message.reply_text("✅ Channel Set")
        context.user_data["channel"] = False

# RUN
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.ALL, message_handler))

app.run_polling()
