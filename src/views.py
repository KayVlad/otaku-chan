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


def _search_manga(lib_id, text_q, sort, filters):
    order = _SORT_MAP.get(sort, _SORT_MAP["name"])
    where_clauses = ["m.lib_id=?"]
    where_params  = [lib_id]

    if text_q:
        where_clauses.append("m.name LIKE ? COLLATE NOCASE")
        where_params.append(f"%{text_q}%")

    for f in filters:
        ftype = str(f.get("type", "tag"))
        fval  = str(f.get("value", "")).strip()
        fmode = str(f.get("mode", "include"))
        fop   = str(f.get("op", "="))
        if not fval and ftype not in ("favorited", "bookmarked"):
            continue
        if ftype == "tag":
            sub = ("SELECT manga_id FROM manga_tags mt "
                   "JOIN tags t ON t.id=mt.tag_id WHERE t.name=? COLLATE NOCASE")
            where_clauses.append(f"m.id {'IN' if fmode == 'include' else 'NOT IN'} ({sub})")
            where_params.append(fval)
        elif ftype == "status":
            if fval not in ("reading", "completed"):
                continue
            if fmode == "include":
                where_clauses.append("um.status = ?")
            else:
                where_clauses.append("(um.status IS NULL OR um.status != ?)")
            where_params.append(fval)
        elif ftype in ("favorited", "bookmarked", "plan_to_read", "dropped"):
            col = ftype
            if fmode == "include":
                where_clauses.append(f"um.{col} = 1")
            else:
                where_clauses.append(f"(um.{col} IS NULL OR um.{col} = 0)")
        elif ftype == "date":
            if fop not in (">", "<", "="):
                continue
            where_clauses.append(f"m.release_date {fop} ?")
            where_params.append(fval)

    where_sql = " AND ".join(where_clauses)
    return q(f"""
        SELECT m.id, m.name, m.volume_count, m.cover_path,
               um.status, um.favorited, um.bookmarked, um.plan_to_read, um.dropped
        FROM manga m
        LEFT JOIN user_manga um ON um.manga_id = m.id AND um.user_id = ?
        WHERE {where_sql}
        ORDER BY {order}
    """, [g.user["id"]] + where_params)


@views_bp.route("/l/<int:lib_id>")
def library(lib_id):
    import json as _json
    lib, root = lib_or_404(lib_id)

    text_q = (request.args.get("q") or "").strip()
    sort   = request.args.get("sort", "name")
    try:
        filters = _json.loads(request.args.get("filters", "[]"))
        if not isinstance(filters, list):
            filters = []
    except (ValueError, TypeError):
        filters = []

    is_search = bool(text_q or filters or sort != "name")

    if is_search:
        manga_list = _search_manga(lib_id, text_q, sort, filters)
        total, total_pages, page = len(manga_list), 1, 1
    else:
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        manga_list, total, total_pages, page = get_library_page(lib_id, page, g.user["id"])
        if page != int(request.args.get("page", 1)):
            return redirect(url_for("views.library", lib_id=lib_id, page=page))

    return render("library.html", lib=lib, manga_list=manga_list, page=page,
                  total_pages=total_pages, total=total,
                  progress_set=lib_progress_set(lib_id),
                  text_q=text_q, sort=sort, filters=filters, is_search=is_search)


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
    description  = (data.get("description") or "").strip() or None
    release_date = (data.get("release_date") or "").strip() or None
    ex("UPDATE manga SET description=?, release_date=? WHERE id=?",
       (description, release_date, manga['id']))
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
