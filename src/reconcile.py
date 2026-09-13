"""Reconcile database metadata against one authoritative library directory."""
import hashlib
import time
import uuid
from pathlib import Path

from db import db

MARKER = ".otaku-id"


def folder_identity(path, renew=False):
    marker = path / MARKER
    identity = None
    if not renew:
        try:
            if not marker.is_symlink():
                identity = str(uuid.UUID(marker.read_text(encoding="ascii").strip()))
        except (OSError, ValueError, UnicodeError):
            pass
    if identity is None:
        identity = str(uuid.uuid4())
        try:
            if marker.is_symlink():
                raise OSError("Symlink identity marker")
            with marker.open("w" if renew else "x", encoding="ascii") as stream:
                stream.write(identity)
        except OSError:
            stat = path.stat()
            identity = f"fs:{stat.st_dev}:{stat.st_ino}" if stat.st_ino else "path:" + str(path.resolve())
    return identity


def directories(path):
    # Raise on inaccessible directories: an I/O failure must never look like an empty library.
    root = path.resolve()
    return [item for item in path.iterdir()
            if not item.name.startswith(".") and not item.is_symlink()
            and item.is_dir() and item.resolve().parent == root]


def _purge_manga(con, row):
    con.execute("DELETE FROM progress WHERE library_id=? AND manga=?", (row["lib_id"], row["name"]))
    con.execute("DELETE FROM manga WHERE id=?", (row["id"],))


def _match_manga(con, lib_id, path, identity):
    row = con.execute("SELECT * FROM manga WHERE folder_id=?", (identity,)).fetchone()
    if row and Path(row["path"]).resolve() != path:
        old_path = Path(row["path"])
        if old_path.is_dir() and folder_identity(old_path) == identity:
            # The source still exists, so this is a copy rather than a move.
            identity = folder_identity(path, renew=True)
            row = None
    if row is None:
        row = con.execute(
            "SELECT * FROM manga WHERE lib_id=? AND name=? AND folder_id IS NULL", (lib_id, path.name)
        ).fetchone()
    return row, identity


def _reconcile_volumes(con, manga_id, lib_id, manga_name, volumes):
    prior = {row["folder_id"]: row for row in con.execute(
        "SELECT * FROM manga_volumes WHERE manga_id=?", (manga_id,)
    )}
    resolved = []
    used = set()
    for path, identity in volumes:
        old = prior.get(identity)
        if identity in used or (old and old["name"] != path.name
                                and (path.parent / old["name"]).is_dir()
                                and folder_identity(path.parent / old["name"]) == identity):
            identity = folder_identity(path, renew=True)
            old = None
        used.add(identity)
        resolved.append((path, identity, old))

    current_ids = {identity for _, identity, _ in resolved}
    for identity, old in prior.items():
        if identity not in current_ids:
            con.execute("DELETE FROM progress WHERE library_id=? AND manga=? AND volume=?",
                        (lib_id, manga_name, old["name"]))
            con.execute("DELETE FROM manga_volumes WHERE manga_id=? AND folder_id=?", (manga_id, identity))

    # Temporary progress names make A↔B volume swaps safe under the composite key.
    temporary = {}
    for path, identity, old in resolved:
        if old and old["name"] != path.name:
            name = ".rename-volume-" + uuid.uuid4().hex
            con.execute("UPDATE progress SET volume=? WHERE library_id=? AND manga=? AND volume=?",
                        (name, lib_id, manga_name, old["name"]))
            temporary[identity] = name
    for path, identity, old in resolved:
        if old and old["name"] != path.name:
            con.execute("UPDATE progress SET volume=? WHERE library_id=? AND manga=? AND volume=?",
                        (path.name, lib_id, manga_name, temporary[identity]))
        con.execute("""INSERT INTO manga_volumes(manga_id,folder_id,name,available) VALUES(?,?,?,1)
                       ON CONFLICT(manga_id,folder_id) DO UPDATE SET name=excluded.name,available=1""",
                    (manga_id, identity, path.name))


