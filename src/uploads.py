"""Library imports staged and validated before any title is installed."""
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from flask import Blueprint, jsonify, request
from libraries import lib_or_404, scan_lib
from config import IMAGE_EXTENSIONS

uploads_bp = Blueprint('uploads', __name__)
MAX_BYTES = int(os.environ.get('UPLOAD_MAX_BYTES', 2 * 1024**3))
MAX_FILES = 20000
RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def import_path(name):
    if '\\' in name or name.startswith('/'):
        raise ValueError('Invalid upload path.')
    parts = name.split('/')
    if any(not p or p in ('.', '..') or any(c in p for c in ':<>"|?*') or p.endswith((' ', '.')) or RESERVED.match(p)
           or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError('Invalid upload path.')
    # Hidden OS files and identity markers must never be imported from an untrusted archive.
    if any(p.startswith('.') or p == '__MACOSX' for p in parts):
        return None
    if len(parts) != 3 or PurePosixPath(name).suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError('Use Manga / Volume / image files (JPG, PNG, WebP, GIF or AVIF).')
    return Path(*parts)


def stage_upload(files, stage):
    total = 0
    count = 0
    seen = set()
    def copy(name, stream):
        nonlocal total, count
        relative = import_path(name)
        if relative is None:
            return
        key = str(relative).casefold()
        if key in seen:
            raise ValueError('Duplicate paths in upload.')
        seen.add(key)
        count += 1
        if count > MAX_FILES:
            raise ValueError('Too many files in one upload.')
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('Unpacked upload exceeds the size limit.')
                output.write(chunk)
    for item in files:
        if item.filename.lower().endswith('.zip') and '/' not in item.filename:
            archive_path = stage / '.incoming.zip'
            with archive_path.open('wb') as output:
                shutil.copyfileobj(item.stream, output)
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.infolist()) > MAX_FILES * 2:
                    raise ValueError('Too many archive entries.')
                for entry in archive.infolist():
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise ValueError('Archive links and special files are not supported.')
                    # Validate directories too, before ignoring them.
                    if entry.is_dir():
                        name = entry.filename.rstrip('/')
                        if '..' in name.split('/') or name.startswith('/') or '\\' in name or ':' in name:
                            raise ValueError('Invalid archive directory.')
                        continue
                    if entry.file_size > MAX_BYTES or entry.flag_bits & 1:
                        raise ValueError('Oversized or encrypted archive entry.')
                    with archive.open(entry) as stream:
                        copy(entry.filename, stream)
        else:
            copy(item.filename, item.stream)
    if not count:
        raise ValueError('No manga images found in the upload.')
    return count


@uploads_bp.route('/api/lib/<int:lib_id>/upload', methods=['POST'])
def upload(lib_id):
    _, root = lib_or_404(lib_id)
    root = root.resolve()
    request.max_content_length = MAX_BYTES
    request.max_form_parts = MAX_FILES + 10
    if not root.is_dir():
        return jsonify(error='Library folder is unavailable.'), 409
    installed = []
    try:
        files = request.files.getlist('files')
        with tempfile.TemporaryDirectory(prefix='.otaku-upload-', dir=root) as temp:
            stage = Path(temp)
            count = stage_upload(files, stage)
            titles = [p for p in stage.iterdir() if p.is_dir() and not p.name.startswith('.')]
            existing = {p.name.casefold() for p in root.iterdir()}
            if any(title.name.casefold() in existing for title in titles):
                return jsonify(error='A title with that name already exists. Rename it before uploading.'), 409
            try:
                for title in titles:
                    destination = root / title.name
                    if destination.exists() or destination.is_symlink():
                        raise ValueError('An upload destination already exists.')
                    title.rename(destination)
                    installed.append(destination)
            except Exception:
                for destination in installed:
                    destination.rename(stage / destination.name)
                raise
        scan_lib(lib_id)
        return jsonify(ok=True, files=count, titles=len(installed))
    except (ValueError, zipfile.BadZipFile, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    except OSError:
        message = 'Files were imported; scanning failed. Try Scan library again.' if installed else 'Upload failed. Check library storage and permissions.'
        return jsonify(error=message), 409
