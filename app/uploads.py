"""Temporary storage for uploaded CSV files. One folder per user, random names, removed after 24 hours, at most 5 files per user."""
import json
import os
import re
import secrets
import time
from pathlib import Path

TTL_SECONDS = 24 * 3600
MAX_FILES = 5
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,40}")


def _dir(base, user_id):
    d = Path(base) / "uploads" / str(int(user_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep(d):
    now = time.time()
    files = sorted((p for p in d.glob("*.csv")), key=lambda p: p.stat().st_mtime)
    for p in list(files):
        if now - p.stat().st_mtime > TTL_SECONDS:
            p.unlink(missing_ok=True)
            p.with_suffix(".json").unlink(missing_ok=True)
            files.remove(p)
    for p in files[:max(0, len(files) - (MAX_FILES - 1))]:
        p.unlink(missing_ok=True)
        p.with_suffix(".json").unlink(missing_ok=True)


def save(base, user_id, name, raw):
    d = _dir(base, user_id)
    _sweep(d)
    token = secrets.token_urlsafe(18)
    (d / f"{token}.csv").write_bytes(raw)
    (d / f"{token}.json").write_text(json.dumps({"name": name}))
    return token


def load(base, user_id, token):
    """Bytes and original name of an upload, or None if it is missing, expired or not this user's."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    d = _dir(base, user_id)
    p = d / f"{token}.csv"
    if not p.is_file() or time.time() - p.stat().st_mtime > TTL_SECONDS:
        return None
    try:
        name = json.loads((d / f"{token}.json").read_text()).get("name", "data.csv")
    except (OSError, ValueError):
        name = "data.csv"
    return p.read_bytes(), name
