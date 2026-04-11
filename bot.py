import os
import logging
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaVideo

# ============== CONFIGURATION ==============

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot Config
API_ID = int(os.getenv("API_ID", "12345"))  # my.telegram.org se
API_HASH = os.getenv("API_HASH", "your_api_hash")  # my.telegram.org se
BOT_TOKEN = os.getenv("BOT_TOKEN")  # BotFather se

# Admin IDs - YAHAN APNA ID DAALO
ADMIN_IDS = [6728678197]  # <-- Apna Telegram ID yahan daalo

# Default caption template (blockquote ke saath)
DEFAULT_CAPTION = """<blockquote expandable>💫 {anime_name} 💫</blockquote>
<b>‣ Episode :</b> <code>{ep}</code>
<b>‣ Season :</b> <code>{season}</code>
<b>‣ Quality :</b> <code>{quality}</code>
<b>‣ Audio :</b> {audio}

<blockquote expandable>🚀 For More Join
🔰 @KENSHIN_ANIME</blockquote>"""

# Storage
pending_videos = {}  # user_id -> list of videos
user_caption_template = {}

# ============== HELPER FUNCTIONS ==============

def is_admin(user_id):
    return user_id in ADMIN_IDS

def detect_anime_name(text, filename=""):
    """Detect anime name from caption or filename"""
    # Pattern 1: 🎬 ᴀɴɪᴍᴇ: Name
    match = re.search(r'🎬\s*ᴀɴɪᴍᴇ:\s*([^\n]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Pattern 2: 📟 Episode ... \n ... \n ... \nName
    match = re.search(r'📟\s*Episode.*?\n.*?🎧.*?\n.*?📀.*?\n([^\n]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Pattern 3: Name - Ep XX or Name SXX
    match = re.search(r'([A-Za-z][A-Za-z\s]+?)(?:\s*-\s*|\s*Ep(?:isode)?\s*\d+|\s*S\d+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # From filename
    if filename:
        clean = re.sub(r'\.(mp4|mkv|avi|mov)$', '', filename, flags=re.IGNORECASE)
        clean = re.sub(r'[_\-]', ' ', clean)
        clean = re.sub(r'\b\d{1,2}\b', '', clean)
        clean = re.sub(r'\b(S\d+|E\d+|EP\d+|Season|Episode|1080p|720p|480p|360p|4K|2160p|HDR|BluRay|x264|HEVC|AAC)\b', '', clean, flags=re.IGNORECASE)
        clean = clean.strip()
        if clean and len(clean) > 2:
            return clean
    
    return "Unknown Anime"

def detect_episode(text, filename=""):
    """Detect episode number"""
    patterns = [
        r'Episode\s*[:\-]?\s*(\d+)',
        r'Ep\s*[:\-]?\s*(\d+)',
        r'E(\d+)',
        r'⌬\s*Episode:\s*(\d+)',
        r'‣\s*Episode\s*:\s*(\d+)',
        r'📟\s*Episode\s*-\s*(\d+)',
        r'Ep\s*(\d+)\s*\[',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
    
    # From filename
    if filename:
        match = re.search(r'(?:Ep|Episode|E)[\s\-]*(\d+)', filename, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
        match = re.search(r'[E\-](\d{1,2})', filename)
        if match:
            return match.group(1).zfill(2)
        match = re.search(r'(\d{1,2})\s*\[', filename)
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
        r'S(\d+)\s+Ep',
        r'\[S(\d+)\]',
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
        match = re.search(r'Season\s*(\d+)', filename, re.IGNORECASE)
        if match:
            return match.group(1).zfill(2)
    
    return "01"

def detect_quality(text, filename=""):
    """Detect quality"""
    qualities = ['2160p', '4K', '1080p', '720p', '480p', '360p']
    combined = text + " " + filename
    
    for q in qualities:
        if q.lower() in combined.lower():
            return q
    
    match = re.search(r'(\d{3,4}p)', combined, re.IGNORECASE)
    if match:
        return match.group(1)
    
    match = re.search(r'(4K)', combined, re.IGNORECASE)
    if match:
        return '4K'
    
    if '1080' in combined:
        return '1080p'
    elif '720' in combined:
        return '720p'
    elif '480' in combined:
        return '480p'
    
    return "1080p"

def detect_audio(text):
    """Detect audio language"""
    text_lower = text.lower()
    if 'hindi' in text_lower:
        return 'Hindi Dub 🎙️ | Official'
    elif 'english' in text_lower:
        return 'English Dub'
    elif 'japanese' in text_lower:
        return 'Japanese'
    elif 'tamil' in text_lower:
        return 'Tamil'
    elif 'telugu' in text_lower:
        return 'Telugu'
    return 'Hindi Dub 🎙️ | Official'

def get_quality_priority(quality):
    """For sorting - higher number = higher quality"""
    quality_map = {
        '360p': 1, 
        '480p': 2, 
        '720p': 3, 
        '1080p': 4, 
        '4K': 5, 
        '2160p': 6
    }
    return quality_map.get(quality, 4)

# ============== BOT INITIALIZATION ==============

app = Client(
    "video_cover_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode="html"  # HTML parse mode for blockquote
)

# ============== COMMAND HANDLERS ==============

@app.on_message(filters.command("start") & filters.private)
async def start(client, message: Message):
    """Start command"""
    if not is_admin(message.from_user.id):
        await message.reply("❌ You are not authorized to use this bot!")
        return
    
    await message.reply("<blockquote>Jinda hu abhi..</blockquote>")

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message: Message):
    """Help command"""
    if not is_admin(message.from_user.id):
        return
    
    help_text = """🤖 <b>Bot Commands:</b>

<code>/start</code> - Bot start karo
<code>/help</code> - Ye help message
<code>/setcaption</code> - Custom caption template set karo
<code>/done</code> - Videos bhejna band, cover manga
<code>/cancel</code> - Sab cancel karo

<b>Placeholders:</b>
<code>{anime_name}</code> - Anime ka naam
<code>{ep}</code> - Episode number (01, 02)
<code>{season}</code> - Season number (01, 02)  
<code>{quality}</code> - Quality (480p, 720p, 1080p, 4K)
<code>{audio}</code> - Audio (Hindi Dub 🎙️ | Official)

<b>Default Caption:</b>
<blockquote expandable>""" + DEFAULT_CAPTION + """</blockquote>

<b>Kaam ka tareeka:</b>
1. Multiple videos select karke bhejo
2. Bot auto-detect karega (anime name, ep, quality)
3. Ek cover photo bhejo
4. Bot sab videos ko:
   - Naya caption laga ke
   - Cover change karke
   - Episode wise sort karke (480p→720p→1080p→4K)
   - Alag alag bhej dega!"""
    
    await message.reply(help_text)

@app.on_message(filters.command("setcaption") & filters.private)
async def setcaption_command(client, message: Message):
    """Set custom caption template"""
    if not is_admin(message.from_user.id):
        return
    
    # Get caption from message
    if len(message.command) < 2:
        await message.reply(
            "❌ Caption template bhejo!\n\n"
            "<b>Example:</b>\n"
            "<code>/setcaption &lt;blockquote expandable&gt;💫 {anime_name} 💫&lt;/blockquote&gt;\nEpisode: {ep}</code>\n\n"
            "<b>Available placeholders:</b>\n"
            "<code>{anime_name}, {ep}, {season}, {quality}, {audio}</code>"
        )
        return
    
    caption = message.text.split(None, 1)[1]
    user_caption_template[message.from_user.id] = caption
    
    # Show preview
    preview = caption.format(
        anime_name="Demon Slayer",
        ep="05",
        season="01",
        quality="1080p",
        audio="Hindi Dub 🎙️ | Official"
    )
    
    await message.reply(f"✅ <b>Caption template saved!</b>\n\n<b>Preview:</b>\n{preview}")

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client, message: Message):
    """Cancel and clear queue"""
    if not is_admin(message.from_user.id):
        return
    
    user_id = message.from_user.id
    count = 0
    
    if user_id in pending_videos:
        count = len(pending_videos[user_id])
        del pending_videos[user_id]
    
    await message.reply(f"❌ <b>Cancelled!</b> {count} videos cleared from queue.")

@app.on_message(filters.command("done") & filters.private)
async def done_command(client, message: Message):
    """Done collecting videos - ask for cover"""
    if not is_admin(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    if user_id not in pending_videos or len(pending_videos[user_id]) == 0:
        await message.reply("❌ Koi videos nahi hain! Pehle videos bhejo.")
        return
    
    videos = pending_videos[user_id]
    total = len(videos)
    
    # Show detected info
    info_text = f"🎬 <b>{total} videos detected:</b>\n\n"
    
    for v in videos[:10]:
        info_text += f"#{v['index']}: <code>{v['anime_name']}</code> | Ep{v['ep']} | {v['quality']}\n"
    
    if len(videos) > 10:
        info_text += f"... aur {len(videos) - 10} videos\n"
    
    info_text += f"\n📸 <b>Ab ek cover photo bhejo!</b>\nSab videos par yahi cover lagega."
    
    await message.reply(info_text)

# ============== VIDEO HANDLING ==============

@app.on_message(filters.video & filters.private)
async def handle_videos(client, message: Message):
    """Handle videos - add to queue"""
    if not is_admin(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    if user_id not in pending_videos:
        pending_videos[user_id] = []
    
    video = message.video
    caption = message.caption or ""
    filename = video.file_name or ""
    
    # Extract all data
    video_data = {
        'index': len(pending_videos[user_id]) + 1,
        'video_file_id': video.file_id,
        'original_caption': caption,
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
    
    # Reply every 5 videos or if less than 5 total
    if total <= 5 or total % 5 == 0:
        await message.reply(
            f"✅ <b>{total} videos in queue!</b>\n"
            f"📸 Ab ek cover bhejo ya aur videos bhejo..."
        )

@app.on_message(filters.photo & filters.private)
async def handle_cover(client, message: Message):
    """Handle cover photo and process all videos"""
    if not is_admin(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    if user_id not in pending_videos or len(pending_videos[user_id]) == 0:
        # Maybe user just sent a photo, not a cover
        return
    
    # Get cover file id (highest quality)
    cover_file_id = message.photo.file_id
    
    videos = pending_videos[user_id]
    caption_template = user_caption_template.get(user_id, DEFAULT_CAPTION)
    
    # Sort: Episode first, then Quality
    videos.sort(key=lambda x: (int(x['ep']), get_quality_priority(x['quality'])))
    
    total = len(videos)
    
    # Send processing message
    status_msg = await message.reply(
        f"⚡ <b>Processing {total} videos...</b>\n"
        f"🔄 Sorting: Episode wise → Quality wise\n"
        f"⏳ Please wait..."
    )
    
    # Process and send videos one by one
    sent = 0
    failed = 0
    
    for i, video_data in enumerate(videos, 1):
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
            await client.send_video(
                chat_id=message.chat.id,
                video=video_data['video_file_id'],
                caption=new_caption,
                duration=video_data['duration'],
                width=video_data['width'],
                height=video_data['height'],
                thumb=cover_file_id,  # Thumbnail
                supports_streaming=video_data['supports_streaming'],
                has_spoiler=video_data['has_spoiler']
            )
            sent += 1
            
            # Update status every 5 videos
            if i % 5 == 0 and i < total:
                try:
                    await status_msg.edit_text(
                        f"⚡ <b>Processing...</b> {i}/{total} done\n"
                        f"⏳ Please wait..."
                    )
                except:
                    pass
            
            # Delay to avoid flood
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Error sending video {video_data['index']}: {e}")
            failed += 1
            continue
    
    # Delete status message
    try:
        await status_msg.delete()
    except:
        pass
    
    # Final message
    if failed > 0:
        await message.reply(
            f"⚠️ <b>Completed with {failed} errors!</b>\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}\n"
            f"📝 Captions changed\n"
            f"🎬 Covers applied\n"
            f"📊 Sorted: Episode → Quality"
        )
    else:
        await message.reply(
            f"✅ <b>All {sent} videos sent!</b>\n"
            f"📝 New captions applied with blockquote\n"
            f"🎬 Covers changed\n"
            f"📊 Sorted: Episode → Quality (480p→720p→1080p→4K)\n\n"
            f"🔄 /start for new batch"
        )
    
    # Cleanup
    del pending_videos[user_id]

# ============== MAIN ==============

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not found!")
        exit(1)
    
    if not API_ID or API_ID == 12345:
        logger.error("❌ API_ID not configured!")
        exit(1)
    
    if not API_HASH or API_HASH == "your_api_hash":
        logger.error("❌ API_HASH not configured!")
        exit(1)
    
    if not ADMIN_IDS or ADMIN_IDS == [123456789]:
        logger.error("❌ ADMIN_IDS not configured! Apna Telegram ID daalo.")
        exit(1)
    
    logger.info("🤖 Bot starting with Pyrofork...")
    app.run()
