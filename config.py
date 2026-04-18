import os

class Config:
    API_ID            = int(os.environ.get("API_ID", 0))
    API_HASH          = os.environ.get("API_HASH", "")
    BOT_TOKEN         = os.environ.get("BOT_TOKEN", "")
    DB_CHANNEL        = int(os.environ.get("DB_CHANNEL", 0))
    OWNER_ID          = int(os.environ.get("OWNER_ID", 0))
    MONGO_URI         = os.environ.get("MONGO_URI", "")
    AUTO_DELETE_TIME  = int(os.environ.get("AUTO_DELETE_TIME", 600))
