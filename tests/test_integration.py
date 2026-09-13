import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


_boot = tempfile.TemporaryDirectory(prefix="otaku-integration-boot-")
os.environ["DATA_DIR"] = _boot.name
os.environ["CONTENT_CACHE_DIR"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app import app
import db
from libraries import scan_lib


class IntegrationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otaku-integration-")
        self.root = Path(self.tmp.name)
        volume = self.root / "library" / "Series" / "Chapter 1"
        volume.mkdir(parents=True)
        (volume / "001.jpg").write_bytes(b"image")
        self.old_db = db.DB_PATH
        db.DB_PATH = self.root / "db.sqlite"
        db.init_db()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.client.post("/setup", data={
            "username": "admin", "password": "testpass",
            "lib_name": "Library", "lib_path": str(self.root / "library"),
        })
        with app.app_context():
            scan_lib(1)
            db.ex("INSERT INTO api_tokens(name,token_hash,created_at) VALUES(?,?,?)",
                  ("test", hashlib.sha256(b"secret-token").hexdigest(), int(time.time())))
            self.manga_id = db.q1("SELECT id FROM manga")["id"]
        self.headers = {"Authorization": "Bearer secret-token"}

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_requires_token_and_lists_catalog(self):
        self.assertEqual(self.client.get("/api/v1/status").status_code, 401)
        status = self.client.get("/api/v1/status", headers=self.headers)
        self.assertEqual(status.json, {"api_version": 1, "service": "otaku-chan"})
        libraries = self.client.get("/api/v1/libraries", headers=self.headers).json["libraries"]
        self.assertEqual(libraries[0]["name"], "Library")
        manga = self.client.get("/api/v1/libraries/1/manga", headers=self.headers).json["manga"]
        self.assertEqual(manga[0]["name"], "Series")
        self.assertNotIn("path", manga[0])

    def test_updates_metadata(self):
        result = self.client.patch(
            f"/api/v1/manga/{self.manga_id}/metadata",
            headers=self.headers,
            json={"author": "Author", "description": "Description", "release_date": "2024",
                  "tags": ["Adventure", "Completed"]},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["manga"]["author"], "Author")
        self.assertEqual(result.json["manga"]["tags"], ["Adventure", "Completed"])


if __name__ == "__main__":
    unittest.main()
