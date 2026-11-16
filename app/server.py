"""Email Impact Studio: a small web app with real accounts (standard library only).

Run:   python app/server.py            then open http://127.0.0.1:8000
Env:   HOST, PORT, DB_PATH, SECURE_COOKIES=1 (when served over HTTPS), ALLOW_REGISTRATION=0 (close sign-ups),
       TRUST_PROXY=1 (read the client IP from X-Forwarded-For when behind a reverse proxy you control)
       BASE_URL (public address used in reset links, e.g. https://studio.example.com), SMTP_HOST/PORT/USER/PASSWORD/FROM/SECURITY
CLI:   python app/server.py create-user EMAIL "Full Name"      (asks for the password)
       python app/server.py reset-link EMAIL                   (prints a one-time reset link; for admins without email set up)
"""
import hmac
import json
import os
import re
import secrets
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import analyze
import engine
import uploads
import pages
from mailer import Mailer
from store import Store, SESSION_DAYS, RESET_MINUTES

HERE = Path(__file__).parent
STATIC = HERE / "static"
STATIC_TYPES = {"app.css": "text/css; charset=utf-8", "header.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8",
                "print.js": "application/javascript; charset=utf-8", "data.js": "application/javascript; charset=utf-8",
                "bg-light.svg": "image/svg+xml", "bg-dark.svg": "image/svg+xml",
                "logo-512.png": "image/png", "logo-128.png": "image/png", "favicon-64.png": "image/png", "apple-touch-icon.png": "image/png",
                "fonts/Carlito-Regular.subset.ttf": "font/ttf", "fonts/Carlito-Bold.subset.ttf": "font/ttf"}
MAX_BODY = 64 * 1024
APP_CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; "
           "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; img-src data:; font-src data:; base-uri 'none'; frame-ancestors 'none'")
EVIDENCE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; font-src data:; form-action 'self'; "
                "script-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")

PLAN_KEYS = ["list_size", "margin_pct", "cost_per_contact", "fixed_cost", "share_both_pct"]
SCORE_KEYS = ["recency", "history", "mens", "womens", "newbie", "zip", "channel", "margin_pct", "cost_per_contact"]


