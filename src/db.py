import sqlite3
from flask import g
from config import DB_PATH


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS libraries (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            path       TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL,
            is_hidden  INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS library_access (
            user_id    INTEGER NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, library_id)
        );
        CREATE TABLE IF NOT EXISTS progress (
            user_id    INTEGER NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
            manga      TEXT NOT NULL,
            volume     TEXT NOT NULL,
            page       INTEGER NOT NULL DEFAULT 0,
            total      INTEGER NOT NULL DEFAULT 0,
            read       INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, library_id, manga, volume)
        );
        CREATE INDEX IF NOT EXISTS idx_progress_recent
            ON progress (user_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS manga (
            id           INTEGER PRIMARY KEY,
            lib_id       INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            volume_count INTEGER NOT NULL DEFAULT 0,
            scanned_at   INTEGER NOT NULL,
            path         TEXT NOT NULL,
            cover_path   TEXT,
            cache_path   TEXT,
            description  TEXT,
            release_date TEXT,
            UNIQUE(lib_id, name)
        );
        CREATE TABLE IF NOT EXISTS tags (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS manga_tags (
            manga_id INTEGER NOT NULL REFERENCES manga(id) ON DELETE CASCADE,
            tag_id   INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
            PRIMARY KEY (manga_id, tag_id)
        );
        CREATE TABLE IF NOT EXISTS user_manga (
            user_id      INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
            manga_id     INTEGER NOT NULL REFERENCES manga(id)  ON DELETE CASCADE,
            status       TEXT CHECK(status IN ('reading', 'completed')),
            favorited    INTEGER NOT NULL DEFAULT 0,
            bookmarked   INTEGER NOT NULL DEFAULT 0,
            plan_to_read INTEGER NOT NULL DEFAULT 0,
            dropped      INTEGER NOT NULL DEFAULT 0,
            updated_at   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, manga_id)
        );
    """)
    con.commit()
    con.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(exc=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def q(sql, args=()):
    return db().execute(sql, args).fetchall()


def q1(sql, args=()):
    return db().execute(sql, args).fetchone()


def ex(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur
