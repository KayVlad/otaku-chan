import sqlite3
import time
from pathlib import Path
from flask import Blueprint, abort, flash, g, redirect, request
from werkzeug.security import generate_password_hash
from auth import admin_required
from db import ex, q, q1
from helpers import render
from libraries import scan_lib

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings")
def settings():
    admin_required()
    libs = [dict(r, exists=Path(r["path"]).is_dir())
            for r in q("SELECT * FROM libraries ORDER BY name COLLATE NOCASE")]
    users = q("SELECT * FROM users ORDER BY username COLLATE NOCASE")
    access = {}
    for r in q("SELECT user_id, library_id FROM library_access"):
        access.setdefault(r["user_id"], set()).add(r["library_id"])
    return render("settings.html", libs=libs, users=users, access=access)


@settings_bp.route("/settings/library/add", methods=["POST"])
def settings_library_add():
    admin_required()
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip()
    if not name or not path:
        flash("Library name and path are required.")
    else:
        try:
            import threading
            from flask import current_app
            ex("INSERT INTO libraries (name, path, created_at) VALUES (?,?,?)",
               (name, str(Path(path).expanduser()), int(time.time())))
            lib_id = q1("SELECT id FROM libraries WHERE path=?",
                        (str(Path(path).expanduser()),))["id"]
            app = current_app._get_current_object()
            def _scan():
                with app.app_context():
                    scan_lib(lib_id)
            threading.Thread(target=_scan, daemon=True).start()
            flash(f"Library '{name}' added — scanning in background.")
        except sqlite3.IntegrityError:
            flash("A library with that path already exists.")
    return redirect("/settings")


@settings_bp.route("/settings/library/<int:lib_id>/toggle-hidden", methods=["POST"])
def settings_library_toggle_hidden(lib_id):
    admin_required()
    ex("UPDATE libraries SET is_hidden = NOT is_hidden WHERE id=?", (lib_id,))
    return redirect("/settings")


@settings_bp.route("/settings/library/<int:lib_id>/delete", methods=["POST"])
def settings_library_delete(lib_id):
    admin_required()
    ex("DELETE FROM libraries WHERE id=?", (lib_id,))
    flash("Library removed (files on disk untouched).")
    return redirect("/settings")


@settings_bp.route("/settings/user/add", methods=["POST"])
def settings_user_add():
    admin_required()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or len(password) < 4:
        flash("Username required; password must be at least 4 characters.")
        return redirect("/settings")
    try:
        ex("INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
           (username, generate_password_hash(password),
            1 if request.form.get("is_admin") else 0, int(time.time())))
        flash(f"User '{username}' created.")
    except sqlite3.IntegrityError:
        flash("That username already exists.")
    return redirect("/settings")


@settings_bp.route("/settings/user/<int:user_id>/update", methods=["POST"])
def settings_user_update(user_id):
    admin_required()
    target = q1("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        abort(404)
    is_admin = 1 if request.form.get("is_admin") else 0
    if user_id == g.user["id"] and not is_admin:
        is_admin = 1
        flash("You can't remove your own admin rights.")
    ex("UPDATE users SET is_admin=? WHERE id=?", (is_admin, user_id))
    pw = request.form.get("password", "")
    if pw:
        if len(pw) < 4:
            flash("Password not changed — must be at least 4 characters.")
        else:
            ex("UPDATE users SET password_hash=? WHERE id=?",
               (generate_password_hash(pw), user_id))
    ex("DELETE FROM library_access WHERE user_id=?", (user_id,))
    for lid in request.form.getlist("libs"):
        try:
            ex("INSERT OR IGNORE INTO library_access (user_id, library_id) VALUES (?,?)",
               (user_id, int(lid)))
        except ValueError:
            pass
    flash(f"User '{target['username']}' updated.")
    return redirect("/settings")


@settings_bp.route("/settings/user/<int:user_id>/delete", methods=["POST"])
def settings_user_delete(user_id):
    admin_required()
    if user_id == g.user["id"]:
        flash("You can't delete your own account.")
        return redirect("/settings")
    ex("DELETE FROM users WHERE id=?", (user_id,))
    flash("User deleted.")
    return redirect("/settings")