class App:
    def __init__(self, db_path=None, secure=False, allow_registration=True, trust_proxy=False, base_url=None, mailer=None):
        self.store = Store(db_path or os.environ.get("DB_PATH") or HERE / "data" / "app.db")
        self.mailer = mailer or Mailer.from_env()
        self.base_url = (base_url or "").rstrip("/")
        self.mail_sync = False                       # tests set this so they can read the message immediately
        self.model = engine.load_model()
        self.secure = secure
        self.allow_registration = allow_registration
        self.trust_proxy = trust_proxy

    def send_mail(self, to, subject, body):
        """Sent in the background so response time does not reveal whether an address has an account."""
        if self.mail_sync:
            return self.mailer.send(to, subject, body)
        threading.Thread(target=self.mailer.send, args=(to, subject, body), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    app = None            # set by make_server
    timeout = 30
    server_version = "EmailImpactStudio"
    sys_version = ""

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _cookies(self):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        return {k: v.value for k, v in c.items()}

    def _ip(self):
        if self.app.trust_proxy:
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[-1].strip()
        return self.client_address[0]

    def _send(self, status, body, ctype="text/html; charset=utf-8", csp=APP_CSP, headers=None, cache="no-store"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", cache)
        if self.app.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, where, cookies=None):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()

    def _cookie(self, name, value, max_age):
        s = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        return s + ("; Secure" if self.app.secure else "")

    def _pre(self):
        """Pre-login CSRF token: reuse the cookie so a second tab or stray request cannot invalidate an open form."""
        pre = self._cookies().get("pre_csrf", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", pre):
            pre = secrets.token_urlsafe(24)
        return pre, [("Set-Cookie", self._cookie("pre_csrf", pre, 3600))]

    def _json(self, status, obj):
        self._send(status, json.dumps(obj), "application/json; charset=utf-8")

    def _read_body(self, limit=MAX_BODY):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            return None
        return self.rfile.read(n)

    def _form(self):
        raw = self._read_body()
        if raw is None:
            return None
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def _session(self):
        return self.app.store.get_session(self._cookies().get("session"))

    def _require_user(self):
        sess = self._session()
        if not sess:
            if self.command in ("GET", "HEAD"):
                self._redirect("/login")
            else:
                self._send(401, "Please sign in.", "text/plain; charset=utf-8")
            return None
        return sess

    @staticmethod
    def _csrf_ok(expected, given):
        return bool(expected) and bool(given) and hmac.compare_digest(expected, given)

    # ------------------------------------------------------------------ routing
    def do_GET(self):
        try:
            self._get()
        except Exception as ex:                                   # never leak a traceback to the browser
            sys.stderr.write(f"error: {ex!r}\n")
            self._send(500, pages.layout("Error", "<section class='card'><h1>Something went wrong</h1><p>Please try again.</p></section>"))

    do_HEAD = do_GET                      # uptime monitors often probe with HEAD

    def do_POST(self):
        try:
            self._post()
        except Exception as ex:
            sys.stderr.write(f"error: {ex!r}\n")
            self._send(500, "Server error.", "text/plain; charset=utf-8")

    def _get(self):
        path = urlsplit(self.path).path
        app, user_sess = self.app, None
        if path == "/favicon.ico":
            return self._send(200, (STATIC / "favicon.ico").read_bytes(), "image/x-icon", csp="default-src 'none'", cache="public, max-age=86400")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name in STATIC_TYPES:
                return self._send(200, (STATIC / name).read_bytes(), STATIC_TYPES[name], csp="default-src 'none'", cache="public, max-age=300")
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if path in ("/login", "/register"):
            if self._session():
                return self._redirect("/planner")
            if path == "/register" and not app.allow_registration:
                return self._send(403, pages.message_page(None, "", "Sign-ups are closed", "Ask the administrator for an account."))
            pre, hdr = self._pre()
            note = "Password updated. Sign in with your new password." if (path == "/login" and "reset=1" in urlsplit(self.path).query) else ""
            return self._send(200, pages.auth_page(path[1:], pre, allow_register=app.allow_registration, note=note), headers=hdr)
        if path == "/forgot":
            pre, hdr = self._pre()
            return self._send(200, pages.forgot_page(pre), headers=hdr)
        m = re.fullmatch(r"/reset/([A-Za-z0-9_-]{20,64})", path)
        if m:
            if app.store.reset_user(m.group(1)) is None:
                return self._send(400, pages.message_page(None, "", "Link expired", "This reset link is invalid or has expired. Request a new one on the sign-in page."))
            pre, hdr = self._pre()
            return self._send(200, pages.reset_page(m.group(1), pre), headers=hdr)
        if path == "/":
            return self._redirect("/planner" if self._session() else "/login")

        sess = self._require_user()
        if not sess:
            return
        uid = sess["user_id"]
        if path == "/planner":
            return self._send(200, pages.planner_page(sess, sess["csrf"], app.store.last_inputs(uid, "plan")))
        if path == "/data":
            return self._send(200, pages.data_page(sess, sess["csrf"]))
        if path == "/scorer":
            return self._send(200, pages.scorer_page(sess, sess["csrf"], app.store.last_inputs(uid, "score")))
        if path == "/history":
            return self._send(200, pages.history_page(sess, sess["csrf"], app.store.list_runs(uid)))
        if path == "/evidence":
            f = HERE.parent / "dashboard" / "index.html"
            if not f.exists():
                return self._send(404, pages.message_page(sess, sess["csrf"], "Evidence page not built", "Run python src/run_all.py to build the dashboard."))
            page = (f.read_text()
                    .replace("/*APP_HEADER_CSS*/", (STATIC / "header.css").read_text())
                    .replace("<!--APP_HEADER-->", pages.header_html(sess, sess["csrf"], "evidence")))
            return self._send(200, page, csp=EVIDENCE_CSP)
        m = re.fullmatch(r"/history/(\d+)", path)
        if m:
            run = app.store.get_run(uid, int(m.group(1)))
            if not run:
                return self._send(404, pages.message_page(sess, sess["csrf"], "Not found", "That run does not exist."))
            return self._send(200, pages.run_page(sess, sess["csrf"], run))
        m = re.fullmatch(r"/report/(\d+)(/download)?", path)
        if m:
            run = app.store.get_run(uid, int(m.group(1)))
            if not run:
                return self._send(404, pages.message_page(sess, sess["csrf"], "Not found", "That run does not exist."))
            if m.group(2):
                fn = f"email-impact-report-{run['id']}.html"
                return self._send(200, pages.report_html(run, sess, download=True), csp=REPORT_CSP,
                                  headers=[("Content-Disposition", f'attachment; filename="{fn}"')])
            return self._send(200, pages.report_html(run, sess), csp=REPORT_CSP)
        return self._send(404, pages.message_page(sess, sess["csrf"], "Not found", "That page does not exist."))

    def _post(self):
        path = urlsplit(self.path).path
        app = self.app
        if path in ("/login", "/register"):
            return self._auth_post(path[1:])
        if path == "/forgot":
            return self._forgot_post()
        m = re.fullmatch(r"/reset/([A-Za-z0-9_-]{20,64})", path)
        if m:
            return self._reset_post(m.group(1))
        sess = self._require_user()
        if not sess:
            return
        uid = sess["user_id"]
        if path == "/logout":
            f = self._form()
            if not f or not self._csrf_ok(sess["csrf"], f.get("csrf")):
                return self._send(403, "Bad request token.", "text/plain; charset=utf-8")
            app.store.end_session(self._cookies().get("session"))
            return self._redirect("/login", [self._cookie("session", "", 0)])
        m = re.fullmatch(r"/history/(\d+)/delete", path)
        if m:
            f = self._form()
            if not f or not self._csrf_ok(sess["csrf"], f.get("csrf")):
                return self._send(403, "Bad request token.", "text/plain; charset=utf-8")
            app.store.delete_run(uid, int(m.group(1)))
            return self._redirect("/history")
        if path == "/api/dataset":
            return self._dataset_post(sess)
        if path == "/api/analyze":
            return self._analyze_post(sess)
        if path in ("/api/plan", "/api/score"):
            if not self._csrf_ok(sess["csrf"], self.headers.get("X-CSRF-Token", "")):
                return self._json(403, {"error": "Your session token is invalid. Reload the page and try again."})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(415, {"error": "Send JSON."})
            raw = self._read_body()
            try:
                data = json.loads(raw.decode()) if raw is not None else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return self._json(400, {"error": "Could not read the form data."})
            return self._run_tool(path == "/api/plan", sess, data)
        return self._send(404, "Not found", "text/plain; charset=utf-8")

    # ------------------------------------------------------------------ uploaded data
    def _upload_dir(self):
        return Path(self.app.store.path).parent

    def _dataset_post(self, sess):
        """Receive a CSV file as the raw request body, keep it for a day, and describe its columns."""
        if not self._csrf_ok(sess["csrf"], self.headers.get("X-CSRF-Token", "")):
            return self._json(403, {"error": "Your session token is invalid. Reload the page and try again."})
        raw = self._read_body(analyze.MAX_BYTES)
        if raw is None:
            return self._json(413, {"error": f"The file is larger than {analyze.MAX_BYTES // (1024 * 1024)} MB."})
        from urllib.parse import unquote
        name = re.sub(r"[^\w.\- ]", "_", unquote(self.headers.get("X-Filename", "data.csv")))[:80] or "data.csv"
        try:
            info = analyze.describe(raw, name)
        except analyze.DataError as ex:
            return self._json(400, {"error": str(ex)})
        info.pop("header", None)
        info["dataset_id"] = uploads.save(self._upload_dir(), sess["user_id"], name, raw)
        return self._json(200, info)

    def _analyze_post(self, sess):
        if not self._csrf_ok(sess["csrf"], self.headers.get("X-CSRF-Token", "")):
            return self._json(403, {"error": "Your session token is invalid. Reload the page and try again."})
        raw = self._read_body()
        try:
            data = json.loads(raw.decode()) if raw is not None else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self._json(400, {"error": "Could not read the form data."})
        up = uploads.load(self._upload_dir(), sess["user_id"], data.get("dataset_id"))
        if up is None:
            return self._json(400, {"error": "That file is no longer stored (files are kept for 24 hours). Upload it again."})
        text = lambda k: str(data.get(k) or "").strip()[:200]
        listed = lambda k: [str(x)[:200] for x in (data.get(k) or []) if isinstance(x, str)][:12] if isinstance(data.get(k), list) else []
        mapping = {"group": text("group"), "control": text("control"), "outcomes": listed("outcomes"), "revenue": text("revenue"), "pre": text("pre"),
                   "features": listed("features"), "margin_pct": data.get("margin_pct"), "cost_per_contact": data.get("cost_per_contact"),
                   "list_size": data.get("list_size"), "fixed_cost": data.get("fixed_cost")}
        label = text("label")[:80]
        try:
            result = analyze.analyze(up[0], mapping, up[1])
        except analyze.DataError as ex:
            return self._json(400, {"error": str(ex)})
        title = label or f"Your data: {up[1]}, {result['dataset']['rows_used']:,} customers"
        rid = self.app.store.save_run(sess["user_id"], "data", title, mapping, result)
        return self._json(200, {"id": rid, "html": pages.render_result({"kind": "data", "result": result})})

    # ------------------------------------------------------------------ handlers
    def _run_tool(self, is_plan, sess, data):
        keys = PLAN_KEYS if is_plan else SCORE_KEYS
        inputs = {k: data.get(k) for k in keys if k in data}
        label = str(data.get("label") or "").strip()[:80]
        try:
            if is_plan:
                result = engine.plan(inputs, self.app.model)
                title = label or f"Plan: {int(result['inputs']['list_size']):,} customers, {result['inputs']['margin']:.0%} margin, {engine.money(result['inputs']['cost_per_contact'])} per contact"
            else:
                result = engine.score(inputs, self.app.model)
                c = result["customer"]
                title = label or f"Customer: {c['recency']} mo since purchase, {engine.money(c['history'], 0)} spent"
        except ValueError as ex:
            return self._json(400, {"error": str(ex)})
        kind = "plan" if is_plan else "score"
        rid = self.app.store.save_run(sess["user_id"], kind, title, inputs, result)
        run = {"kind": kind, "result": result}
        return self._json(200, {"id": rid, "html": pages.render_result(run)})

    def _forgot_post(self):
        app = self.app
        f = self._form()
        pre = self._cookies().get("pre_csrf")
        if not f or not self._csrf_ok(pre, f.get("csrf")):
            return self._send(403, pages.forgot_page(secrets.token_urlsafe(24), error="Your form expired. Please try again."))
        email = f.get("email", "").strip()
        keys = ("rst-ip:" + self._ip(), "rst-email:" + email.lower())
        if app.store.too_many_fails(*keys):
            return self._send(429, pages.forgot_page(pre, error="Too many requests. Try again in ten minutes."))
        app.store.record_fail(*keys)                      # counts every request toward the limit
        res = app.store.create_reset(email)
        if res:
            token, name, addr = res
            link = f"{app.base_url}/reset/{token}"
            body = (f"Hello {name},\n\nSomeone asked to reset the password for your {pages.APP_NAME} account. Open this link to choose a new password. "
                    f"It works once and expires in {RESET_MINUTES} minutes:\n\n{link}\n\nIf you did not ask for this, ignore this email. Your password will not change.\n")
            app.send_mail(addr, f"Reset your {pages.APP_NAME} password", body)
        return self._send(200, pages.forgot_page(pre, sent=True))        # same answer whether or not the account exists

    def _reset_post(self, token):
        app = self.app
        f = self._form()
        pre = self._cookies().get("pre_csrf")
        if not f or not self._csrf_ok(pre, f.get("csrf")):
            return self._send(403, pages.reset_page(token, secrets.token_urlsafe(24), "Your form expired. Please try again."))
        if f.get("password", "") != f.get("confirm", ""):
            return self._send(400, pages.reset_page(token, pre, "The two passwords do not match."))
        try:
            app.store.complete_reset(token, f.get("password", ""))
        except ValueError as ex:
            if "invalid or has expired" in str(ex):
                return self._send(400, pages.message_page(None, "", "Link expired", str(ex)))
            return self._send(400, pages.reset_page(token, pre, str(ex)))
        self._redirect("/login?reset=1", [self._cookie("session", "", 0), self._cookie("pre_csrf", "", 0)])

    def _auth_post(self, kind):
        app = self.app
        f = self._form()
        pre = self._cookies().get("pre_csrf")
        if not f or not self._csrf_ok(pre, f.get("csrf")):
            return self._send(403, pages.auth_page(kind, secrets.token_urlsafe(24), "Your form expired. Please try again.", allow_register=app.allow_registration))
        email, password, name = f.get("email", ""), f.get("password", ""), f.get("name", "")
        fresh = secrets.token_urlsafe(24)
        retry = [("Set-Cookie", self._cookie("pre_csrf", fresh, 3600))]
        if kind == "register":
            if not app.allow_registration:
                return self._send(403, pages.message_page(None, "", "Sign-ups are closed", "Ask the administrator for an account."))
            try:
                uid = app.store.create_user(email, name, password)
            except ValueError as ex:
                return self._send(400, pages.auth_page("register", fresh, str(ex), email, name), headers=retry)
        else:
            keys = ("ip:" + self._ip(), "email:" + email.strip().lower())
            if app.store.too_many_fails(*keys):
                return self._send(429, pages.auth_page("login", fresh, "Too many failed attempts. Wait ten minutes and try again.", email), headers=retry)
            uid = app.store.authenticate(email, password)
            if uid is None:
                app.store.record_fail(*keys)
                return self._send(401, pages.auth_page("login", fresh, "Email or password is incorrect.", email), headers=retry)
            app.store.clear_fails(*keys)
        token = app.store.create_session(uid)
        self._redirect("/planner", [self._cookie("session", token, SESSION_DAYS * 86400), self._cookie("pre_csrf", "", 0)])


def make_server(host="127.0.0.1", port=8000, db_path=None, secure=False, allow_registration=True, trust_proxy=False, base_url=None, mailer=None):
    app = App(db_path, secure, allow_registration, trust_proxy, base_url, mailer)
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    if not app.base_url:                                # never built from the request's Host header
        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        app.base_url = f"http://{shown}:{srv.server_address[1]}"
    srv.app = app
    return srv


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "create-user":
        import getpass
        s = Store(os.environ.get("DB_PATH") or HERE / "data" / "app.db")
        pw = getpass.getpass("Password (min 10 characters): ")
        try:
            s.create_user(sys.argv[2], sys.argv[3], pw)
            print("Created.")
        except ValueError as ex:
            sys.exit(str(ex))
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "reset-link":
        s = Store(os.environ.get("DB_PATH") or HERE / "data" / "app.db")
        res = s.create_reset(sys.argv[2])
        if not res:
            sys.exit("No account with that email.")
        base = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        print(f"{base}/reset/{res[0]}\n(valid for {RESET_MINUTES} minutes, works once)")
        return
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000"))
    srv = make_server(host, port, os.environ.get("DB_PATH"), os.environ.get("SECURE_COOKIES") == "1",
                      os.environ.get("ALLOW_REGISTRATION", "1") != "0", os.environ.get("TRUST_PROXY") == "1",
                      os.environ.get("BASE_URL"))
    if not srv.app.mailer.configured:
        print("Note: SMTP is not configured, so password-reset links are printed here instead of emailed (local use only).")
    print(f"Email Impact Studio running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
