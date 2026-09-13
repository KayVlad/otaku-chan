"""Transactional folder reconciliation; missing content keeps recoverable metadata."""
import hashlib
import time
import uuid
from pathlib import Path
from db import db

MARKER = '.otaku-id'


def folder_identity(path, renew=False):
    marker = path / MARKER
    identity = None
    if not renew:
        try:
            if not marker.is_symlink():
                identity = str(uuid.UUID(marker.read_text(encoding='ascii').strip()))
        except (OSError, ValueError, UnicodeError):
            pass
    if identity is None:
        identity = str(uuid.uuid4())
        try:
            if marker.is_symlink():
                raise OSError('Symlink identity marker')
            with marker.open('w' if renew else 'x', encoding='ascii') as stream:
                stream.write(identity)
        except OSError:
            stat = path.stat()
            identity = f'fs:{stat.st_dev}:{stat.st_ino}' if stat.st_ino else 'path:' + str(path.resolve())
    return identity


def directories(path):
    # Deliberately raise on inaccessible directories: never interpret an I/O error as deletion.
    return [p for p in path.iterdir() if not p.name.startswith('.') and not p.is_symlink() and p.is_dir() and p.resolve().parent == path.resolve()]


def scan_library(lib_id):
    from libraries import has_cover
    from config import IMAGE_EXTENSIONS
    con = db()
    lib = con.execute('SELECT * FROM libraries WHERE id=?', (lib_id,)).fetchone()
    if lib is None:
        return
    root = Path(lib['path']).resolve()
    if not root.is_dir():
        raise OSError('Library folder is unavailable; metadata was preserved.')
    # Inspect before starting the write transaction, including every volume directory.
    found = []
    for path in directories(root):
        volumes = directories(path)
        revision = hashlib.sha256()
        for volume in sorted(volumes):
            for image in sorted(volume.iterdir()):
                if image.is_file() and not image.is_symlink() and image.suffix.lower() in IMAGE_EXTENSIONS:
                    st = image.stat()
                    revision.update(f'{volume.name}/{image.name}:{st.st_size}:{st.st_mtime_ns}'.encode())
        found.append((path, folder_identity(path), [(v, folder_identity(v)) for v in volumes], has_cover(path), revision.hexdigest()))
    now = int(time.time() * 1000)
    con.execute('BEGIN IMMEDIATE')
    try:
        seen = set()
        for path, identity, volumes, cover, revision in found:
            row = con.execute('SELECT * FROM manga WHERE folder_id=?', (identity,)).fetchone()
            if row and Path(row['path']).resolve() != path and Path(row['path']).exists() and folder_identity(Path(row['path'])) == identity:
                # A copy is a new manga, not a move. Never steal the source's metadata.
                identity = folder_identity(path, renew=True)
                row = None
            by_name = con.execute('SELECT * FROM manga WHERE lib_id=? AND name=?', (lib_id, path.name)).fetchone()
            if row is None and by_name and (not by_name['folder_id'] or by_name['folder_id'] == identity):
                row = by_name
            if by_name and (row is None or by_name['id'] != row['id']):
                # Preserve a replaced folder's record without violating the legacy name uniqueness constraint.
                retired = f".missing-{by_name['id']}-{uuid.uuid4().hex}"
                con.execute('UPDATE progress SET manga=? WHERE library_id=? AND manga=?', (retired, lib_id, by_name['name']))
                con.execute('UPDATE manga SET name=?, available=0 WHERE id=?', (retired, by_name['id']))
            if row:
                mid = row['id']
                moved = row['lib_id'] != lib_id or row['name'] != path.name
                if moved:
                    con.execute('UPDATE progress SET library_id=?, manga=? WHERE library_id=? AND manga=?',
                                (lib_id, path.name, row['lib_id'], row['name']))
                con.execute('''UPDATE manga SET lib_id=?,name=?,path=?,folder_id=?,available=1,
                               volume_count=?,cover_path=?,scanned_at=?,cache_path=CASE WHEN path=? AND content_revision=? THEN cache_path ELSE NULL END,content_revision=? WHERE id=?''',
                            (lib_id, path.name, str(path), identity, len(volumes), cover, now, str(path), revision, revision, mid))
            else:
                mid = con.execute('''INSERT INTO manga(lib_id,name,path,folder_id,volume_count,cover_path,scanned_at,content_revision)
                                     VALUES(?,?,?,?,?,?,?,?)''', (lib_id,path.name,str(path),identity,len(volumes),cover,now,revision)).lastrowid
            seen.add(mid)
            prior = {v['folder_id']: v for v in con.execute('SELECT * FROM manga_volumes WHERE manga_id=?', (mid,))}
            con.execute('UPDATE manga_volumes SET available=0 WHERE manga_id=?', (mid,))
            # A replacement volume must not inherit progress from an unrelated old volume with the same name.
            incoming_ids = {vid for _, vid in volumes}
            for old in prior.values():
                if old['folder_id'] not in incoming_ids and any(v.name == old['name'] for v, _ in volumes):
                    retired = '.missing-volume-' + uuid.uuid4().hex
                    con.execute('UPDATE progress SET volume=? WHERE library_id=? AND manga=? AND volume=?',
                                (retired,lib_id,path.name,old['name']))
                    con.execute('UPDATE manga_volumes SET name=? WHERE manga_id=? AND folder_id=?', (retired,mid,old['folder_id']))
            renamed = {}
            for volume, vid in volumes:
                old = prior.get(vid)
                if old and old['name'] != volume.name and (not (path / old['name']).exists() or folder_identity(path / old['name']) != vid):
                    temporary = '.rename-' + uuid.uuid4().hex
                    con.execute('UPDATE progress SET volume=? WHERE library_id=? AND manga=? AND volume=?',
                                (temporary,lib_id,path.name,old['name']))
                    renamed[vid] = temporary
            used = set()
            for volume, vid in volumes:
                old = prior.get(vid)
                if vid in used or (old and old['name'] != volume.name and (path / old['name']).exists() and folder_identity(path / old['name']) == vid):
                    vid = folder_identity(volume, renew=True)
                    old = None
                used.add(vid)
                if old and old['name'] != volume.name:
                    con.execute('UPDATE progress SET volume=? WHERE library_id=? AND manga=? AND volume=?',
                                (volume.name,lib_id,path.name,renamed.get(vid, old['name'])))
                con.execute('''INSERT INTO manga_volumes(manga_id,folder_id,name,available) VALUES(?,?,?,1)
                               ON CONFLICT(manga_id,folder_id) DO UPDATE SET name=excluded.name,available=1''', (mid,vid,volume.name))
            current = {v.name for v, _ in volumes}
            for user in con.execute("SELECT user_id,status FROM user_manga WHERE manga_id=?", (mid,)).fetchall():
                progress = con.execute('SELECT volume,read FROM progress WHERE user_id=? AND library_id=? AND manga=?', (user['user_id'],lib_id,path.name)).fetchall()
                started = {r['volume'] for r in progress} & current
                read = {r['volume'] for r in progress if r['read']} & current
                status = 'completed' if current and current <= read else ('reading' if started else None)
                if status != user['status']:
                    con.execute("UPDATE user_manga SET status=?,updated_at=? WHERE user_id=? AND manga_id=?", (status,now,user['user_id'],mid))
        for row in con.execute('SELECT id,path FROM manga WHERE lib_id=?', (lib_id,)).fetchall():
            if row['id'] not in seen and not Path(row['path']).is_dir():
                con.execute('UPDATE manga SET available=0,cache_path=NULL WHERE id=?', (row['id'],))
        con.commit()
    except Exception:
        con.rollback()
        raise
