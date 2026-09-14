# Otaku-chan

Personal manga reader with administrator-created accounts. Source layout:
`manga_library/Series/Volume/page.png`. First-run setup creates the initial admin;
subsequent accounts are created in Settings. Add `/mnt/manga` as a library in Docker.

## Docker

Copy `.env.example` to `.env`, then run `docker compose up -d --build`.
Open port 6769, create the initial administrator and scan your library in Settings.
The source library is mounted read-only; database and session secret persist in `./data`.

## Optional SSD manga cache

Set `MANGA_CACHE_HOST_PATH` in `.env` to an absolute directory on your SSD, then run:

```sh
docker compose -f docker-compose.yml -f docker-compose.cache.yml up -d --build
```

The override mounts the SSD directory at `/cache` and sets `CONTENT_CACHE_DIR=/cache`.
Without the override/content-cache setting, reading works directly from source as before.
For non-Docker runs set `CONTENT_CACHE_DIR` to the SSD directory (or set
`content_cache_dir` in data/config.json). `CACHE_DIR` remains the separate cover folder
setting; database `manga.cache_path` records a completed content snapshot.

Starting a reader queues a background copy of the entire series. That reader's images
continue to come from the source without waiting. Later reader opens use the completed
snapshot. Partial copies are never served, simultaneous opens share one copy job, and
copies are serialized to avoid multiple simultaneous HDD scans. Failed copies are logged;
source reading continues and a later reader open retries.

The 30-day lifetime starts when copying begins, never renews on reads, and persists across
restarts. Expired entries are immediately ineligible for reads; a background sweep removes
them on startup and hourly, even if nobody opens that series. A later reader open queues
a fresh copy. Only managed subdirectories below the cache's `otaku-chan` directory are
removed. Source content is never deleted. Use a dedicated cache directory separate from
all source libraries; do not share it between app instances. The Docker server uses one
Gunicorn worker with four request threads and a separate copy thread so disk copying does
not block the gevent event loop. Do not switch to gevent for this background-copy setup.

New source volumes missing from the snapshot are read from source without refreshing the
whole cached series. Scan the library to discover additions and reopen completed series.
Existing cached volumes remain snapshots until expiry (source edits are not mirrored).
Caching is shared across users; all normal library access checks still apply.

## Tests

```sh
python -m unittest discover -s tests -v
```

Reader gestures: pinch with two fingers to zoom, then drag with one finger to pan.
Zoom mode also provides +/− buttons. The page slider supports drag/tap and keyboard
arrows, Home and End, regardless of the number of pages.
Run the JavaScript reader regressions with `node --test tests/reader.test.cjs` (Node 18+).

Search supports title text plus combined include/exclude filters for tags, author, description, reading status (including Unread / not started), and personal flags. Author and description match case-insensitive literal substrings, including Unicode. Release-date filters accept a year or YYYY-MM-DD; a year compares whole years. Results stay paginated with filters preserved. Administrators can edit author metadata on the manga detail page; existing databases add the optional author field automatically at startup.


Library maintenance and imports:
- Scan once after upgrading to assign hidden `.otaku-id` identity files to manga and volume folders. Keep these files when moving folders. Renames and moves between configured libraries retain author, tags, flags and reading progress after scanning the destination. Copies receive a separate identity. On read-only storage, filesystem IDs provide same-filesystem rename tracking; moves to another filesystem need the identity files to preserve identity.
- A successful scan treats the library folder as authoritative: manga, volumes, progress, flags and unused tags that no longer match disk content are deleted instead of accumulating as hidden records. If the library is unavailable or any filesystem read fails, the scan aborts and leaves the database unchanged. Scans run as database transactions; changed source pages invalidate the old SSD snapshot.
- In a library, drop a ZIP or manga folder, or use **Upload → Choose ZIP / Choose folder**. Structure: `Manga/Volume/images`. Users can upload to libraries they can access. Imports reject existing title names, traversal paths, links, duplicate paths and non-image files. The default request/unpacked limit is 2 GiB and 20,000 images; set `UPLOAD_MAX_BYTES` to change the byte limit. Imports are staged before installation and scanned before the grid refreshes. A ZIP may contain several manga folders; omit an extra library wrapper folder.
- **Unread all** clears reading progress and resume positions, including progress for missing volumes, while preserving personal flags. Read/unread updates the detail section and status bar through Alpine AJAX. Search ignores accents as well as letter case. Library, reader, settings and search navigation use partial updates; authentication still uses full navigation.

## Companion API

Otakuarr and other trusted companion services use the versioned integration API.
Create an admin-level bearer token from the Otaku-chan container or source directory:

```sh
flask --app app create-api-token --name otakuarr
```

With Docker Compose, run it from the application directory. The explicit
working directory also works with older images:

```sh
docker compose exec -w /app/src app flask --app app create-api-token --name otakuarr
```

The token is printed once and stored as a SHA-256 digest. Send it as
`Authorization: Bearer oc_...`. Reissuing the same name rotates the token. Revoke
it with `flask --app app revoke-api-token --name otakuarr`.

Version 1 provides server status, library and manga catalogs, metadata updates,
cover uploads, and scan triggers under `/api/v1`. Upload a cover with a multipart
`PUT /api/v1/manga/<id>/cover` request whose file field is named `cover`. Images
are validated, resized to at most 1200×1800, and stored as the manga's custom
`.cache/cover.jpg`. Browser sessions cannot authenticate these routes, and bearer
tokens do not authenticate browser pages. Library filesystem paths and
content-cache paths are not returned through the API.
