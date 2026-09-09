"""Optional SSD snapshots. Only complete snapshots are ever used for reading."""
import hashlib
import json
import logging
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import current_app

TTL = 30 * 24 * 60 * 60
_ENTRY = re.compile(r"manga-[0-9]+-[0-9a-f]{24}(?:\.partial)?")
log = logging.getLogger(__name__)


class ContentCache:
    def __init__(self, root, on_ready=None, on_remove=None):
        self.root = Path(root).expanduser().resolve() / "otaku-chan" if root else None
        self.on_ready = on_ready or (lambda *args: None)
        self.on_remove = on_remove or (lambda *args: None)
        self._lock = threading.RLock()
        self._jobs = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="manga-cache")
        self._stop = threading.Event()
        self._cleaner = None

    def _entry(self, manga):
        source = str(Path(manga["path"]).absolute())
        digest = hashlib.sha256(source.encode()).hexdigest()[:24]
        return self.root / f"manga-{manga['id']}-{digest}"

    def _metadata(self, entry):
        if entry.is_symlink() or entry.resolve().parent != self.root:
            return None
        try:
            meta = json.loads((entry / "entry.json").read_text())
            if not isinstance(meta, dict) or not isinstance(meta.get("copied_at"), (int, float)):
                return None
            return meta
        except (OSError, ValueError):
            return None

    def lookup(self, manga):
        if not self.root:
            return None
        entry = self._entry(manga)
        with self._lock:
            meta = self._metadata(entry)
            if not meta or time.time() - meta["copied_at"] >= TTL:
                return None
            if meta.get("source") != str(Path(manga["path"]).absolute()):
                return None
            data = entry / "data"
            if data.is_symlink() or not data.is_dir():
                return None
            return data

    def ensure(self, manga):
        """Queue at most one copy per series; never wait for disk copying."""
        if not self.root:
            return None
        manga = dict(manga)
        entry = self._entry(manga)
        with self._lock:
            if self.lookup(manga):
                return None
            if entry in self._jobs:
                return self._jobs[entry]
            future = self._pool.submit(self._copy, manga, entry)
            self._jobs[entry] = future
            future.add_done_callback(lambda _: self._finished(entry))
            return future

    def _finished(self, entry):
        with self._lock:
            self._jobs.pop(entry, None)

    def _remove(self, entry):
        # Delete only app-owned direct children of the configured cache directory.
        if (not self.root or not _ENTRY.fullmatch(entry.name)
                or entry.is_symlink() or entry.resolve().parent != self.root):
            raise ValueError("Refusing to remove a path outside the managed cache")
        if entry.exists():
            shutil.rmtree(entry)

    def _copy(self, manga, entry):
        partial = entry.with_name(entry.name + ".partial")
        try:
            source = Path(manga["path"]).resolve()
            if source == self.root or source in self.root.parents or self.root in source.parents:
                raise ValueError("Content cache and source manga must not overlap")
            if not source.is_dir():
                return
            self.root.mkdir(parents=True, exist_ok=True)
            self._remove(partial)
            # Remove expired snapshots before recopying. Never touch the HDD source.
            self._remove(entry)
            self.on_remove(str(entry / "data"))
            partial.mkdir()
            copied_at = time.time()
            def skip_links(directory, names):
                return [name for name in names if (Path(directory) / name).is_symlink()]
            shutil.copytree(source, partial / "data", ignore=skip_links)
            (partial / "entry.json").write_text(json.dumps({
                "source": str(Path(manga["path"]).absolute()), "copied_at": copied_at,
            }))
            with self._lock:
                partial.rename(entry)
            self.on_ready(manga["id"], str(Path(manga["path"]).absolute()), str(entry / "data"))
        except Exception:
            log.exception("Manga %s cache copy failed; source remains available", manga["id"])
            try:
                self._remove(partial)
            except (OSError, ValueError):
                log.exception("Could not remove incomplete cache copy")

    def cleanup(self):
        if not self.root or not self.root.is_dir():
            return
        for entry in self.root.iterdir():
            if not _ENTRY.fullmatch(entry.name):
                continue
            with self._lock:
                if entry in self._jobs or entry.with_name(entry.name.removesuffix(".partial")) in self._jobs:
                    continue
                try:
                    meta = self._metadata(entry)
                    # Orphan/incomplete entries left by a killed process are also reclaimed.
                    created = meta["copied_at"] if meta else entry.stat().st_mtime
                    if time.time() - created >= TTL:
                        self._remove(entry)
                        self.on_remove(str(entry / "data"))
                except (OSError, ValueError):
                    log.exception("Could not expire cache entry %s", entry.name)

    def start(self):
        if self.root and self._cleaner is None:
            def clean():
                while not self._stop.is_set():
                    try:
                        self.cleanup()
                    except OSError:
                        log.exception("Content cache cleanup failed")
                    self._stop.wait(3600)
            self._cleaner = threading.Thread(target=clean, name="cache-expiry", daemon=True)
            self._cleaner.start()

    def close(self):
        self._stop.set()
        if self._cleaner:
            self._cleaner.join()
        self._pool.shutdown(wait=True)


def init_cache(app, root):
    from db import ex, q1
    def ready(manga_id, source, destination):
        with app.app_context():
            row = q1("SELECT path FROM manga WHERE id=?", (manga_id,))
            if row and str(Path(row["path"]).absolute()) == source:
                ex("UPDATE manga SET cache_path=? WHERE id=?", (destination, manga_id))
    def removed(destination):
        with app.app_context():
            ex("UPDATE manga SET cache_path=NULL WHERE cache_path=?", (destination,))
    cache = ContentCache(root, ready, removed)
    app.extensions["content_cache"] = cache
    cache.start()
    return cache


def content_cache():
    return current_app.extensions["content_cache"]


def reading_path(manga, volume_name):
    from helpers import safe_path
    cached = content_cache().lookup(manga)
    if cached and safe_path(cached, volume_name).is_dir():
        return cached, False
    return Path(manga["path"]), True
