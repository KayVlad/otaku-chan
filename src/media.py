from pathlib import Path
from flask import Blueprint, abort, send_file, request
from content_cache import content_cache
from config import CACHE_DIR, COVER_NAMES, IMAGE_EXTENSIONS
from helpers import safe_path
from libraries import first_image, lib_or_404, manga_or_404

media_bp = Blueprint("media", __name__)


@media_bp.route("/img/<int:manga_id>/<volume_name>/<filename>")
def serve_image(manga_id, volume_name, filename):
    manga = manga_or_404(manga_id)
    manga_path = Path(manga['path'])
    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
        abort(404)
    if request.args.get('source') != '1':
        cached = content_cache().lookup(manga)
        if cached:
            cached_file = safe_path(cached, volume_name, filename)
            try:
                return send_file(cached_file)
            except OSError:
                pass  # Expired/missing cached pages always fall back to the source.
    path = safe_path(manga_path, volume_name, filename)
    if not path.is_file():
        abort(404)
    return send_file(path)


def _send_cover(manga_path: Path):
    cache = manga_path / CACHE_DIR
    for n in COVER_NAMES:
        p = cache / n
        if p.is_file():
            return send_file(p)
#    try:
#        from PIL import Image
#        cache.mkdir(exist_ok=True)
#        out = cache / "cover.jpg"
#        with Image.open(src) as im:
#            im = im.convert("RGB")
#            im.thumbnail((400, 600))
#            im.save(out, "JPEG", quality=82)
#        return send_file(out)
#    except Exception:
    return None


@media_bp.route("/api/cover/<int:manga_id>")
def serve_manga_cover(manga_id):
    manga = manga_or_404(manga_id)
    if not manga["cover_path"]:
        abort(404)
    return _send_cover(Path(manga["path"])) or ""


@media_bp.route("/cover/<int:lib_id>/<manga_name>")
def serve_cover(lib_id, manga_name):
    _, root = lib_or_404(lib_id)
    manga_path = safe_path(root, manga_name)
    if not manga_path.is_dir():
        abort(404)
    return _send_cover(manga_path)
