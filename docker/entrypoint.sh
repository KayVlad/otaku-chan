#!/bin/sh
set -e

cd /app/src
python /app/docker/build_config.py
exec gunicorn -w 1 --worker-class gevent -b "0.0.0.0:${PORT:-5001}" app:app
