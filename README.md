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
