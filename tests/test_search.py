import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from html import unescape
import re

_boot = tempfile.TemporaryDirectory(prefix='otaku-search-boot-')
os.environ['DATA_DIR'] = _boot.name
os.environ['CONTENT_CACHE_DIR'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from app import app
import db
from flask import jsonify


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='otaku-search-test-')
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / 'test.db'
        db.init_db()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.client.post('/setup', data=dict(username='admin', password='testpass',
                         lib_name='Library', lib_path=self.tmp.name))
        with app.app_context():
            for name in ['Étoile', '100%_real', 'Flagged', 'Progress', 'Done', 'Plain'] + [f'Bulk {i:02}' for i in range(61)]:
                db.ex('INSERT INTO manga(lib_id,name,path,scanned_at,release_date) VALUES(1,?,?,0,?)',
                      (name, self.tmp.name, '2020-05-12' if name == 'Étoile' else '2021'))
            self.ids = {r['name']: r['id'] for r in db.q('SELECT id,name FROM manga')}
            db.ex('INSERT INTO user_manga(user_id,manga_id,plan_to_read,dropped,favorited,bookmarked) VALUES(1,?,1,1,1,1)', (self.ids['Flagged'],))
            db.ex("INSERT INTO user_manga(user_id,manga_id,status) VALUES(1,?,'completed')", (self.ids['Done'],))
            db.ex("INSERT INTO progress(user_id,library_id,manga,volume,updated_at) VALUES(1,1,'Progress','1',0)")
            db.ex("INSERT INTO tags(name) VALUES('Épic')")
            db.ex('INSERT INTO manga_tags(manga_id,tag_id) VALUES(?,1)', (self.ids['Étoile'],))
            db.ex("INSERT INTO tags(name) VALUES('Unused')")

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def search(self, filters=None, **params):
        if filters is not None:
            params['filters'] = json.dumps(filters)
        def render(template, **ctx):
            return jsonify(names=[m['name'] for m in ctx['manga_list']], total=ctx['total'],
                           pages=ctx['total_pages'], page=ctx['page'], filters=ctx['filters'],
                           tags=[t['name'] for t in ctx['tag_suggestions']])
        with patch('views.render', side_effect=render):
            response = self.client.get('/l/1', query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_unicode_and_literal_characters(self):
        self.assertEqual(self.search(q='éTOILE')['names'], ['Étoile'])
        for query in ['%', '_', '100%_']:
            self.assertEqual(self.search(q=query)['names'], ['100%_real'])
        self.assertEqual(self.search([dict(type='tag', value='éPIC')])['names'], ['Étoile'])

    def test_boolean_include_exclude(self):
        for kind in ['plan_to_read', 'dropped', 'favorited', 'bookmarked']:
            self.assertEqual(self.search([dict(type=kind)])['names'], ['Flagged'])
            self.assertEqual(self.search([dict(type=kind, mode='exclude')])['total'], 66)

    def test_unread_progress_and_independent_flags(self):
        self.assertEqual(self.search([dict(type='status', value='unread')], q='Flagged')['names'], ['Flagged'])
        self.assertEqual(self.search([dict(type='status', value='unread')])['total'], 65)
        self.assertEqual(self.search([dict(type='status', value='unread', mode='exclude')])['names'], ['Done', 'Progress'])
        self.assertEqual(self.search([dict(type='status', value='reading')])['names'], ['Progress'])

    def test_pagination_and_preserved_query_links(self):
        first = self.search(q='Bulk', sort='name_desc')
        second = self.search(q='Bulk', sort='name_desc', page=2)
        self.assertEqual((first['total'], first['pages'], len(first['names']), len(second['names'])), (61, 2, 50, 11))
        self.assertFalse(set(first['names']) & set(second['names']))
        params = dict(q='Bulk', sort='name_desc', filters=json.dumps([dict(type='date', value='2021')]))
        html = self.client.get('/l/1', query_string=params).get_data(as_text=True)
        urls = [unescape(x) for x in re.findall(r'href="([^"]+)"', html)]
        page2 = next(url for url in urls if 'page=2' in url)
        parsed = parse_qs(urlparse(page2).query)
        self.assertEqual(parsed['q'], ['Bulk'])
        self.assertEqual(parsed['sort'], ['name_desc'])
        self.assertEqual(json.loads(parsed['filters'][0])[0]['value'], '2021')

    def test_malformed_parameters_and_bounds(self):
        for filters in [[1, None, []], {}, [dict(type=[], value={})], [dict(type='date', value='2020-99-12')]]:
            self.assertEqual(self.search(filters)['filters'], [])
        for page, expected in [('abc', 1), ('-3', 1), ('9999', 2)]:
            self.assertEqual(self.search(page=page)['page'], expected)
        self.assertEqual(self.client.get('/l/1?filters=broken').status_code, 200)

    def test_year_and_full_date(self):
        for value in ['2020', '2020-05-12']:
            self.assertEqual(self.search([dict(type='date', value=value)])['names'], ['Étoile'])
        self.assertEqual(self.search([dict(type='date', value='2020', op='>')])['total'], 66)
        self.assertEqual(self.search([dict(type='date', value='2021', op='<')])['names'], ['Étoile'])

    def test_author_edit_and_description_search(self):
        mid = self.ids['Étoile']
        response = self.client.post(f'/api/m/{mid}/meta', json=dict(
            author='  Émilie Writer  ', description='A pilot exploring 100%_new worlds', release_date='2020'))
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            self.assertEqual(db.q1('SELECT author FROM manga WHERE id=?', (mid,))['author'], 'Émilie Writer')
        for kind, value in [('author', 'émilie'), ('description', 'PILOT'), ('description', '%_')]:
            self.assertEqual(self.search([dict(type=kind, value=value)])['names'], ['Étoile'])
            self.assertEqual(self.search([dict(type=kind, value=value, mode='exclude')])['total'], 66)
        self.assertEqual(self.search([dict(type='author', value='writer'), dict(type='description', value='worlds')])['names'], ['Étoile'])
        self.client.post(f'/api/m/{mid}/meta', json=dict(description='Changed'))
        self.assertEqual(self.search([dict(type='author', value='writer')])['total'], 1)
        self.client.post(f'/api/m/{mid}/meta', json=dict(author=''))
        self.assertEqual(self.search([dict(type='author', value='writer')])['total'], 0)

    def test_existing_database_author_migration(self):
        import sqlite3
        con = sqlite3.connect(db.DB_PATH)
        con.execute('ALTER TABLE manga DROP COLUMN author')
        con.commit()
        con.close()
        db.init_db()
        db.init_db()
        with app.app_context():
            row = db.q1('SELECT name, author FROM manga WHERE id=?', (self.ids['Étoile'],))
            self.assertEqual(row['name'], 'Étoile')
            self.assertIsNone(row['author'])

    def test_suggestions_are_library_scoped(self):
        with app.app_context():
            db.ex("INSERT INTO libraries(name,path,created_at,is_hidden) VALUES('Other','other',0,1)")
            db.ex("INSERT INTO manga(lib_id,name,path,scanned_at) VALUES(2,'Private','other',0)")
            db.ex('INSERT INTO manga_tags(manga_id,tag_id) VALUES(?,2)', (68,))
        self.assertEqual(self.search()['tags'], ['Épic'])


if __name__ == '__main__':
    unittest.main()