def _sync_statuses(con, manga_id, lib_id, manga_name, current, now):
    for user in con.execute("SELECT user_id,status FROM user_manga WHERE manga_id=?", (manga_id,)).fetchall():
        progress = con.execute(
            "SELECT volume,read FROM progress WHERE user_id=? AND library_id=? AND manga=?",
            (user["user_id"], lib_id, manga_name),
        ).fetchall()
        started = {row["volume"] for row in progress} & current
        read = {row["volume"] for row in progress if row["read"]} & current
        status = "completed" if current and current <= read else ("reading" if started else None)
        if status != user["status"]:
            con.execute("UPDATE user_manga SET status=?,updated_at=? WHERE user_id=? AND manga_id=?",
                        (status, now, user["user_id"], manga_id))


def scan_library(lib_id):
    from config import IMAGE_EXTENSIONS
    from libraries import has_cover

    con = db()
    lib = con.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if lib is None:
        return
    root = Path(lib["path"]).resolve()
    if not root.is_dir():
        raise OSError("Library folder is unavailable; database was left unchanged.")

    # Complete filesystem reads first. If storage fails, the database is untouched.
    found = []
    for path in directories(root):
        volume_paths = directories(path)
        revision = hashlib.sha256()
        for volume in sorted(volume_paths):
            for image in sorted(volume.iterdir()):
                if image.is_file() and not image.is_symlink() and image.suffix.lower() in IMAGE_EXTENSIONS:
                    stat = image.stat()
                    revision.update(f"{volume.name}/{image.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        found.append((path, folder_identity(path),
                      [(volume, folder_identity(volume)) for volume in volume_paths],
                      has_cover(path), revision.hexdigest()))

    assignments = []
    for path, identity, volumes, cover, revision in found:
        row, identity = _match_manga(con, lib_id, path, identity)
        assignments.append((path, identity, volumes, cover, revision, row))

    now = int(time.time() * 1000)
    con.execute("BEGIN IMMEDIATE")
    try:
        assigned_ids = {item[5]["id"] for item in assignments if item[5]}
        # A successful scan is authoritative. Purge records no longer represented on disk.
        for row in con.execute("SELECT * FROM manga WHERE lib_id=?", (lib_id,)).fetchall():
            if row["id"] not in assigned_ids:
                _purge_manga(con, row)

        temporary = {}
        for path, _, _, _, _, row in assignments:
            if row and (row["lib_id"] != lib_id or row["name"] != path.name):
                name = ".rename-manga-" + uuid.uuid4().hex
                con.execute("UPDATE progress SET manga=? WHERE library_id=? AND manga=?",
                            (name, row["lib_id"], row["name"]))
                con.execute("UPDATE manga SET name=? WHERE id=?", (name, row["id"]))
                temporary[row["id"]] = (row["lib_id"], name)

        for path, identity, volumes, cover, revision, row in assignments:
            if row:
                manga_id = row["id"]
                old_lib, old_name = temporary.get(manga_id, (row["lib_id"], row["name"]))
                if old_lib != lib_id or old_name != path.name:
                    con.execute("UPDATE progress SET library_id=?,manga=? WHERE library_id=? AND manga=?",
                                (lib_id, path.name, old_lib, old_name))
                con.execute("""UPDATE manga SET lib_id=?,name=?,path=?,folder_id=?,available=1,
                               volume_count=?,cover_path=?,scanned_at=?,
                               cache_path=CASE WHEN path=? AND content_revision=? THEN cache_path ELSE NULL END,
                               content_revision=? WHERE id=?""",
                            (lib_id, path.name, str(path), identity, len(volumes), cover, now,
                             str(path), revision, revision, manga_id))
            else:
                manga_id = con.execute("""INSERT INTO manga(
                    lib_id,name,path,folder_id,volume_count,cover_path,scanned_at,content_revision,available)
                    VALUES(?,?,?,?,?,?,?,?,1)""",
                    (lib_id, path.name, str(path), identity, len(volumes), cover, now, revision)).lastrowid
            _reconcile_volumes(con, manga_id, lib_id, path.name, volumes)
            _sync_statuses(con, manga_id, lib_id, path.name, {path.name for path, _ in volumes}, now)

        # Clean leftovers from older versions and remove tags no manga uses anymore.
        con.execute("""DELETE FROM progress WHERE library_id=? AND NOT EXISTS (
                    SELECT 1 FROM manga WHERE manga.lib_id=progress.library_id AND manga.name=progress.manga)""",
                    (lib_id,))
        con.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM manga_tags WHERE tag_id=tags.id)")
        con.commit()
    except Exception:
        con.rollback()
        raise
