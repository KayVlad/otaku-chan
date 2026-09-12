from pathlib import Path
from content_cache import content_cache, reading_path
from flask import Blueprint, abort, g, jsonify, redirect, request, url_for
from config import MANGA_PER_PAGE
from db import ex, q, q1
from libraries import (accessible_libraries, get_volumes, get_images, get_library_page,
                       lib_or_404, manga_or_404, scan_lib, visible_dirs)
from helpers import render
from progress import continue_list, lib_progress_set, manga_progress, set_volume_read, sync_completion

views_bp = Blueprint("views", __name__)


@views_bp.route("/")
def home():
    show_hidden = request.args.get("show_hidden") == "1"
    libs = accessible_libraries(show_hidden=show_hidden)
    cont = continue_list(show_hidden=show_hidden)
    lib_rows = []
    for lib in libs:
        _, total, _, _ = get_library_page(lib["id"], 1)
        lib_rows.append({"lib": lib, "total": total})
    return render("home.html", cont=cont, lib_rows=lib_rows, libs=libs, show_hidden=show_hidden)


_SORT_MAP = {
    "name":        "m.name COLLATE NOCASE ASC",
    "name_desc":   "m.name COLLATE NOCASE DESC",
    "volumes_desc":"m.volume_count DESC",
    "volumes_asc": "m.volume_count ASC",
    "date_asc":    "m.release_date IS NULL, m.release_date ASC",
    "date_desc":   "m.release_date IS NULL, m.release_date DESC",
}


def _clean_filters(raw):
    from datetime import date
    if not isinstance(raw, list):
        return []
    result = []
    flags = ("favorited", "bookmarked", "plan_to_read", "dropped")
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "tag")
        value = item.get("value", "")
        mode = item.get("mode", "include")
        op = item.get("op", "=")
        if not isinstance(value, str) or mode not in ("include", "exclude"):
            continue
        value = value.strip()
        if kind in flags:
            value = ""
        elif kind in ("tag", "author", "description"):
            if not value:
                continue
        elif kind == "status":
            if value not in ("unread", "reading", "completed"):
                continue
        elif kind == "date":
            if op not in ("=", ">", "<"):
                continue
            try:
                if len(value) == 4 and value.isascii() and value.isdigit():
                    date(int(value), 1, 1)
                elif len(value) == 10 and date.fromisoformat(value).isoformat() == value:
                    pass
                else:
                    continue
            except ValueError:
                continue
        else:
            continue
        result.append(dict(type=kind, value=value, mode=mode, op=op if kind == "date" else "="))
    return result


