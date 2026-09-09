import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Isolate app initialization from any real database/cache.
_boot = tempfile.TemporaryDirectory(prefix="otaku-cache-boot-")
os.environ["DATA_DIR"] = _boot.name
os.environ["CONTENT_CACHE_DIR"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app import app
import db
from content_cache import ContentCache, TTL, init_cache
from libraries import scan_lib


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otaku-cache-test-")
        self.root = Path(self.tmp.name).resolve()
        db.DB_PATH = self.root / "test.db"
        db.init_db()
        app.config.update(TESTING=True)
        self.cache = init_cache(app, self.root / "ssd")
        self.source = self.root / "hdd" / "Series"
        self.volume = self.source / "Volume 1"
        self.volume.mkdir(parents=True)
        (self.volume / "001.png").write_bytes(b"original")
        self.client = app.test_client()
        self.client.post("/setup", data={"username": "admin", "password": "testpass",
            "lib_name": "Library", "lib_path": str(self.source.parent)})
        with app.app_context():
            scan_lib(1)
            self.manga = dict(db.q1("SELECT * FROM manga"))
        self.mid = self.manga["id"]
        self.reader = f"/m/{self.mid}/Volume%201"
        self.image = f"/img/{self.mid}/Volume%201/001.png"

    def tearDown(self):
        self.cache.close()
        self.tmp.cleanup()

    def get(self, url):
        response = self.client.get(url)
        response.get_data()
        response.close()
        return response

    def copied(self):
        future = self.cache.ensure(self.manga)
        if future:
            future.result(timeout=10)
        return self.cache.lookup(self.manga)

    def expire(self):
        marker = self.cache._entry(self.manga) / "entry.json"
        meta = json.loads(marker.read_text())
        meta["copied_at"] = time.time() - TTL - 1
        marker.write_text(json.dumps(meta))

    def test_disabled_and_detail_do_not_copy(self):
        self.assertEqual(self.get(f"/m/{self.mid}").status_code, 200)
        self.assertIsNone(self.cache.lookup(self.manga))
        self.cache.root = None
        self.assertEqual(self.get(self.reader).status_code, 200)
        self.assertIsNone(self.cache.ensure(self.manga))
        self.assertEqual(self.get(self.image).data, b"original")

    def test_first_read_never_waits_or_switches_to_completed_copy(self):
        entered, release = threading.Event(), threading.Event()
        import shutil
        real = shutil.copytree
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return real(*args, **kwargs)
        with patch("content_cache.shutil.copytree", side_effect=blocked):
            try:
                start = time.monotonic()
                response = self.get(self.reader)
                self.assertLess(time.monotonic() - start, 2)
                self.assertTrue(entered.wait(2))
                self.assertIn(b"source=1", response.data)
                self.assertIsNone(self.cache.lookup(self.manga))
                self.assertEqual(self.get(self.image + "?source=1").data, b"original")
            finally:
                release.set()
            self.cache._pool.submit(lambda: None).result(timeout=10)
        cached = self.cache.lookup(self.manga)
        self.assertIsNotNone(cached)
        (self.volume / "001.png").write_bytes(b"source changed")
        self.assertEqual(self.get(self.image + "?source=1").data, b"source changed")
        self.assertEqual(self.get(self.image).data, b"original")
        self.assertNotIn(b"source=1", self.get(self.reader).data)
        with app.app_context():
            self.assertEqual(db.q1("SELECT cache_path FROM manga")["cache_path"], str(cached))

    def test_simultaneous_opens_share_one_copy(self):
        entered, release = threading.Event(), threading.Event()
        import shutil
        real = shutil.copytree
        def blocked(*args, **kwargs):
            entered.set(); release.wait(5)
            return real(*args, **kwargs)
        with patch("content_cache.shutil.copytree", side_effect=blocked):
            try:
                first = self.cache.ensure(self.manga)
                self.assertTrue(entered.wait(2))
                second = self.cache.ensure(self.manga)
                self.assertIs(first, second)
            finally:
                release.set()
            first.result(timeout=10)

    def test_cached_reads_do_not_recopy_or_renew(self):
        self.copied()
        marker = self.cache._entry(self.manga) / "entry.json"
        original = marker.read_text()
        with patch("content_cache.shutil.copytree", side_effect=AssertionError("recopy")):
            self.assertIsNone(self.cache.ensure(self.manga))
            self.assertEqual(self.get(self.reader).status_code, 200)
        self.assertEqual(marker.read_text(), original)

    def test_expiry_falls_back_then_repopulates(self):
        old = self.copied()
        self.expire()
        (self.volume / "001.png").write_bytes(b"new source")
        self.assertIsNone(self.cache.lookup(self.manga))
        self.assertEqual(self.get(self.image).data, b"new source")
        self.assertEqual(self.get(self.reader).status_code, 200)
        self.cache._pool.submit(lambda: None).result(timeout=10)
        self.assertEqual((old / "Volume 1" / "001.png").read_bytes(), b"new source")
        self.assertIsNotNone(self.cache.lookup(self.manga))

    def test_cleanup_expires_without_read_and_preserves_source(self):
        cached = self.copied()
        self.expire()
        unrelated = self.cache.root / "keep-me"
        unrelated.mkdir()
        self.cache.cleanup()
        self.assertFalse(cached.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual((self.volume / "001.png").read_bytes(), b"original")
        with app.app_context():
            self.assertIsNone(db.q1("SELECT cache_path FROM manga")["cache_path"])

    def test_restart_reuses_timestamp_and_snapshot(self):
        cached = self.copied()
        self.cache.close()
        self.cache = init_cache(app, self.root / "ssd")
        self.assertEqual(self.cache.lookup(self.manga), cached)
        self.assertIsNone(self.cache.ensure(self.manga))

    def test_failed_copy_is_not_published_and_can_retry(self):
        with patch("content_cache.shutil.copytree", side_effect=OSError("SSD full")):
            with self.assertLogs("content_cache", level="ERROR"):
                self.cache.ensure(self.manga).result(timeout=10)
        self.assertIsNone(self.cache.lookup(self.manga))
        self.assertEqual(self.get(self.image).data, b"original")
        self.assertIsNotNone(self.copied())

    def test_interrupted_partial_is_replaced(self):
        partial = self.cache._entry(self.manga).with_name(self.cache._entry(self.manga).name + ".partial")
        partial.mkdir(parents=True)
        (partial / "unfinished").write_bytes(b"partial")
        self.assertIsNone(self.cache.lookup(self.manga))
        self.assertIsNotNone(self.copied())
        self.assertFalse(partial.exists())

    def test_cached_image_does_not_inspect_source_disk(self):
        self.copied()
        import media
        real_safe_path = media.safe_path
        def guard(root, *parts):
            self.assertNotEqual(root, self.source)
            return real_safe_path(root, *parts)
        with patch("media.safe_path", side_effect=guard):
            self.assertEqual(self.get(self.image).data, b"original")

    def test_missing_ssd_file_falls_back(self):
        cached = self.copied()
        (cached / "Volume 1" / "001.png").unlink()
        self.assertEqual(self.get(self.image).data, b"original")

    def test_new_volume_reopens_completion_and_reads_source(self):
        self.client.post("/api/progress", json={"library_id": 1, "manga": "Series",
            "volume": "Volume 1", "page": 0, "total": 1})
        with app.app_context():
            self.assertEqual(db.q1("SELECT status FROM user_manga")["status"], "completed")
        cached = self.copied()
        new = self.source / "Volume 2"
        new.mkdir(); (new / "001.png").write_bytes(b"new volume")
        with app.app_context():
            scan_lib(1)
            self.assertEqual(db.q1("SELECT status FROM user_manga")["status"], "reading")
        response = self.get(f"/m/{self.mid}/Volume%202")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"source=1", response.data)
        self.assertEqual(self.get(f"/img/{self.mid}/Volume%202/001.png").data, b"new volume")
        self.assertFalse((cached / "Volume 2").exists())

    def test_cache_does_not_bypass_access(self):
        self.copied()
        self.client.post("/settings/user/add", data={"username": "other", "password": "testpass"})
        self.get("/logout")
        self.client.post("/login", data={"username": "other", "password": "testpass"})
        self.assertEqual(self.get(self.image).status_code, 404)
        self.assertEqual(self.get(self.reader).status_code, 404)

    def test_special_names_use_encoded_image_urls(self):
        (self.volume / "002#page.png").write_bytes(b"special")
        response = self.get(f"/api/m/{self.mid}/Volume%201/images")
        url = next(url for url in response.json if "002" in url)
        self.assertIn("%23", url)
        self.assertEqual(self.get(url).data, b"special")

    def test_removal_refuses_nonmanaged_paths(self):
        with self.assertRaises(ValueError):
            self.cache._remove(self.source)
        self.assertTrue(self.source.exists())

    def test_overlap_does_not_copy_or_delete_source(self):
        self.cache.root = self.source / "nested-cache"
        with self.assertLogs("content_cache", level="ERROR"):
            self.cache.ensure(self.manga).result(timeout=10)
        self.assertEqual((self.volume / "001.png").read_bytes(), b"original")
        self.assertIsNone(self.cache.lookup(self.manga))


if __name__ == "__main__":
    unittest.main()
