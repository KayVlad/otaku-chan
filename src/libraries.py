from pathlib import Path
from flask import g, abort
from config import IMAGE_EXTENSIONS, CACHE_DIR, COVER_NAMES, MANGA_PER_PAGE
from db import q, q1, ex
from helpers import natural_sort_key, safe_iterdir, safe_path
import time

def scan_lib(lib_id: int):
    row = q1("SELECT * FROM libraries WHERE id=?", (lib_id,))
    if row is None:
        return
    
    root = Path(row["path"])
    if not root.is_dir():
        return
    
    existing = {r["name"]: r for r in q(
        "SELECT * FROM manga WHERE lib_id=?", (lib_id,))}
    
    on_disk = {d.name for d in visible_dirs(root)}
    
    deleted = set(existing.keys()) - on_disk
    if deleted:
        ex("DELETE FROM manga WHERE lib_id=? AND name IN ({})".format(
            ",".join("?" * len(deleted))),
           (lib_id, *deleted))
    
    now = int(time.time() * 1000)
    for name in on_disk:
        manga_path = root / name
        chapters = visible_dirs(manga_path)
        volume_count = len(chapters)
        cover_path = has_cover(manga_path)

        ex("""INSERT INTO manga (lib_id, name, path, volume_count, cover_path, scanned_at)
              VALUES (?,?,?,?,?,?)
              ON CONFLICT(lib_id, name) DO UPDATE SET
                volume_count = excluded.volume_count,
                cover_path   = excluded.cover_path,
                scanned_at   = excluded.scanned_at""",
           (lib_id, name, str(manga_path), volume_count, cover_path, now))
   
def visible_dirs(path: Path):
    return sorted(
        (d for d in safe_iterdir(path) if d.is_dir() and not d.name.startswith(".")),
        key=lambda p: natural_sort_key(p.name),
    )

def volume_images(volume_path: Path):
    return sorted(
        (f for f in safe_iterdir(volume_path)
         if f.is_file() and not f.name.startswith(".")
         and f.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda p: natural_sort_key(p.name),
    )


def first_image(manga_path: Path):
    for vol in visible_dirs(manga_path):
        imgs = volume_images(vol)
        if imgs:
            return imgs[0]
    return None

def has_cover(manga_path: Path) -> bool:
    cache = manga_path / CACHE_DIR
    return any((cache / n).exists() for n in COVER_NAMES) or first_image(manga_path) is not None


def get_library_page(lib_id: int, page: int, user_id: int = None):
    total = q1("SELECT COUNT(*) c FROM manga WHERE lib_id=?", (lib_id,))['c']
    total_pages = max(1, (total + MANGA_PER_PAGE - 1) // MANGA_PER_PAGE)
    page = max(1, min(page, total_pages))
    if user_id:
        rows = q("""SELECT m.*, um.status, um.favorited, um.bookmarked,
                           um.plan_to_read, um.dropped
                    FROM manga m
                    LEFT JOIN user_manga um ON um.manga_id = m.id AND um.user_id = ?
                    WHERE m.lib_id=? ORDER BY m.name COLLATE NOCASE LIMIT ? OFFSET ?""",
                 (user_id, lib_id, MANGA_PER_PAGE, (page - 1) * MANGA_PER_PAGE))
    else:
        rows = q("SELECT * FROM manga WHERE lib_id=? ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?",
                 (lib_id, MANGA_PER_PAGE, (page - 1) * MANGA_PER_PAGE))
    return rows, total, total_pages, page



def get_volumes(path: Path):
    if not path.is_dir():
        abort(404)
    volumes = []
    for d in visible_dirs(path):
        imgs = volume_images(d)
        volumes.append({
            "name": d.name,
            "image_count": len(imgs),
            "filenames": [f.stem for f in imgs],
        })
    return volumes


def get_images(manga_id: int, manga_path: Path, volume_name: str):
    volume_path = safe_path(manga_path, volume_name)
    if not volume_path.is_dir():
        abort(404)
    return [f"/img/{manga_id}/{volume_name}/{f.name}"
            for f in volume_images(volume_path)]


def accessible_libraries(show_hidden=False):
    if g.user["is_admin"]:
        sql = "SELECT * FROM libraries"
        if not show_hidden:
            sql += " WHERE is_hidden=0"
        rows = q(sql + " ORDER BY name COLLATE NOCASE")
    else:
        sql = """SELECT l.* FROM libraries l
                 JOIN library_access a ON a.library_id = l.id
                 WHERE a.user_id = ?"""
        if not show_hidden:
            sql += " AND l.is_hidden=0"
        rows = q(sql + " ORDER BY l.name COLLATE NOCASE", (g.user["id"],))
    return [dict(r, exists=Path(r["path"]).is_dir()) for r in rows]


def manga_or_404(manga_id: int):
    row = q1("""SELECT m.*, l.name AS lib_name, l.path AS lib_path
                FROM manga m JOIN libraries l ON l.id = m.lib_id
                WHERE m.id=?""", (manga_id,))
    if row is None:
        abort(404)
    if not g.user["is_admin"]:
        if q1("SELECT 1 FROM library_access WHERE user_id=? AND library_id=?",
              (g.user["id"], row["lib_id"])) is None:
            abort(404)
    return row

def lib_or_404(lib_id: int):
    row = q1("SELECT * FROM libraries WHERE id=?", (lib_id,))
    if row is None:
        abort(404)
    if not g.user["is_admin"]:
        if q1("SELECT 1 FROM library_access WHERE user_id=? AND library_id=?",
              (g.user["id"], lib_id)) is None:
            abort(404)
    return row, Path(row["path"])