def _search_manga(lib_id, text_q, sort, filters, page=1):
    order = _SORT_MAP.get(sort, _SORT_MAP["name"]) + ", m.id ASC"
    clauses, params = ["m.lib_id=?"], [lib_id]
    if text_q:
        clauses.append("instr(search_text(m.name), search_text(?)) > 0")
        params.append(text_q)
    started = """EXISTS (SELECT 1 FROM progress p WHERE p.user_id=um_user.id
                 AND p.library_id=m.lib_id AND p.manga=m.name)"""
    for f in filters:
        kind, value, exclude = f["type"], f["value"], f["mode"] == "exclude"
        if kind == "tag":
            clause = """EXISTS (SELECT 1 FROM manga_tags mt JOIN tags t ON t.id=mt.tag_id
                        WHERE mt.manga_id=m.id AND search_text(t.name)=search_text(?))"""
            params.append(value)
        elif kind in ("author", "description"):
            clause = f"instr(search_text(m.{kind}), search_text(?)) > 0"
            params.append(value)
        elif kind == "status":
            if value == "unread":
                clause = f"(um.status IS NULL AND NOT {started})"
            elif value == "reading":
                clause = f"(COALESCE(um.status, '')='reading' OR (um.status IS NULL AND {started}))"
            else:
                clause = "COALESCE(um.status, '')='completed'"
        elif kind == "date":
            # A year compares whole years, including metadata stored as YYYY-MM-DD.
            column = "substr(m.release_date, 1, 4)" if len(value) == 4 else "m.release_date"
            clause = f"COALESCE({column} {f['op']} ?, 0)"
            params.append(value)
        else:
            clause = f"COALESCE(um.{kind}, 0)=1"
        clauses.append(f"NOT ({clause})" if exclude else f"({clause})")
    source = """FROM manga m JOIN users um_user ON um_user.id=?
                LEFT JOIN user_manga um ON um.manga_id=m.id AND um.user_id=um_user.id
                WHERE """ + " AND ".join(clauses)
    params = [g.user["id"]] + params
    total = q1("SELECT COUNT(*) AS n " + source, params)["n"]
    total_pages = max(1, (total + MANGA_PER_PAGE - 1) // MANGA_PER_PAGE)
    page = max(1, min(page, total_pages))
    rows = q("""SELECT m.id, m.name, m.volume_count, m.cover_path,
                      um.status, um.favorited, um.bookmarked, um.plan_to_read, um.dropped
             """ + source + f" ORDER BY {order} LIMIT ? OFFSET ?",
             params + [MANGA_PER_PAGE, (page - 1) * MANGA_PER_PAGE])
    return rows, total, total_pages, page


@views_bp.route("/l/<int:lib_id>")
def library(lib_id):
    import json
    lib, root = lib_or_404(lib_id)
    text_q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "name")
    if sort not in _SORT_MAP:
        sort = "name"
    try:
        filters = _clean_filters(json.loads(request.args.get("filters", "[]")))
    except (ValueError, TypeError):
        filters = []
    try:
        requested_page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        requested_page = 1
    manga_list, total, total_pages, page = _search_manga(lib_id, text_q, sort, filters, requested_page)
    def page_url(number):
        args = dict(lib_id=lib_id, page=number, sort=sort)
        if text_q:
            args["q"] = text_q
        if filters:
            args["filters"] = json.dumps(filters, ensure_ascii=False)
        return url_for("views.library", **args)
    tags = q("""SELECT DISTINCT t.name FROM tags t JOIN manga_tags mt ON mt.tag_id=t.id
                JOIN manga m ON m.id=mt.manga_id WHERE m.lib_id=?
                ORDER BY search_text(t.name), t.id""", (lib_id,))
    return render("library.html", lib=lib, manga_list=manga_list, page=page,
                  total_pages=total_pages, total=total, page_url=page_url, tag_suggestions=tags,
                  progress_set=lib_progress_set(lib_id), text_q=text_q, sort=sort,
                  filters=filters, is_search=bool(text_q or filters or sort != "name"))


@views_bp.route("/m/<int:manga_id>")
def manga_detail(manga_id):
    manga = manga_or_404(manga_id)
    lib = {"id": manga["lib_id"], "name": manga["lib_name"]}
    manga_path = Path(manga['path'])
    volumes = get_volumes(manga_path)
    prog, last_volume = manga_progress(manga['lib_id'], manga['name'])
    last_idx = next((i for i, vol in enumerate(volumes) if vol["name"] == last_volume), -1)
    read_count = sum(1 for vol in volumes if prog.get(vol["name"], {}).get("read"))
    cover = f"/api/cover/{manga_id}"
    tag_rows = q("""SELECT t.name FROM tags t
                    JOIN manga_tags mt ON mt.tag_id = t.id
                    WHERE mt.manga_id=? ORDER BY t.name COLLATE NOCASE""", (manga['id'],))
    tags_csv = ", ".join(r["name"] for r in tag_rows)
    um = q1("SELECT * FROM user_manga WHERE user_id=? AND manga_id=?",
            (g.user["id"], manga['id']))
    user_manga = dict(um) if um else {}
    return render("volumes.html", lib=lib, manga=manga, volumes=volumes,
                  cover=cover, prog=prog, last_volume=last_volume, last_idx=last_idx,
                  read_count=read_count, tags_csv=tags_csv, user_manga=user_manga)


@views_bp.route("/m/<int:manga_id>/<string:volume_name>")
def reader(manga_id, volume_name):
    manga = manga_or_404(manga_id)
    lib = {"id": manga["lib_id"], "name": manga["lib_name"]}
    manga_path = Path(manga['path'])
    read_path, source_only = reading_path(manga, volume_name)
    images = get_images(manga['id'], read_path, volume_name, source_only=source_only)
    if not images:
        abort(404)
    content_cache().ensure(manga)
    all_volumes = [d.name for d in visible_dirs(manga_path)]
    idx = all_volumes.index(volume_name) if volume_name in all_volumes else 0
    row = q1("""SELECT page FROM progress
                WHERE user_id=? AND library_id=? AND manga=? AND volume=?""",
             (g.user["id"], manga['lib_id'], manga['name'], volume_name))
    resume_page = row["page"] if row else 0
    return render("reader.html",
        lib=lib, manga_id=manga_id, manga_name=manga['name'], volume_name=volume_name, images=images,
        prev_volume=all_volumes[idx - 1] if idx > 0 else None,
        next_volume=all_volumes[idx + 1] if idx < len(all_volumes) - 1 else None,
        volume_index=idx + 1, total_volumes=len(all_volumes),
        resume_page=resume_page)


