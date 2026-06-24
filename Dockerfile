FROM python:3.13-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + the recipients spreadsheet (xlsx is committed, not gitignored)
COPY . .

# Default port for platforms that don't inject one (Fly sets PORT=8080 in
# fly.toml; Railway injects its own PORT at runtime — shell form below honors it).
ENV PORT=8080
EXPOSE 8080

# ONE worker only — the app runs the email campaign in a background thread and
# keeps state in SQLite; multiple workers would double-send and corrupt state.
# Shell form so ${PORT} expands at runtime (works on both Fly and Railway).
CMD gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-8080}
