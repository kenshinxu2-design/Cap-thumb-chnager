import os
import logging
import asyncio
import re
from telegram import Update, InputMediaVideo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States
COLLECTING_VIDEOS = 1
WAITING_FOR_COVER = 2

# Storage
pending_videos = {}
user_cover = {}
user_caption_template = {}

# Admin IDs (set kar lena)
ADMIN_IDS = [123456789]  # Apna Telegram ID yahan daal

# Default caption template
DEFAULT_CAPTION = """<b><blockquote>💫 {anime_name} 💫</blockquote>
‣ Episode : {ep}
‣ Season : {season}
‣ Quality : {quality}
‣ Audio : {audio}     
━━━━━━━━━━━━━━━━━━━━━
<blockquote>🚀 For More Join     
🔰 [@KENSHIN_ANIME]</blockquote>    
━━━━━━━━━━━━━━━━━━━━━</b>"""

BOT_TOKEN = os.getenv("BOT_TOKEN")

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ============== PATTERN DETECTION ==============

def detect_anime_name(text, filename=""):
    """Detect anime name from caption or filename"""
    patterns = [
        r'🎬\s*ᴀɴɪᴍᴇ:\s*([^\n]+)',
        r'📟\s*Episode.*?\n.*?🎧.*?\n.*?📀.*?\n([^\n]+)',
        r'([A-Za-z\s]+)(?:\s*-\s*|\s*Ep(?:isode)?\s*\d+)',
        r'([A-Za-z\s]+)(?:\s*S\d+|\s*-\s*\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # From filename
    if filename:
        # Remove extension and common words
        clean = re.sub(r'\.(mp4|mkv|avi)$', '', filename, flags=re.IGNORECASE)
        clean = re.sub(r'[_\-]', ' ', clean)
        clean = re.sub(r'\d+', '', clean)
        clean = re.sub(r'(S\d+|E\d+|EP\d+|Season|Episode|1080p|720p|480p|4K|2160p)', '', clean, flags=re.IGNORECASE)
        return clean.strip() or "Unknown Anime"
    
    return "Unknown Anime"

def detect_episode(text, filename=""):
    """Detect episode number"""
    patterns = [
        r'Episode\s*[:\-]?\s*(\d+)',
        r'Ep\s*[:\-]?\s*(\d+)',
        r'E(\d+)',
        r'⌬\s*Episode:\s*(\d+)',
        r'‣\s*Episode\s*:\s*(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)  # 01, 02 format
    
    # From filename
    if filename:
        match = re.search(r'(?:Ep|Episode|E)[\s\-]*(\d+)', filename, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
    
    return "01"

def detect_season(text, filename=""):
    """Detect season number"""
    patterns = [
        r'Season\s*[:\-]?\s*(\d+)',
        r'S(\d+)',
        r'⌬\s*Season:\s*(\d+)',
        r'‣\s*Season\s*:\s*(\d+)',
        r'\(\s*S(\d+)\s*\)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
    
    # From filename
    if filename:
        match = re.search(r'S(\d+)', filename, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
    
    return "01"

def detect_quality(text, filename=""):
    """Detect quality"""
    qualities = ['2160p', '4K', '1080p', '720p', '480p', '360p']
    
    for q in qualities:
        if q.lower() in text.lower() or q.lower() in filename.lower():
            return q
    
    # Check patterns
    patterns = [
        r'(\d{3,4}p)',
        r'(4K)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text + " " + filename, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return "1080p"

def detect_audio(text):
    """Detect audio language"""
    if 'hindi' in text.lower():
        return 'Hindi Dub 🎙️ | Official'
    elif 'english' in text.lower():
        return 'English Dub'
    elif 'japanese' in text.lower():
        return 'Japanese'
    return 'Hindi Dub 🎙️ | Official'

def get_quality_priority(quality):
    """For sorting - higher number = higher quality"""
    quality_map = {
        '360p': 1, '480p': 2, '720p': 3, 
        '1080p': 4, '4K': 5, '2160p': 6
    }
    return quality_map.get(quality, 4)

# ============== COMMANDS ==============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    await update.message.reply_text(
        "<blockquote>Jinda hu abhi..</blockquote>",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    if not is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text("""🤖 **Commands:**

/start - Bot start
/help - Ye message
/setcaption - Caption template set karo
/done - Videos bhejna band, cover manga

**Placeholders:**
{anime_name} - Anime ka naam
{ep} - Episode number (01, 02)
{season} - Season number (01, 02)  
{quality} - Quality (480p, 720p, 1080p, 4K)
{audio} - Audio (Hindi Dub 🎙️ | Official)

**Default Caption:**
""" + DEFAULT_CAPTION, parse_mode=ParseMode.MARKDOWN)

async def setcaption_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set custom caption template"""
    if not is_admin(update.effective_user.id):
        return
    
    # Get caption from message
    if not context.args:
        await update.message.reply_text(
            "❌ Caption template bhejo!\n\n"
            "Example:\n"
            "/setcaption <b>{anime_name}</b>\n"
            "Episode: {ep}\n"
            "Quality: {quality}"
        )
        return
    
    caption = " ".join(context.args)
    user_caption_template[update.effective_user.id] = caption
    
    await update.message.reply_text(
        "✅ Caption template saved!\n\n"
        "Preview:\n" + caption.replace('{anime_name}', 'Test Anime')
                         .replace('{ep}', '01')
                         .replace('{season}', '01')
                         .replace('{quality}', '1080p')
                         .replace('{audio}', 'Hindi Dub 🎙️ | Official'),
        parse_mode=ParseMode.HTML
    )

# ============== VIDEO HANDLING ==============

async def handle_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multiple videos"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in pending_videos:
        pending_videos[user_id] = []
        context.user_data['collecting'] = True
    
    # Handle media group
    if update.message.media_group_id:
        if context.user_data.get('current_group') != update.message.media_group_id:
            context.user_data['current_group'] = update.message.media_group_id
            context.user_data['group_count'] = 0
        
        if update.message.video:
            video = update.message.video
            caption = update.message.caption if update.message.caption else ""
            filename = video.file_name if video.file_name else ""
            
            # Extract data
            video_data = {
                'index': len(pending_videos[user_id]) + 1,
                'video_file_id': video.file_id,
                'caption': caption,
                'caption_entities': update.message.caption_entities if update.message.caption_entities else [],
                'filename': filename,
                'anime_name': detect_anime_name(caption, filename),
                'ep': detect_episode(caption, filename),
                'season': detect_season(caption, filename),
                'quality': detect_quality(caption, filename),
                'audio': detect_audio(caption),
                'width': video.width,
                'height': video.height,
                'duration': video.duration,
                'supports_streaming': getattr(video, 'supports_streaming', True),
                'has_spoiler': getattr(video, 'has_spoiler', False)
            }
            pending_videos[user_id].append(video_data)
            context.user_data['group_count'] += 1
        
        return COLLECTING_VIDEOS
    
    # Single video
    if update.message.video:
        video = update.message.video
        caption = update.message.caption if update.message.caption else ""
        filename = video.file_name if video.file_name else ""
        
        video_data = {
            'index': len(pending_videos[user_id]) + 1,
            'video_file_id': video.file_id,
            'caption': caption,
            'caption_entities': update.message.caption_entities if update.message.caption_entities else [],
            'filename': filename,
            'anime_name': detect_anime_name(caption, filename),
            'ep': detect_episode(caption, filename),
            'season': detect_season(caption, filename),
            'quality': detect_quality(caption, filename),
            'audio': detect_audio(caption),
            'width': video.width,
            'height': video.height,
            'duration': video.duration,
            'supports_streaming': getattr(video, 'supports_streaming', True),
            'has_spoiler': getattr(video, 'has_spoiler', False)
        }
        pending_videos[user_id].append(video_data)
        
        total = len(pending_videos[user_id])
        
        if context.user_data.get('group_count', 0) > 1:
            count = context.user_data['group_count']
            await update.message.reply_text(f"✅ {count} videos added! Total: {total}")
            context.user_data['group_count'] = 0
            context.user_data.pop('current_group', None)
        else:
            await update.message.reply_text(f"✅ Video #{total} added! Total: {total}")
        
        return COLLECTING_VIDEOS
    
    return COLLECTING_VIDEOS

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Done collecting videos"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in pending_videos or len(pending_videos[user_id]) == 0:
        await update.message.reply_text("❌ Koi videos nahi!")
        return ConversationHandler.END
    
    total = len(pending_videos[user_id])
    
    # Show detected info
    videos = pending_videos[user_id]
    info_text = f"🎬 **{total} videos detected:**\n\n"
    
    for v in videos[:5]:  # Show first 5
        info_text += f"#{v['index']}: {v['anime_name']} Ep{v['ep']} {v['quality']}\n"
    
    if len(videos) > 5:
        info_text += f"... aur {len(videos) - 5} videos\n"
    
    info_text += f"\n📸 **Ab ek cover bhejo!**"
    
    await update.message.reply_text(info_text)
    return WAITING_FOR_COVER

async def handle_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cover and process all videos"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in pending_videos or len(pending_videos[user_id]) == 0:
        await update.message.reply_text("❌ Pehle videos bhejo!")
        return COLLECTING_VIDEOS
    
    if not update.message.photo:
        await update.message.reply_text("❌ Photo bhejo cover ke liye!")
        return WAITING_FOR_COVER
    
    cover_file_id = update.message.photo[-1].file_id
    videos = pending_videos[user_id]
    
    # Get user's caption template or default
    caption_template = user_caption_template.get(user_id, DEFAULT_CAPTION)
    
    # Sort videos: First by Episode, then by Quality (480p→720p→1080p→4K)
    videos.sort(key=lambda x: (int(x['ep']), get_quality_priority(x['quality'])))
    
    total = len(videos)
    await update.message.reply_text(f"⚡ Processing {total} videos...\nSorting: Episode wise → Quality wise")
    
    # Send videos one by one with new caption and cover
    sent = 0
    for video_data in videos:
        try:
            # Generate new caption
            new_caption = caption_template.format(
                anime_name=video_data['anime_name'],
                ep=video_data['ep'],
                season=video_data['season'],
                quality=video_data['quality'],
                audio=video_data['audio']
            )
            
            # Send video with new caption and cover
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video_data['video_file_id'],
                caption=new_caption,
                parse_mode=ParseMode.HTML,
                duration=video_data['duration'],
                width=video_data['width'],
                height=video_data['height'],
                thumbnail=cover_file_id,
                cover=cover_file_id,
                supports_streaming=video_data['supports_streaming'],
                has_spoiler=video_data['has_spoiler']
            )
            sent += 1
            
            # Small delay
            await asyncio.sleep(0.2)
            
        except Exception as e:
            logger.error(f"Error sending video {video_data['index']}: {e}")
            await update.message.reply_text(f"❌ Video #{video_data['index']} failed!")
    
    # Cleanup
    del pending_videos[user_id]
    context.user_data.clear()
    
    # Success message
    await update.message.reply_text(
        f"✅ **{sent}/{total} videos sent!**\n"
        f"📝 New captions applied\n"
        f"🎬 Covers changed\n"
        f"📊 Sorted: Episode → Quality (480p→720p→1080p→4K)\n\n"
        f"🔄 /start for new batch"
    )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    count = 0
    
    if user_id in pending_videos:
        count = len(pending_videos[user_id])
        del pending_videos[user_id]
    
    context.user_data.clear()
    await update.message.reply_text(f"❌ {count} videos cancelled!")

def main():
    if not BOT_TOKEN:
        logger.error("No BOT_TOKEN!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO, handle_videos)],
        states={
            COLLECTING_VIDEOS: [
                MessageHandler(filters.VIDEO, handle_videos),
                MessageHandler(filters.PHOTO, handle_cover),
                CommandHandler('done', done_command),
            ],
            WAITING_FOR_COVER: [
                MessageHandler(filters.PHOTO, handle_cover),
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("setcaption", setcaption_command))
    application.add_handler(conv_handler)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
