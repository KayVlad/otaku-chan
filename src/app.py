import secrets
from datetime import timedelta
from flask import Flask
from config import DATA_DIR, DEBUG, PORT, SECRET_PATH, CONTENT_CACHE_DIR
from content_cache import init_cache
from db import close_db, init_db
from auth import auth_bp
from views import views_bp
from media import media_bp
from progress import progress_bp
from settings import settings_bp
from cli import make_sample, seed_meta

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

if not SECRET_PATH.exists():
    SECRET_PATH.write_text(secrets.token_hex(32))
app.secret_key = SECRET_PATH.read_text().strip()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=90)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.register_blueprint(auth_bp)
app.register_blueprint(views_bp)
app.register_blueprint(media_bp)
app.register_blueprint(progress_bp)
app.register_blueprint(settings_bp)
app.teardown_appcontext(close_db)
app.cli.command("make-sample")(make_sample)
app.cli.command("seed-meta")(seed_meta)

init_db()
init_cache(app, CONTENT_CACHE_DIR)

if __name__ == "__main__":
    app.run(debug=DEBUG, port=PORT)
