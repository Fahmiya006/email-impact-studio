"""End-to-end tests: python -m unittest discover -s app/tests -v   (standard library only)"""
import http.client
import json
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import server

PASSWORD = "correct horse battery"
PLAN = {"list_size": 100000, "margin_pct": 40, "cost_per_contact": 0.10, "fixed_cost": 0, "share_both_pct": 10.1}
CUSTOMER = {"recency": 2, "history": 450, "mens": True, "womens": True, "newbie": False, "zip": "Urban", "channel": "Web",
            "margin_pct": 40, "cost_per_contact": 0.10}


class Client:
    def __init__(self, port):
        self.port, self.cookies = port, {}

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read().decode("utf-8", "replace")
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                val = rest.split(";")[0]
                if "Max-Age=0" in v:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = val
        conn.close()
        return r.status, dict(r.getheaders()), data

    def form_token(self, path):
        _, _, html_ = self.request("GET", path)
        return re.search(r'name="csrf" value="([^"]+)"', html_).group(1)

    def post_form(self, path, fields):
        return self.request("POST", path, urlencode(fields), {"Content-Type": "application/x-www-form-urlencoded"})

    def register(self, email, name="Test User", password=PASSWORD):
        tok = self.form_token("/register")
        return self.post_form("/register", {"csrf": tok, "email": email, "name": name, "password": password})

    def login(self, email, password=PASSWORD):
        tok = self.form_token("/login")
        return self.post_form("/login", {"csrf": tok, "email": email, "password": password})

    def csrf(self):
        _, _, html_ = self.request("GET", "/planner")
        return re.search(r'<meta name="csrf" content="([^"]+)"', html_).group(1)

    def api(self, path, payload, token=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-CSRF-Token"] = token
        s, hd, body = self.request("POST", path, json.dumps(payload), h)
        return s, json.loads(body)


class FakeMailer:
    configured = True

    def __init__(self):
        self.outbox = []

    def send(self, to, subject, body):
        self.outbox.append({"to": to, "subject": subject, "body": body})
        return True


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "t.db")
        cls.mail = FakeMailer()
        cls.srv = server.make_server("127.0.0.1", 0, cls.db, mailer=cls.mail)
        cls.srv.app.mail_sync = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.tmp.cleanup()

    def setUp(self):                                   # throttle counters are per IP, and every test shares 127.0.0.1
        con = sqlite3.connect(self.db); con.execute("DELETE FROM fails"); con.commit(); con.close()
        self.mail.outbox.clear()

    def db_bytes(self):
        """The database file plus its write-ahead log: recent writes live in the -wal file, so reading only t.db could miss them."""
        out = b""
        for suffix in ("", "-wal"):
            f = Path(self.db + suffix)
            if f.exists():
                with open(f, "rb") as fh:
                    out += fh.read()
        return out

    def new_user(self, email):
        c = Client(self.port)
        s, _, _ = c.register(email)
        self.assertEqual(s, 303)
        return c

    # ---------------------------------------------------------------- access control
    def test_requires_login(self):
        c = Client(self.port)
        for p in ["/planner", "/scorer", "/history", "/report/1", "/evidence"]:
            s, h, _ = c.request("GET", p)
            self.assertEqual((s, h["Location"]), (303, "/login"), p)
        s, _, _ = c.request("POST", "/api/plan", json.dumps(PLAN), {"Content-Type": "application/json"})
        self.assertEqual(s, 401)

    def test_security_headers(self):
        s, h, _ = Client(self.port).request("GET", "/login")
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Cache-Control"], "no-store")

    # ---------------------------------------------------------------- registration and login
    def test_register_validation_and_duplicates(self):
        c = Client(self.port)
        s, _, body = c.register("weak@example.com", password="short")
        self.assertEqual(s, 400); self.assertIn("at least 10 characters", body)
        s, _, body = c.register("not-an-email")
        self.assertEqual(s, 400); self.assertIn("valid email", body)
        self.assertEqual(c.register("dup@example.com")[0], 303)
        c2 = Client(self.port)
        s, _, body = c2.register("DUP@example.com")
        self.assertEqual(s, 400); self.assertIn("already exists", body)

    def test_password_is_hashed(self):
        self.new_user("hash@example.com")
        raw = self.db_bytes()
        self.assertIn(b"hash@example.com", raw)                              # the search really covers recent writes
        self.assertNotIn(PASSWORD.encode(), raw)
        con = sqlite3.connect(self.db)
        stored = con.execute("SELECT pw_hash FROM users WHERE email='hash@example.com'").fetchone()[0]
        con.close()
        self.assertTrue(stored.startswith(("scrypt$", "pbkdf2$")))

    def test_login_logout_and_throttle(self):
        self.new_user("login@example.com")
        c = Client(self.port)
        s, _, body = c.login("login@example.com", "wrong password!!")
        self.assertEqual(s, 401); self.assertIn("incorrect", body)
        s, h, _ = c.login("login@example.com")
        self.assertEqual((s, h["Location"]), (303, "/planner"))
        self.assertEqual(c.request("GET", "/planner")[0], 200)
        tok = c.csrf()
        self.assertEqual(c.post_form("/logout", {"csrf": "bad"})[0], 403)
        self.assertEqual(c.post_form("/logout", {"csrf": tok})[0], 303)
        self.assertEqual(c.request("GET", "/planner")[0], 303)
        t = Client(self.port)
        for _ in range(5):
            t.login("throttle@example.com", "wrong password!!")
        self.assertEqual(t.login("throttle@example.com", "wrong password!!")[0], 429)

    def test_other_requests_do_not_invalidate_open_form(self):
        c = Client(self.port)
        tok = c.form_token("/register")
        self.assertEqual(c.request("GET", "/favicon.ico")[0], 200)
        c.request("GET", "/login")                                   # e.g. a second tab
        s, _, _ = c.post_form("/register", {"csrf": tok, "email": "tabs@example.com", "name": "Tabs", "password": PASSWORD})
        self.assertEqual(s, 303)

    # ---------------------------------------------------------------- password reset
    def ask_reset(self, email):
        c = Client(self.port)
        tok = c.form_token("/forgot")
        s, _, body = c.post_form("/forgot", {"csrf": tok, "email": email})
        return c, s, body

    def link_from_mail(self, msg):
        return re.search(r"http://127\.0\.0\.1:\d+(/reset/[A-Za-z0-9_-]+)", msg["body"]).group(1)

    def test_forgot_does_not_reveal_which_accounts_exist(self):
        self.new_user("exists@example.com")
        _, s1, b1 = self.ask_reset("exists@example.com")
        n_after_known = len(self.mail.outbox)
        _, s2, b2 = self.ask_reset("nobody@example.com")
        self.assertEqual((s1, s2), (200, 200))
        self.assertIn("Check your email", b1); self.assertIn("Check your email", b2)
        self.assertEqual((n_after_known, len(self.mail.outbox)), (1, 1))          # nothing sent for the unknown address

    def test_full_reset_flow(self):
        old = self.new_user("flow@example.com")                                  # an open session that must be revoked
        self.ask_reset("flow@example.com")
        msg = self.mail.outbox[-1]; link = self.link_from_mail(msg)
        self.assertEqual(msg["to"], "flow@example.com")
        c = Client(self.port)
        s, _, page = c.request("GET", link)
        self.assertEqual(s, 200); self.assertIn("Choose a new password", page)
        tok = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.assertEqual(c.post_form(link, {"csrf": tok, "password": "another long pass", "confirm": "different one!!"})[0], 400)
        s, _, body = c.post_form(link, {"csrf": tok, "password": "short", "confirm": "short"})
        self.assertEqual(s, 400); self.assertIn("at least 10 characters", body)
        self.assertEqual(c.request("GET", link)[0], 200)                         # still usable after a mistake
        tok = re.search(r'name="csrf" value="([^"]+)"', c.request("GET", link)[2]).group(1)
        s, h, _ = c.post_form(link, {"csrf": tok, "password": "brand new password", "confirm": "brand new password"})
        self.assertEqual((s, h["Location"]), (303, "/login?reset=1"))
        self.assertIn("Password updated", Client(self.port).request("GET", "/login?reset=1")[2])
        self.assertEqual(Client(self.port).login("flow@example.com")[0], 401)    # old password dead
        self.assertEqual(Client(self.port).login("flow@example.com", "brand new password")[0], 303)
        self.assertEqual(old.request("GET", "/planner")[0], 303)                 # old session signed out
        self.assertEqual(c.request("GET", link)[0], 400)                         # single use

    def test_reset_link_expires(self):
        self.new_user("exp@example.com")
        self.ask_reset("exp@example.com"); link = self.link_from_mail(self.mail.outbox[-1])
        con = sqlite3.connect(self.db); con.execute("UPDATE resets SET expires_at=0"); con.commit(); con.close()
        self.assertEqual(Client(self.port).request("GET", link)[0], 400)

    def test_newer_request_replaces_older_link_and_tokens_are_hashed(self):
        self.new_user("twice@example.com")
        self.ask_reset("twice@example.com"); first = self.link_from_mail(self.mail.outbox[-1])
        self.ask_reset("twice@example.com"); second = self.link_from_mail(self.mail.outbox[-1])
        self.assertNotEqual(first, second)
        self.assertEqual(Client(self.port).request("GET", first)[0], 400)
        self.assertEqual(Client(self.port).request("GET", second)[0], 200)
        raw = self.db_bytes()
        self.assertIn(b"twice@example.com", raw)
        self.assertNotIn(second.rsplit("/", 1)[1].encode(), raw)

    def test_reset_requires_csrf_and_forgot_is_throttled(self):
        self.new_user("csrf@example.com")
        self.ask_reset("csrf@example.com"); link = self.link_from_mail(self.mail.outbox[-1])
        c = Client(self.port); c.request("GET", link)
        s, _, _ = c.post_form(link, {"csrf": "forged", "password": "brand new password", "confirm": "brand new password"})
        self.assertEqual(s, 403)
        statuses = [self.ask_reset(f"spam{i}@example.com")[1] for i in range(7)]
        self.assertEqual(statuses[-1], 429)

    def test_login_requires_matching_csrf(self):
        c = Client(self.port)
        c.request("GET", "/login")
        s, _, _ = c.post_form("/login", {"csrf": "forged", "email": "x@example.com", "password": PASSWORD})
        self.assertEqual(s, 403)

    # ---------------------------------------------------------------- tools, history, reports
    def test_plan_flow_history_report(self):
        c = self.new_user("plan@example.com")
        tok = c.csrf()
        s, _ = c.api("/api/plan", PLAN)                                  # missing CSRF header
        self.assertEqual(s, 403)
        s, body = c.api("/api/plan", {**PLAN, "label": "<script>alert(1)</script>"}, tok)
        self.assertEqual(s, 200)
        self.assertIn("Policy comparison", body["html"])
        rid = body["id"]
        _, _, hist = c.request("GET", "/history")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", hist); self.assertNotIn("<script>alert(1)", hist)
        s, h, rep = c.request("GET", f"/report/{rid}")
        self.assertEqual(s, 200); self.assertIn("Print or save as PDF", rep); self.assertNotIn("<script>alert(1)", rep)
        s, h, rep = c.request("GET", f"/report/{rid}/download")
        self.assertIn("attachment", h["Content-Disposition"]); self.assertNotIn("printBtn", rep)
        self.assertEqual(c.request("GET", f"/history/{rid}")[0], 200)
        # saved inputs prefill the form next time
        _, _, page = c.request("GET", "/planner")
        self.assertIn('value="100000"', page)

    def test_score_flow(self):
        c = self.new_user("score@example.com")
        tok = c.csrf()
        s, body = c.api("/api/score", CUSTOMER, tok)
        self.assertEqual(s, 200); self.assertIn("Results per customer", body["html"])
        s, body = c.api("/api/score", {**CUSTOMER, "mens": False, "womens": False}, tok)
        self.assertEqual(s, 400); self.assertIn("at least one product line", body["error"])

    def test_validation_errors(self):
        c = self.new_user("valid@example.com")
        tok = c.csrf()
        for bad in [{**PLAN, "margin_pct": 0}, {**PLAN, "list_size": "abc"}, {**PLAN, "cost_per_contact": -1}, {**PLAN, "share_both_pct": 150}]:
            self.assertEqual(c.api("/api/plan", bad, tok)[0], 400, bad)
        self.assertEqual(c.api("/api/score", {**CUSTOMER, "zip": "Moon"}, tok)[0], 400)
        s, _, _ = c.request("POST", "/api/plan", "not json", {"Content-Type": "application/json", "X-CSRF-Token": tok})
        self.assertEqual(s, 400)

    def test_users_cannot_see_each_others_runs(self):
        a, b = self.new_user("a@example.com"), self.new_user("b@example.com")
        _, body = a.api("/api/plan", PLAN, a.csrf())
        rid = body["id"]
        for p in [f"/history/{rid}", f"/report/{rid}", f"/report/{rid}/download"]:
            self.assertEqual(b.request("GET", p)[0], 404, p)
        self.assertEqual(b.post_form(f"/history/{rid}/delete", {"csrf": b.csrf()})[0], 303)
        self.assertEqual(a.request("GET", f"/history/{rid}")[0], 200)            # still there
        self.assertEqual(a.post_form(f"/history/{rid}/delete", {"csrf": a.csrf()})[0], 303)
        self.assertEqual(a.request("GET", f"/history/{rid}")[0], 404)

    def test_login_page_layout_title_box_headline(self):
        _, _, page = Client(self.port).request("GET", "/login")
        title, box, headline = page.index('class="auth-title"'), page.index('class="auth card"'), page.index("Compare email campaigns and score customers")
        self.assertTrue(title < box < headline)                          # title on top, login box in the middle, headline below
        self.assertNotIn("data-set-theme", page)                         # no theme switch: the page follows the device setting
        self.assertIn('class="auth-logo"', page); self.assertIn('rel="icon"', page)   # round logo and tab icon

    def test_evidence_page_keeps_the_app_menu(self):
        c = self.new_user("evidence@example.com")
        s, h, page = c.request("GET", "/evidence")
        self.assertEqual(s, 200)
        self.assertIn('<aside class="side"', page); self.assertIn('<nav class="nav"', page); self.assertIn('href="/planner"', page); self.assertIn('href="/scorer"', page)
        self.assertRegex(page, r'<a href="/evidence" aria-current="page">')
        self.assertIn('action="/logout"', page)
        self.assertNotIn("APP_HEADER", page.replace("<!--APP_HEADER-->", ""))   # placeholder was replaced, not left behind
        self.assertNotIn("fonts.googleapis", page)

    def test_no_ask_no_theme_switch_and_logo(self):
        c = self.new_user("dock@example.com")
        for path in ("/planner", "/scorer", "/history", "/evidence"):
            page = c.request("GET", path)[2]
            self.assertNotIn('class="dock"', page); self.assertNotIn("data-ask", page); self.assertNotIn("data-set-theme", page)
            self.assertIn('class="logo"', page); self.assertIn("/static/logo-128.png", page); self.assertIn('rel="icon"', page)
        tok = c.csrf()
        s, body = c.api("/api/plan", PLAN, tok)
        self.assertEqual(s, 200)
        html = body["html"]
        self.assertIn('class="insight i1"', html)                        # notes are coloured rows
        self.assertIn('class="kpi g1"', html); self.assertIn("<circle", html)   # gradient number cards and a dot-matrix strip
        self.assertIn("PROFIT: above $0", html); self.assertIn("How to read this chart", html)   # the chart explains itself
        self.assertIn("At your cost of", html)

    def test_report_is_an_official_document(self):
        c = self.new_user("official-report@example.com")
        tok = c.csrf()
        s, body = c.api("/api/plan", PLAN, tok)
        rid = body["id"]
        for path in (f"/report/{rid}", f"/report/{rid}/download"):
            s, h, page = c.request("GET", path)
            self.assertEqual(s, 200)
            for needle in ("Document control", "EIS-P-", "Executive summary", "Inputs and assumptions", "Policy comparison", "Methodology", "Glossary",
                           "Authorisation", "Confidential", "Campaign Profit Report", "data:image/png;base64"):
                self.assertIn(needle, page, needle)
            self.assertNotRegex(page, r"<p[ >]")
        self.assertIn("printBtn", c.request("GET", f"/report/{rid}")[2])
        self.assertNotIn("printBtn", c.request("GET", f"/report/{rid}/download")[2])

    def test_fonts_and_background_are_served(self):
        c = Client(self.port)
        for path, ctype in [("/static/fonts/Carlito-Regular.subset.ttf", "font/ttf"), ("/static/fonts/Carlito-Bold.subset.ttf", "font/ttf"),
                            ("/static/bg-light.svg", "image/svg+xml"), ("/static/bg-dark.svg", "image/svg+xml"), ("/static/header.css", "text/css"),
                            ("/static/logo-128.png", "image/png"), ("/static/logo-512.png", "image/png"), ("/static/favicon-64.png", "image/png"), ("/favicon.ico", "image/x-icon")]:
            s, h, _ = c.request("GET", path)
            self.assertEqual(s, 200, path); self.assertTrue(h["Content-Type"].startswith(ctype), path)
        self.assertEqual(c.request("GET", "/static/fonts/../app.css")[0], 404)

    def test_results_use_tables_not_paragraphs(self):
        c = self.new_user("tables@example.com")
        tok = c.csrf()
        _, plan = c.api("/api/plan", PLAN, tok)
        _, score = c.api("/api/score", CUSTOMER, tok)
        for html in (plan["html"], score["html"]):
            self.assertNotRegex(html, r"<p[ >]")                          # no paragraphs in results
            self.assertIn("<table", html); self.assertIn("Why", html)     # every finding comes with a reason
            self.assertNotIn("versus", html)
        for path in ("/planner", "/scorer", "/history"):
            _, _, page = c.request("GET", path)
            self.assertEqual(re.findall(r'<p[ >][^>]*', page.replace('<p class="error" id="formError" role="alert" hidden>', "")), [], path)
        self.assertIn("Expected gain vs no email", score["html"])
        self.assertNotIn('class="tile"', plan["html"] + score["html"])      # data is shown as plain tables, not in tiles

    def test_report_embeds_calibri_and_is_self_contained(self):
        c = self.new_user("report@example.com")
        _, body = c.api("/api/plan", PLAN, c.csrf())
        _, _, rep = c.request("GET", f"/report/{body['id']}/download")
        self.assertIn("font-face", rep); self.assertIn("data:font/ttf;base64,", rep)
        self.assertNotRegex(rep, r"<p[ >]")

    def test_style_rules(self):
        import colorsys
        base = Path(__file__).resolve().parents[1] / "static"
        css = (base / "app.css").read_text() + (base / "header.css").read_text()
        text_colours = set(re.findall(r"(?<![-\w])color:\s*([^;}\s]+)", css))
        self.assertTrue(text_colours <= {"var(--ink)", "var(--on-ink)", "var(--accent-ink)", "#ffffff"}, text_colours)   # black or white only
        self.assertNotIn("border-left", css)                              # no one-sided accent borders
        self.assertNotRegex(css, r"dashed|dotted")                        # no dashed or dotted lines
        self.assertNotIn("opacity", css.replace("opacity:1", ""))         # no faded, grey-looking text
        fams = set(re.findall(r"font-family:\s*([^;}]+)", css))
        self.assertTrue(fams <= {"var(--font)", '"Calibri"'}, fams)        # Calibri only
        self.assertIn("--font:Calibri,sans-serif", css)
        self.assertIn(".kpi{", css); self.assertIn("linear-gradient", css)  # gradient number cards, as in the references
        for hexv in set(re.findall(r"#([0-9a-fA-F]{6})\b", css)):
            r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (0, 2, 4))
            h, l, sat = colorsys.rgb_to_hls(r, g, b)
            self.assertFalse(sat < 0.15 and 0.04 < l < 0.96, "grey colour #" + hexv)

    def test_dashboard_follows_the_same_rules(self):
        html = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text()
        html = re.sub(r"data:[a-z/+-]+;base64,[A-Za-z0-9+/=]+", "data:", html)       # embedded font and picture bytes are not rules
        self.assertNotRegex(html, r"dasharray|dashed|dotted")
        self.assertNotIn("<p ", html); self.assertNotIn("<p>", html)
        self.assertNotIn("fonts.googleapis", html)
        self.assertIn("/*APP_HEADER_CSS*/", html); self.assertIn("<!--APP_HEADER-->", html)

    def test_static_whitelist(self):
        c = Client(self.port)
        self.assertEqual(c.request("GET", "/static/app.css")[0], 200)
        self.assertEqual(c.request("GET", "/static/../server.py")[0], 404)
        self.assertEqual(c.request("GET", "/static/%2e%2e/store.py")[0], 404)


