import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))

_cfg_path = DATA_DIR / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

DB_PATH     = DATA_DIR / "manga.db"
SECRET_PATH = DATA_DIR / "secret.key"

PORT           = _cfg.get("port",           5001)
DEBUG          = _cfg.get("debug",          False)
MANGA_PER_PAGE = _cfg.get("manga_per_page", 50)
HOME_ROW_SIZE  = _cfg.get("home_row_size",  10)
CONTINUE_LIMIT = _cfg.get("continue_limit", 12)
CACHE_DIR      = _cfg.get("cache_dir",      ".cache")  # covers only
CONTENT_CACHE_DIR = os.environ.get("CONTENT_CACHE_DIR", _cfg.get("content_cache_dir", ""))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
COVER_NAMES      = ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp")
