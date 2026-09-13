"""Authenticated integration API for companion applications such as Otakuarr."""

import hashlib
import hmac
import io
import os
import time
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError

from config import CACHE_DIR
from db import db, q, q1
from libraries import scan_lib


integration_bp = Blueprint("integration", __name__, url_prefix="/api/v1")

MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_COVER_SIZE = (1200, 1800)
MAX_COVER_PIXELS = 40_000_000


def _token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@integration_bp.before_request
def require_bearer_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify(error="A bearer token is required."), 401
    supplied = header[7:].strip()
    if not supplied:
        return jsonify(error="A bearer token is required."), 401
    digest = _token_hash(supplied)
    token = q1("SELECT * FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL", (digest,))
    if token is None or not hmac.compare_digest(token["token_hash"], digest):
        return jsonify(error="The bearer token is invalid or revoked."), 401
    db().execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (int(time.time()), token["id"]))
    db().commit()


def _tags_for(manga_id):
    return [row["name"] for row in q("""SELECT t.name FROM tags t
        JOIN manga_tags mt ON mt.tag_id=t.id WHERE mt.manga_id=?
        ORDER BY t.name COLLATE NOCASE""", (manga_id,))]


def _manga_json(row):
    item = dict(row)
    item.pop("path", None)
    item.pop("cache_path", None)
    item["tags"] = _tags_for(row["id"])
    item["stable_id"] = item.pop("folder_id", None)
    item["available"] = bool(item.get("available", 1))
    return item


@integration_bp.get("/status")
def status():
    return jsonify(service="otaku-chan", api_version=1)


@integration_bp.get("/libraries")
def libraries():
    rows = q("SELECT id,name,is_hidden,path FROM libraries ORDER BY name COLLATE NOCASE")
    return jsonify(libraries=[{
        "id": row["id"], "name": row["name"],
        "hidden": bool(row["is_hidden"]),
        "available": __import__("pathlib").Path(row["path"]).is_dir(),
    } for row in rows])


@integration_bp.get("/libraries/<int:lib_id>/manga")
def library_manga(lib_id):
    if q1("SELECT 1 FROM libraries WHERE id=?", (lib_id,)) is None:
        abort(404)
    rows = q("SELECT * FROM manga WHERE lib_id=? AND available=1 ORDER BY name COLLATE NOCASE", (lib_id,))
    return jsonify(manga=[_manga_json(row) for row in rows])


@integration_bp.get("/manga/<int:manga_id>")
def manga(manga_id):
    row = q1("SELECT * FROM manga WHERE id=? AND available=1", (manga_id,))
    if row is None:
        abort(404)
    return jsonify(manga=_manga_json(row))


def _valid_release_date(value):
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    try:
        if len(value) == 4 and value.isascii() and value.isdigit():
            date(int(value), 1, 1)
        else:
            date.fromisoformat(value)
        return True
    except ValueError:
        return False


@integration_bp.patch("/manga/<int:manga_id>/metadata")
def update_metadata(manga_id):
    if q1("SELECT 1 FROM manga WHERE id=? AND available=1", (manga_id,)) is None:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="A JSON object is required."), 400
    permitted = {"author", "description", "release_date", "tags"}
    unknown = set(body) - permitted
    if unknown:
        return jsonify(error=f"Unsupported metadata fields: {', '.join(sorted(unknown))}"), 400
    if "release_date" in body and not _valid_release_date(body["release_date"]):
        return jsonify(error="release_date must be a year or ISO date."), 400

    con = db()
    for field in ("author", "description", "release_date"):
        if field in body:
            value = body[field]
            if value is not None and not isinstance(value, str):
                return jsonify(error=f"{field} must be text or null."), 400
            con.execute(f"UPDATE manga SET {field}=? WHERE id=?", ((value or "").strip() or None, manga_id))
    if "tags" in body:
        if not isinstance(body["tags"], list) or not all(isinstance(tag, str) for tag in body["tags"]):
            return jsonify(error="tags must be an array of strings."), 400
        names = list(dict.fromkeys(tag.strip() for tag in body["tags"] if tag.strip()))
        con.execute("DELETE FROM manga_tags WHERE manga_id=?", (manga_id,))
        for name in names:
            con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            tag_id = con.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()["id"]
            con.execute("INSERT INTO manga_tags(manga_id,tag_id) VALUES(?,?)", (manga_id, tag_id))
    con.commit()
    row = q1("SELECT * FROM manga WHERE id=?", (manga_id,))
    return jsonify(manga=_manga_json(row))


@integration_bp.put("/manga/<int:manga_id>/cover")
def update_cover(manga_id):
    manga = q1("SELECT * FROM manga WHERE id=? AND available=1", (manga_id,))
    if manga is None:
        abort(404)
    upload = request.files.get("cover")
    if upload is None or not upload.filename:
        return jsonify(error="A multipart cover file is required."), 400
    content = upload.stream.read(MAX_COVER_BYTES + 1)
    if len(content) > MAX_COVER_BYTES:
        return jsonify(error="The cover must be 10 MB or smaller."), 413

    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.width * source.height > MAX_COVER_PIXELS:
                return jsonify(error="The uploaded cover has too many pixels."), 400
            source.load()
            cover = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return jsonify(error="The uploaded cover is not a supported image."), 400
    if cover.width < 1 or cover.height < 1:
        cover.close()
        return jsonify(error="The uploaded cover has invalid dimensions."), 400
    cover.thumbnail(MAX_COVER_SIZE)

    cache = Path(manga["path"]) / CACHE_DIR
    if cache.is_symlink():
        cover.close()
        return jsonify(error="The manga cover directory cannot be a symbolic link."), 409
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        cover.close()
        return jsonify(error="The cover directory could not be created in the manga folder."), 409
    output = cache / "cover.jpg"
    temporary = cache / f".cover-{os.getpid()}-{time.time_ns()}.tmp"
    try:
        cover.save(temporary, "JPEG", quality=88, optimize=True)
        os.replace(temporary, output)
    except OSError:
        temporary.unlink(missing_ok=True)
        return jsonify(error="The cover could not be written to the manga folder."), 409
    finally:
        cover.close()

    db().execute("UPDATE manga SET cover_path=1 WHERE id=?", (manga_id,))
    db().commit()
    return jsonify(ok=True, cover=f"/api/cover/{manga_id}")


@integration_bp.post("/libraries/<int:lib_id>/scan")
def scan_library(lib_id):
    if q1("SELECT 1 FROM libraries WHERE id=?", (lib_id,)) is None:
        abort(404)
    try:
        scan_lib(lib_id)
    except OSError:
        return jsonify(error="Library scan failed; existing metadata was preserved."), 409
    return jsonify(ok=True)
