import os
import time
from pathlib import Path
from flask import Blueprint, abort, g, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from db import ex, q1
from helpers import render

auth_bp = Blueprint("auth", __name__)


def user_count():
    return q1("SELECT COUNT(*) c FROM users")["c"]


@auth_bp.before_app_request
def auth_guard():
    if request.endpoint == "static":
        return
    if user_count() == 0:
        if request.endpoint != "auth.setup":
            return redirect("/setup")
        return
    if request.endpoint == "auth.setup":
        return redirect("/login")
    if request.endpoint == "auth.login":
        if session.get("uid"):
            return redirect("/")
        return
    if request.endpoint == "auth.logout":
        return
    uid = session.get("uid")
    if not uid:
        return redirect("/login")
    g.user = q1("SELECT * FROM users WHERE id=?", (uid,))
    if g.user is None:
        session.clear()
        return redirect("/login")


@auth_bp.app_context_processor
def inject_user():
    return {"user": g.get("user")}


def admin_required():
    if not g.get("user") or not g.user["is_admin"]:
        abort(403)


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < 4:
            error = "Username required; password must be at least 4 characters."
        else:
            ex("INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,1,?)",
               (username, generate_password_hash(password), int(time.time())))
            lib_name = request.form.get("lib_name", "").strip()
            lib_path = request.form.get("lib_path", "").strip()
            if lib_name and lib_path:
                ex("INSERT INTO libraries (name, path, created_at) VALUES (?,?,?)",
                   (lib_name, str(Path(lib_path).expanduser()), int(time.time())))
            session.permanent = True; session["uid"] =q1("SELECT id FROM users WHERE username=?", (username,))["id"]
            return redirect("/")
    return render("setup.html", error=error, env_root=os.environ.get("MANGA_ROOT", ""))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        row = q1("SELECT * FROM users WHERE username=?",
                 (request.form.get("username", "").strip(),))
        if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
            session.permanent = True; session["uid"] =row["id"]
            return redirect("/")
        error = "Invalid username or password."
    return render("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")
