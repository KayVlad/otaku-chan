"""
Reads .env (and live environment variable overrides) and writes data/config.json.
Run once before starting the app, or in the Docker entrypoint.
"""
import json
import os
import re
from pathlib import Path

DEFAULTS = {
    "port":           5001,
    "debug":          False,
    "manga_per_page": 50,
    "home_row_size":  10,
    "continue_limit": 12,
    "cache_dir":      ".cache",
    "content_cache_dir": "",
}

# env var name -> (json key, cast function)
ENV_MAP = {
    "PORT":           ("port",           int),
    "DEBUG":          ("debug",          lambda v: v.lower() in ("1", "true", "yes")),
    "MANGA_PER_PAGE": ("manga_per_page", int),
    "HOME_ROW_SIZE":  ("home_row_size",  int),
    "CONTINUE_LIMIT": ("continue_limit", int),
    "CACHE_DIR":      ("cache_dir",      str),
    "CONTENT_CACHE_DIR": ("content_cache_dir", str),
}


def _parse_dotenv(path=".env"):
    result = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
            if m:
                result[m.group(1)] = m.group(2).strip("\"'")
    except FileNotFoundError:
        pass
    return result


def main():
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    dotenv = _parse_dotenv()
    cfg = dict(DEFAULTS)

    for env_key, (cfg_key, cast) in ENV_MAP.items():
        # live env var takes precedence over .env file
        raw = os.environ.get(env_key) or dotenv.get(env_key)
        if raw is not None:
            try:
                cfg[cfg_key] = cast(raw)
            except (ValueError, TypeError):
                pass

    out = data_dir / "config.json"
    out.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"config written → {out}")


if __name__ == "__main__":
    main()