class EngineTests(unittest.TestCase):
    model = engine.load_model()

    def test_planner_breakeven_logic(self):
        cheap = engine.plan(PLAN, self.model)
        self.assertEqual(cheap["best"], "mens_all")
        costly = engine.plan({**PLAN, "cost_per_contact": 1.0}, self.model)
        self.assertEqual(costly["best"], "none")
        self.assertAlmostEqual(cheap["breakeven"]["value"], 0.307, delta=0.01)

    def test_planner_scales_linearly_with_list_size(self):
        a, b = engine.plan(PLAN, self.model), engine.plan({**PLAN, "list_size": 200000}, self.model)
        self.assertAlmostEqual(b["policies"][1]["profit_total"], 2 * a["policies"][1]["profit_total"], places=3)

    def test_scorer_outputs_are_sane(self):
        r = engine.score(CUSTOMER, self.model)
        for a in r["arms"]:
            self.assertTrue(0 < a["p_visit"] < 1)
            self.assertLessEqual(a["gain_ci"][0], a["gain_ci"][1])
        self.assertEqual(r["arms"][0]["gain_vs_none"], 0)
        clipped = engine.score({**CUSTOMER, "history": 99999}, self.model)
        self.assertTrue(any("outside the model" in n[0] for n in clipped["notes"]))

    def test_percentile(self):
        self.assertEqual(engine.pct([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(engine.interval(list(range(101)))[0], 2.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
