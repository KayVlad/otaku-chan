import time
from pathlib import Path
from flask import Blueprint, abort, g, jsonify, request
from config import CONTINUE_LIMIT
from db import ex, q, q1
from libraries import lib_or_404, visible_dirs

progress_bp = Blueprint("progress", __name__)


def save_progress(lib_id, manga, volume, page, total):
    now = int(time.time() * 1000)
    ex("""INSERT INTO progress (user_id, library_id, manga, volume, page, total, read, updated_at)
          VALUES (?,?,?,?,?,?,?,?)
          ON CONFLICT(user_id, library_id, manga, volume) DO UPDATE SET
            page = excluded.page,
            total = excluded.total,
            read = CASE WHEN progress.read = 1 THEN 1 ELSE excluded.read END,
            updated_at = excluded.updated_at""",
       (g.user["id"], lib_id, manga, volume, page, total,
        1 if page >= total - 1 else 0, now))
    manga_row = q1("SELECT id FROM manga WHERE lib_id=? AND name=?", (lib_id, manga))
    if manga_row:
        ex("""INSERT INTO user_manga (user_id, manga_id, status, updated_at)
              VALUES (?,?,'reading',?)
              ON CONFLICT(user_id, manga_id) DO UPDATE SET
                status = CASE WHEN user_manga.status IS NULL THEN 'reading' ELSE user_manga.status END,
                updated_at = excluded.updated_at""",
             (g.user["id"], manga_row["id"], now))
        sync_completion(manga_row["id"], lib_id, manga)


def manga_progress(lib_id, manga):
    rows = q("""SELECT * FROM progress
                WHERE user_id=? AND library_id=? AND manga=?""",
             (g.user["id"], lib_id, manga))
    by_volume = {r["volume"]: dict(r) for r in rows}
    last = max(rows, key=lambda r: r["updated_at"])["volume"] if rows else None
    return by_volume, last


def set_volume_read(lib_id, manga, volume, read):
    ex("""INSERT INTO progress (user_id, library_id, manga, volume, page, total, read, updated_at)
          VALUES (?,?,?,?,0,1,?,?)
          ON CONFLICT(user_id, library_id, manga, volume) DO UPDATE SET
            read=excluded.read, updated_at=excluded.updated_at""",
       (g.user["id"], lib_id, manga, volume, 1 if read else 0, int(time.time() * 1000)))


def sync_completion(manga_id, lib_id, manga_name):
    """Auto-set 'completed' when all volumes are read; revert to 'reading' when not."""
    manga_row = q1("SELECT path FROM manga WHERE id=?", (manga_id,))
    if not manga_row:
        return
    current = {d.name for d in visible_dirs(Path(manga_row["path"]))}
    if not current:
        return
    read = {r["volume"] for r in q(
        "SELECT volume FROM progress WHERE user_id=? AND library_id=? AND manga=? AND read=1",
        (g.user["id"], lib_id, manga_name))}
    now = int(time.time() * 1000)
    if current <= read:
        ex("""INSERT INTO user_manga (user_id, manga_id, status, updated_at)
              VALUES (?,?,'completed',?)
              ON CONFLICT(user_id, manga_id) DO UPDATE SET
                status='completed', updated_at=excluded.updated_at""",
           (g.user["id"], manga_id, now))
    else:
        ex("""UPDATE user_manga SET status='reading', updated_at=?
              WHERE user_id=? AND manga_id=? AND status='completed'""",
           (now, g.user["id"], manga_id))


def lib_progress_set(lib_id):
    return {r["manga"] for r in q(
        "SELECT DISTINCT manga FROM progress WHERE user_id=? AND library_id=?",
        (g.user["id"], lib_id))}


def continue_list(show_hidden=False):
    access_join = "" if g.user["is_admin"] else \
        "JOIN library_access a ON a.library_id = p.library_id AND a.user_id = p.user_id"
    hidden_filter = "" if show_hidden else "AND l.is_hidden=0"
    rows = q(f"""
        SELECT p.*, l.name AS lib_name, l.path AS lib_path, m.id AS manga_id
        FROM progress p
        JOIN libraries l ON l.id = p.library_id
        JOIN manga m ON m.lib_id = p.library_id AND m.name = p.manga
        LEFT JOIN user_manga um ON um.manga_id = m.id AND um.user_id = p.user_id
        {access_join}
        JOIN (SELECT library_id, manga, MAX(updated_at) mu
              FROM progress WHERE user_id=?
              GROUP BY library_id, manga) x
          ON x.library_id = p.library_id AND x.manga = p.manga AND x.mu = p.updated_at
        WHERE p.user_id=? {hidden_filter}
          AND (um.status IS NULL OR um.status != 'completed')
        ORDER BY p.updated_at DESC LIMIT ?""",
        (g.user["id"], g.user["id"], CONTINUE_LIMIT))
    return [dict(r) for r in rows if (Path(r["lib_path"]) / r["manga"]).is_dir()]


@progress_bp.route("/api/progress", methods=["POST"])
def api_progress():
    data = request.get_json(silent=True) or {}
    try:
        lib_id = int(data["library_id"])
        page = max(0, int(data["page"]))
        total = max(1, int(data["total"]))
        manga = str(data["manga"])
        volume = str(data["volume"])
    except (KeyError, ValueError, TypeError):
        abort(400)
    lib_or_404(lib_id)
    save_progress(lib_id, manga, volume, min(page, total - 1), total)
    return jsonify(ok=True)
