import random
import hashlib
import secrets
import shutil
import time
from pathlib import Path

import click


@click.option("--name", required=True, help="Name shown for the integration token.")
def create_api_token(name):
    """Create an admin-level integration token and print it once."""
    from db import ex
    token = "oc_" + secrets.token_urlsafe(32)
    ex("""INSERT INTO api_tokens(name,token_hash,created_at)
          VALUES(?,?,?)
          ON CONFLICT(name) DO UPDATE SET token_hash=excluded.token_hash,
              created_at=excluded.created_at,last_used_at=NULL,revoked_at=NULL""",
       (name.strip(), hashlib.sha256(token.encode("utf-8")).hexdigest(), int(time.time())))
    click.echo(token)


def seed_meta():
    from db import ex, q
    rows = q("SELECT id, name FROM manga ORDER BY name COLLATE NOCASE LIMIT 3")
    if not rows:
        print("No manga in DB — scan a library first.")
        return
    metas = [
        {
            "release_date": "2019",
            "description":  (
                "A hot-headed teenager stumbles upon a sealed relic that bonds with his soul, "
                "granting him powers that haven't been seen in centuries. Hunted by a secret "
                "order that wants the relic destroyed, he must master his abilities before the "
                "next new moon — or lose himself to them forever."
            ),
            "tags": "action, adventure, shonen, superpower",
        },
        {
            "release_date": "2020",
            "description":  (
                "Childhood friends Hana and Sou drifted apart after a painful misunderstanding "
                "in middle school. Years later they find themselves working at the same tiny "
                "bookshop in a sleepy coastal town. Neither is ready to talk about what happened "
                "— but rainy afternoons and shared closing shifts have a way of reopening old pages."
            ),
            "tags": "romance, drama, slice of life",
        },
        {
            "release_date": "2021",
            "description":  (
                "Burned out and underpaid, office drone Kenji Mori dies face-down in a spreadsheet "
                "and wakes up as a goblin in a kingdom that runs on bureaucratic mana. Armed with "
                "ten years of middle-management experience and absolutely zero combat skills, "
                "he sets out to unionise the dungeon workforce."
            ),
            "tags": "fantasy, isekai, comedy, magic",
        },
    ]
    for row, meta in zip(rows, metas):
        ex("UPDATE manga SET description=?, release_date=?, tags=? WHERE id=?",
           (meta["description"], meta["release_date"], meta["tags"], row["id"]))
        print(f"Seeded: {row['name']}")


def make_sample():
    from PIL import Image, ImageDraw
    sample_root = Path("./manga_library")
    if sample_root.exists():
        shutil.rmtree(sample_root)
    colors = ["#1a1a2e", "#16213e", "#0f3460", "#533483", "#2b2d42"]
    accent = ["#e94560", "#00d4ff", "#ff6b35", "#7bed9f", "#ffd32a"]
    for mi in range(1, 60):
        manga_name = f"Manga Title {mi:02d}"
        for ci in range(1, 30):
            volume_path = sample_root / manga_name / f"Chapter {ci:03d}"
            volume_path.mkdir(parents=True, exist_ok=True)
            for pi in range(1, 9):
                img = Image.new("RGB", (800, 1200), color=colors[mi % len(colors)])
                draw = ImageDraw.Draw(img)
                draw.rectangle([0, 0, 800, 60], fill=accent[ci % len(accent)])
                draw.text((20, 15), f"{manga_name} — Ch.{ci} p.{pi}", fill="white")
                for _ in range(8):
                    x1, y1 = random.randint(0, 700), random.randint(60, 1100)
                    draw.rectangle(
                        [x1, y1, x1 + random.randint(50, 200), y1 + random.randint(50, 200)],
                        fill=accent[random.randint(0, 4)], outline="white")
                img.save(volume_path / f"page_{pi:03d}.jpg", quality=85)
        print(f"Created {manga_name}")
    print(f"Done — {sample_root.resolve()}")