@views_bp.route("/api/m/<int:manga_id>/meta", methods=["POST"])
def api_update_manga_meta(manga_id):
    from auth import admin_required
    admin_required()
    manga = manga_or_404(manga_id)
    data = request.get_json(silent=True) or {}
    author = data.get("author", manga["author"])
    if author is not None and not isinstance(author, str):
        return jsonify(error="Author must be text"), 400
    author = (author or "").strip() or None
    description  = (data.get("description") or "").strip() or None
    release_date = (data.get("release_date") or "").strip() or None
    ex("UPDATE manga SET description=?, release_date=?, author=? WHERE id=?",
       (description, release_date, author, manga['id']))
    if "tags" in data:
        tag_names = [t.strip() for t in (data.get("tags") or "").split(",") if t.strip()]
        ex("DELETE FROM manga_tags WHERE manga_id=?", (manga['id'],))
        for name in tag_names:
            ex("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag = q1("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,))
            ex("INSERT OR IGNORE INTO manga_tags (manga_id, tag_id) VALUES (?,?)",
               (manga['id'], tag['id']))
    return jsonify(ok=True)


@views_bp.route("/api/m/<int:manga_id>/user", methods=["POST"])
def api_update_user_manga(manga_id):
    import time
    manga        = manga_or_404(manga_id)
    data         = request.get_json(silent=True) or {}
    favorited    = 1 if data.get("favorited")    else 0
    bookmarked   = 1 if data.get("bookmarked")   else 0
    plan_to_read = 1 if data.get("plan_to_read") else 0
    dropped      = 1 if data.get("dropped")      else 0
    ex("""INSERT INTO user_manga (user_id, manga_id, favorited, bookmarked, plan_to_read, dropped, updated_at)
          VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(user_id, manga_id) DO UPDATE SET
            favorited=excluded.favorited, bookmarked=excluded.bookmarked,
            plan_to_read=excluded.plan_to_read, dropped=excluded.dropped,
            updated_at=excluded.updated_at""",
       (g.user["id"], manga['id'], favorited, bookmarked, plan_to_read, dropped,
        int(time.time() * 1000)))
    return jsonify(ok=True)




@views_bp.route("/api/lib/<int:lib_id>/scan", methods=["POST"])
def api_scan_lib(lib_id):
    import threading
    from flask import current_app
    from auth import admin_required
    admin_required()
    lib_or_404(lib_id)
    app = current_app._get_current_object()
    def _run():
        with app.app_context():
            scan_lib(lib_id)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True)


@views_bp.route("/api/m/<int:manga_id>/<volume_name>/images")
def api_images(manga_id, volume_name):
    manga = manga_or_404(manga_id)
    read_path, source_only = reading_path(manga, volume_name)
    return jsonify(get_images(manga['id'], read_path, volume_name, source_only=source_only))


@views_bp.route("/api/m/<int:manga_id>/volume/<path:volume_name>/read", methods=["POST"])
def api_mark_volume_read(manga_id, volume_name):
    manga = manga_or_404(manga_id)
    data = request.get_json(silent=True) or {}
    read = bool(data.get("read", True))
    set_volume_read(manga['lib_id'], manga['name'], volume_name, read)
    sync_completion(manga_id, manga['lib_id'], manga['name'])
    return jsonify(ok=True)


@views_bp.route("/api/m/<int:manga_id>/read-all", methods=["POST"])
def api_mark_all_read(manga_id):
    manga = manga_or_404(manga_id)
    data = request.get_json(silent=True) or {}
    read = bool(data.get("read", True))
    for vol in get_volumes(Path(manga['path'])):
        set_volume_read(manga['lib_id'], manga['name'], vol['name'], read)
    sync_completion(manga_id, manga['lib_id'], manga['name'])
    return jsonify(ok=True)
