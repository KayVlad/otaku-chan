import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_boot = tempfile.TemporaryDirectory(prefix='otaku-ops-boot-')
os.environ['DATA_DIR'] = _boot.name
os.environ['CONTENT_CACHE_DIR'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from app import app
import db
from libraries import scan_lib
from content_cache import init_cache


class LibraryOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='otaku-ops-')
        self.root = Path(self.tmp.name)
        self.library = self.root / 'library'
        self.volume = self.library / 'Series' / 'Volume 1'
        self.volume.mkdir(parents=True)
        (self.volume / '1.png').write_bytes(b'image')
        self.old_db = db.DB_PATH
        db.DB_PATH = self.root / 'db.sqlite'
        db.init_db()
        app.config.update(TESTING=True)
        self.cache = init_cache(app, self.root / 'cache')
        self.client = app.test_client()
        self.client.post('/setup', data=dict(username='admin', password='testpass', lib_name='Library', lib_path=str(self.library)))
        with app.app_context():
            scan_lib(1)
            self.mid = db.q1('SELECT id FROM manga')['id']
            db.ex("UPDATE manga SET author='Émilie',description='Space adventure' WHERE id=?", (self.mid,))
            db.ex("INSERT INTO user_manga(user_id,manga_id,favorited,plan_to_read) VALUES(1,?,1,1)", (self.mid,))
        self.client.post('/api/progress', json=dict(library_id=1,manga='Series',volume='Volume 1',page=0,total=3))

    def tearDown(self):
        self.cache.close()
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def scan(self, lib=1):
        with app.app_context():
            scan_lib(lib)

    def row(self):
        with app.app_context():
            return dict(db.q1('SELECT * FROM manga WHERE id=?', (self.mid,)))

    def test_read_all_and_unread_clear_status_and_resume(self):
        url = f'/api/m/{self.mid}/read-all'
        result = self.client.post(url, json={'read':True}, headers={'X-Alpine-Request':'true'})
        self.assertEqual(result.status_code,200)
        self.assertIn(b'id="manga-content"',result.data)
        self.assertIn(b'status-completed',result.data)
        self.assertNotIn(b'<!DOCTYPE',result.data)
        result = self.client.post(url,json={'read':False},headers={'X-Alpine-Request':'true'})
        self.assertNotIn(b'status-completed',result.data)
        self.assertNotIn(b'status-reading',result.data)
        with app.app_context():
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM progress')['n'],0)
            row=db.q1('SELECT * FROM user_manga WHERE manga_id=?',(self.mid,))
            self.assertIsNone(row['status'])
            self.assertEqual(row['favorited'],1)
            self.assertEqual(row['plan_to_read'],1)

    def test_single_volume_unread_clears_reading_when_nothing_left(self):
        self.client.post(f'/api/m/{self.mid}/volume/Volume%201/read',json={'read':False})
        with app.app_context():
            self.assertIsNone(db.q1('SELECT status FROM user_manga')['status'])

    def test_accent_fold_and_combined_description(self):
        import json
        result=self.client.get('/l/1',query_string={'filters':json.dumps([dict(type='author',value='emilie'),dict(type='description',value='SPACE')])})
        self.assertIn(b'1 result',result.data)

    def test_series_and_volume_rename_preserve_identity_and_progress(self):
        self.volume.rename(self.volume.with_name('Chapter 01'))
        (self.library/'Series').rename(self.library/'Renamed')
        self.scan()
        self.assertEqual(self.row()['name'],'Renamed')
        self.assertEqual(self.row()['author'],'Émilie')
        with app.app_context():
            row=db.q1('SELECT * FROM progress')
            self.assertEqual((row['manga'],row['volume']),('Renamed','Chapter 01'))

    def test_move_to_another_library_preserves_metadata(self):
        other=self.root/'other';other.mkdir()
        with app.app_context():
            db.ex('INSERT INTO libraries(name,path,created_at) VALUES(?,?,0)',('Other',str(other)))
        shutil.move(str(self.library/'Series'),str(other/'Moved'))
        self.scan(2);self.scan(1)
        row=self.row()
        self.assertEqual((row['lib_id'],row['name'],row['available']),(2,'Moved',1))
        with app.app_context():
            self.assertEqual(db.q1('SELECT library_id FROM progress')['library_id'],2)

    def test_missing_folder_is_purged_and_returning_folder_is_new(self):
        outside=self.root/'temporarily-out'
        (self.library/'Series').rename(outside)
        self.scan()
        with app.app_context():
            self.assertIsNone(db.q1('SELECT * FROM manga WHERE id=?',(self.mid,)))
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM progress')['n'],0)
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM user_manga')['n'],0)
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM manga_tags')['n'],0)
        self.assertEqual(self.client.get(f'/m/{self.mid}').status_code,404)
        outside.rename(self.library/'Returned')
        self.scan()
        with app.app_context():
            returned=dict(db.q1("SELECT * FROM manga WHERE name='Returned'"))
            self.assertIsNone(returned['author'])
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM manga')['n'],1)

    def test_source_first_cross_library_move_becomes_new_without_stale_rows(self):
        other=self.root/'other';other.mkdir()
        with app.app_context():
            db.ex('INSERT INTO libraries(name,path,created_at) VALUES(?,?,0)',('Other',str(other)))
        shutil.move(str(self.library/'Series'),str(other/'Moved'))
        self.scan(1);self.scan(2)
        with app.app_context():
            moved=dict(db.q1("SELECT * FROM manga WHERE name='Moved'"))
            self.assertIsNone(moved['author'])
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM manga')['n'],1)
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM progress')['n'],0)

    def test_unavailable_and_failed_scan_preserve_rows(self):
        self.library.rename(self.root/'offline')
        with self.assertRaises(OSError):self.scan()
        self.assertEqual(self.row()['available'],1)
        (self.root/'offline').rename(self.library)
        with patch('reconcile.directories',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):self.scan()
        self.assertEqual(self.row()['author'],'Émilie')

    def test_copy_is_separate_identity(self):
        shutil.copytree(self.library/'Series',self.library/'Copy')
        self.scan()
        with app.app_context():
            copy=db.q1("SELECT * FROM manga WHERE name='Copy'")
            self.assertNotEqual(copy['folder_id'],self.row()['folder_id'])
            self.assertIsNone(copy['author'])

    def test_swapped_names_keep_metadata(self):
        other=self.library/'Other'/'V';other.mkdir(parents=True);(other/'1.png').write_bytes(b'other')
        self.scan()
        (self.library/'Series').rename(self.library/'temp')
        (self.library/'Other').rename(self.library/'Series')
        (self.library/'temp').rename(self.library/'Other')
        self.scan()
        self.assertEqual(self.row()['name'],'Other')
        self.assertEqual(self.row()['author'],'Émilie')

    def test_replaced_volume_does_not_inherit_old_progress(self):
        self.volume.rename(self.root/'old-volume')
        self.volume.mkdir()
        (self.volume/'new.png').write_bytes(b'new')
        self.scan()
        with app.app_context():
            self.assertIsNone(db.q1('SELECT status FROM user_manga')['status'])
            self.assertIsNone(db.q1("SELECT * FROM progress WHERE volume='Volume 1'"))
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM manga_volumes')['n'],1)

    def test_successful_scan_prunes_legacy_stale_rows_and_unused_tags(self):
        with app.app_context():
            db.ex("""INSERT INTO manga(lib_id,name,path,scanned_at,available,folder_id)
                   VALUES(1,'.missing-old',?,0,0,'missing-identity')""",(str(self.root/'gone'),))
            db.ex("""INSERT INTO progress(user_id,library_id,manga,volume,updated_at)
                   VALUES(1,1,'.missing-old','V',0)""")
            db.ex("INSERT INTO tags(name) VALUES('unused')")
        self.scan()
        with app.app_context():
            self.assertEqual(db.q1('SELECT COUNT(*) n FROM manga WHERE available=0')['n'],0)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM manga WHERE name LIKE '.missing-%'")['n'],0)
            self.assertIsNone(db.q1("SELECT * FROM progress WHERE manga='.missing-old'"))
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM tags WHERE name='unused'")['n'],0)

    def test_scan_transaction_rolls_back_and_database_is_consistent(self):
        (self.library/'Series').rename(self.library/'Renamed')
        with app.app_context():
            db.ex("CREATE TRIGGER fail_scan BEFORE UPDATE ON manga BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):scan_lib(1)
            self.assertEqual(db.q1('SELECT name FROM manga')['name'],'Series')
            self.assertEqual(db.q1('SELECT manga FROM progress')['manga'],'Series')
            self.assertEqual(db.q1('PRAGMA integrity_check')[0],'ok')
            self.assertEqual(db.q('PRAGMA foreign_key_check'),[])

    def test_cover_fallback_and_library_cover(self):
        for url in [f'/api/cover/{self.mid}', '/api/lib/1/cover']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data,b'image')
            response.close()

    def test_content_change_invalidates_ssd_copy(self):
        future=self.cache.ensure(self.row());future.result(timeout=10)
        self.assertIsNotNone(self.cache.lookup(self.row()))
        (self.volume/'2.png').write_bytes(b'new page')
        self.scan()
        self.assertIsNone(self.cache.lookup(self.row()))

    def test_invalid_progress_never_creates_orphan(self):
        result=self.client.post('/api/progress',json=dict(library_id=1,manga='Missing',volume='V',page=0,total=3))
        self.assertEqual(result.status_code,404)

    def archive(self, files):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name,data in files:z.writestr(name,data)
        stream.seek(0)
        return stream

    def upload_zip(self,files):
        return self.client.post('/api/lib/1/upload',data={'files':(self.archive(files),'manga.zip')})

    def test_zip_and_folder_upload_scan_without_overwriting(self):
        r=self.upload_zip([('Imported/Volume 1/1.png',b'page')]);self.assertEqual(r.status_code,200,r.data)
        self.assertTrue((self.library/'Imported'/'Volume 1'/'1.png').is_file())
        r=self.upload_zip([('Imported/Volume 1/1.png',b'overwrite')]);self.assertEqual(r.status_code,409)
        self.assertEqual((self.library/'Imported'/'Volume 1'/'1.png').read_bytes(),b'page')
        r=self.client.post('/api/lib/1/upload',data={'files':[(io.BytesIO(b'1'),'Folder/Vol/1.jpg'),(io.BytesIO(b'2'),'Folder/Vol/2.jpg')]})
        self.assertEqual(r.status_code,200,r.data)
        with app.app_context():self.assertEqual(db.q1("SELECT volume_count FROM manga WHERE name='Folder'")['volume_count'],1)

    def test_upload_rejects_traversal_bad_structure_duplicates_and_symlinks(self):
        for name in ['../escape.png','/escape.png','M/V/../../escape.png','M/V/C:evil.png','M/V/CON.png','M/page.png','M/V/script.html']:
            r=self.upload_zip([(name,b'bad')]);self.assertEqual(r.status_code,400,(name,r.data))
        r=self.upload_zip([('Dup/V/1.png',b'1'),('dup/v/1.PNG',b'2')]);self.assertEqual(r.status_code,400)
        entry=zipfile.ZipInfo('Link/V/1.png');entry.create_system=3;entry.external_attr=0o120777<<16
        r=self.upload_zip([(entry,b'../../target')]);self.assertEqual(r.status_code,400)
        self.assertFalse((self.root/'escape.png').exists())
        self.assertEqual(sorted(p.name for p in self.library.iterdir()),['Series'])

    def test_upload_size_and_library_access(self):
        with patch('uploads.MAX_BYTES',2):
            self.assertEqual(self.upload_zip([('Big/V/1.png',b'123')]).status_code,413)
        with app.app_context():
            db.ex("INSERT INTO users(username,password_hash,is_admin,created_at) VALUES('reader','x',0,0)")
        with self.client.session_transaction() as session:session['uid']=2
        self.assertEqual(self.upload_zip([('Denied/V/1.png',b'x')]).status_code,404)
        with app.app_context():db.ex('INSERT INTO library_access(user_id,library_id) VALUES(2,1)')
        self.assertEqual(self.upload_zip([('Allowed/V/1.png',b'x')]).status_code,200)

    def test_ajax_pages_and_settings_are_fragments(self):
        for url in ['/', '/l/1', f'/m/{self.mid}', f'/m/{self.mid}/Volume%201','/settings']:
            r=self.client.get(url,headers={'X-Alpine-Request':'true'})
            self.assertEqual(r.status_code,200)
            self.assertNotIn(b'<!DOCTYPE',r.data)
            for region in ['breadcrumb','main','statusbar']:
                self.assertIn(f'id="{region}"'.encode(),r.data)
        r=self.client.post('/settings/library/1/toggle-hidden',headers={'X-Alpine-Request':'true'},follow_redirects=True)
        self.assertNotIn(b'<!DOCTYPE',r.data)


if __name__=='__main__':unittest.main()
