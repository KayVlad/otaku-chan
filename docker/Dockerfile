FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/build_config.py /app/docker/build_config.py
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data

EXPOSE 5001

ENTRYPOINT ["/entrypoint.sh"]
