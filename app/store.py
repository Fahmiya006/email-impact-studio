"""Storage and authentication: SQLite, scrypt password hashing, session tokens, login throttling."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

SESSION_DAYS = 7
RESET_MINUTES = 60
MAX_FAILS, FAIL_WINDOW = 5, 600          # 5 failed logins per 10 minutes per email or IP
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('plan','score','data')), title TEXT NOT NULL, created_at TEXT NOT NULL,
  inputs TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_user ON runs(user_id, id DESC);
CREATE TABLE IF NOT EXISTS resets(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fails(key TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS fails_key ON fails(key, ts);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def validate_password(password):
    if len(password) < 10:
        raise ValueError("Use a password of at least 10 characters.")
    if len(password) > 200:
        raise ValueError("Password is too long.")


class Store:
    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c):
        """Databases made before uploaded-data runs existed only allow the kinds 'plan' and 'score'. Rebuild the table once to allow 'data'."""
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()
        if row and "'data'" not in row["sql"]:
            c.executescript("""
ALTER TABLE runs RENAME TO runs_old;
CREATE TABLE runs(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('plan','score','data')), title TEXT NOT NULL, created_at TEXT NOT NULL,
  inputs TEXT NOT NULL, result TEXT NOT NULL);
INSERT INTO runs(id,user_id,kind,title,created_at,inputs,result) SELECT id,user_id,kind,title,created_at,inputs,result FROM runs_old;
DROP TABLE runs_old;
CREATE INDEX IF NOT EXISTS runs_user ON runs(user_id, id DESC);""")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------------ passwords
    @staticmethod
    def hash_password(password):
        salt = secrets.token_bytes(16)
        try:
            n, r, p = 2 ** 14, 8, 1
            h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
            return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"
        except (AttributeError, ValueError):               # OpenSSL without scrypt
            it = 600_000
            h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, it, 32)
            return f"pbkdf2${it}${salt.hex()}${h.hex()}"

    @staticmethod
    def verify_password(password, stored):
        try:
            kind, *rest = stored.split("$")
            if kind == "scrypt":
                n, r, p, salt, h = int(rest[0]), int(rest[1]), int(rest[2]), bytes.fromhex(rest[3]), bytes.fromhex(rest[4])
                cand = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(h))
            else:
                it, salt, h = int(rest[0]), bytes.fromhex(rest[1]), bytes.fromhex(rest[2])
                cand = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, it, len(h))
            return hmac.compare_digest(cand, h)
        except Exception:
            return False

    # ------------------------------------------------------------------ users
    def create_user(self, email, name, password):
        email, name = email.strip(), name.strip()
        if not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address.")
        if not (1 <= len(name) <= 80):
            raise ValueError("Enter your name (up to 80 characters).")
        validate_password(password)
        with self.conn() as c:
            if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise ValueError("An account with this email already exists.")
            cur = c.execute("INSERT INTO users(email,name,pw_hash,created_at) VALUES(?,?,?,?)",
                            (email, name, self.hash_password(password), now_iso()))
            return cur.lastrowid

    def authenticate(self, email, password):
        with self.conn() as c:
            row = c.execute("SELECT * FROM users WHERE email=?", (email.strip(),)).fetchone()
        if row is None:                                   # burn comparable time so timing does not reveal accounts
            self.verify_password(password, self.hash_password("x" * 12))
            return None
        return row["id"] if self.verify_password(password, row["pw_hash"]) else None

    # ------------------------------------------------------------------ password reset
    def create_reset(self, email):
        """Returns (token, name, email) for an existing account, else None. Only one live token per user."""
        with self.conn() as c:
            u = c.execute("SELECT id,name,email FROM users WHERE email=?", (email.strip(),)).fetchone()
            if u is None:
                return None
            token = secrets.token_urlsafe(32)
            c.execute("DELETE FROM resets WHERE user_id=?", (u["id"],))
            c.execute("INSERT INTO resets(token_hash,user_id,expires_at) VALUES(?,?,?)",
                      (hashlib.sha256(token.encode()).hexdigest(), u["id"], time.time() + RESET_MINUTES * 60))
            return token, u["name"], u["email"]

    def reset_user(self, token):
        """User id if the token is live, else None."""
        th = hashlib.sha256((token or "").encode()).hexdigest()
        with self.conn() as c:
            c.execute("DELETE FROM resets WHERE expires_at<?", (time.time(),))
            r = c.execute("SELECT user_id FROM resets WHERE token_hash=?", (th,)).fetchone()
        return r["user_id"] if r else None

    def complete_reset(self, token, password):
        """Sets the new password, burns the token, and signs the account out everywhere."""
        validate_password(password)
        uid = self.reset_user(token)
        if uid is None:
            raise ValueError("This reset link is invalid or has expired. Request a new one.")
        with self.conn() as c:
            email = c.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()["email"]
            c.execute("UPDATE users SET pw_hash=? WHERE id=?", (self.hash_password(password), uid))
            c.execute("DELETE FROM resets WHERE user_id=?", (uid,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            c.execute("DELETE FROM fails WHERE key=?", ("email:" + email.lower(),))
        return uid

    # ------------------------------------------------------------------ throttling
    def too_many_fails(self, *keys):
        cutoff = time.time() - FAIL_WINDOW
        with self.conn() as c:
            c.execute("DELETE FROM fails WHERE ts<?", (cutoff,))
            for k in keys:
                if c.execute("SELECT COUNT(*) FROM fails WHERE key=? AND ts>=?", (k, cutoff)).fetchone()[0] >= MAX_FAILS:
                    return True
        return False

    def record_fail(self, *keys):
        with self.conn() as c:
            for k in keys:
                c.execute("INSERT INTO fails(key,ts) VALUES(?,?)", (k, time.time()))

    def clear_fails(self, *keys):
        with self.conn() as c:
            for k in keys:
                c.execute("DELETE FROM fails WHERE key=?", (k,))

    # ------------------------------------------------------------------ sessions
    def create_session(self, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.conn() as c:
            c.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)",
                      (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, time.time() + SESSION_DAYS * 86400))
        return token

    def get_session(self, token):
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        with self.conn() as c:
            row = c.execute("SELECT s.csrf, s.expires_at, u.id, u.email, u.name FROM sessions s JOIN users u ON u.id=s.user_id "
                            "WHERE s.token_hash=?", (th,)).fetchone()
            if row is None:
                return None
            if row["expires_at"] < time.time():
                c.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
                return None
            return {"user_id": row["id"], "email": row["email"], "name": row["name"], "csrf": row["csrf"]}

    def end_session(self, token):
        if token:
            with self.conn() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    # ------------------------------------------------------------------ runs (saved history)
    def save_run(self, user_id, kind, title, inputs, result):
        with self.conn() as c:
            cur = c.execute("INSERT INTO runs(user_id,kind,title,created_at,inputs,result) VALUES(?,?,?,?,?,?)",
                            (user_id, kind, title[:120], now_iso(), json.dumps(inputs), json.dumps(result)))
            return cur.lastrowid

    def list_runs(self, user_id, limit=200):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT id,kind,title,created_at FROM runs WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))]

    def get_run(self, user_id, run_id):
        with self.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["inputs"], d["result"] = json.loads(d["inputs"]), json.loads(d["result"])
        return d

    def last_inputs(self, user_id, kind):
        with self.conn() as c:
            r = c.execute("SELECT inputs FROM runs WHERE user_id=? AND kind=? ORDER BY id DESC LIMIT 1", (user_id, kind)).fetchone()
        return json.loads(r["inputs"]) if r else {}

    def delete_run(self, user_id, run_id):
        with self.conn() as c:
            return c.execute("DELETE FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).rowcount > 0
