import re
import unicodedata
from pathlib import Path
from flask import abort, request, Response, render_template as _render_template


def natural_sort_key(s: str):
    """Compare numbered file/folder names naturally, with stable tie-breaking."""
    normalized = unicodedata.normalize("NFKC", s).casefold()
    parts = tuple(int(t) if t.isdecimal() else t for t in re.split(r"(\d+)", normalized))
    return parts, normalized, s



def safe_iterdir(path: Path):
    try:
        return list(path.iterdir())
    except (NotADirectoryError, PermissionError, FileNotFoundError):
        return []


def safe_path(root: Path, *parts) -> Path:
    r = root.resolve()
    p = r.joinpath(*parts).resolve()
    if p != r and r not in p.parents:
        abort(404)
    return p


def is_ajax() -> bool:
    return request.headers.get("X-Alpine-Request") == "true"


def render(fragment_template: str, **ctx):
    fragment_html = _render_template(fragment_template, **ctx)
    if is_ajax():
        return Response(fragment_html, content_type="text/html")
    return _render_template("shell.html", fragment=fragment_html)
