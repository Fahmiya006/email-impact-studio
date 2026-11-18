"""Tests for the "Your data" feature: python -m unittest discover -s app/tests -v   (standard library only)"""
import csv
import io
import json
import random
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze
import server
import store
from test_app import Client, FakeMailer

ROOT = Path(__file__).resolve().parents[2]


def synthetic(n=4000, seed=7, uniform_arms=True):
    """Three arms; email_a lifts purchase by 5 points for everyone; email_b only in the North. Order values are about $60."""
    rnd = random.Random(seed)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["customer_id", "campaign_arm", "prior_spend", "region", "purchased", "order_value"])
    for i in range(n):
        arm = rnd.choice(["control", "email_a", "email_b"])
        pre = max(0.0, rnd.gauss(50, 20))
        region = rnd.choice(["North", "South"])
        p = 0.10 + pre / 1000 + (0.05 if arm == "email_a" else 0.05 if arm == "email_b" and region == "North" else 0.0)
        buy = rnd.random() < p
        w.writerow([i, arm, round(pre, 2), region, int(buy), round(max(0.0, rnd.gauss(60, 20)), 2) if buy else 0])
    return out.getvalue().encode()


MAPPING = {"group": "campaign_arm", "control": "control", "outcomes": ["purchased", "order_value"], "revenue": "order_value", "pre": "prior_spend",
           "features": ["region"], "margin_pct": 40, "cost_per_contact": 0.10, "list_size": 100000, "fixed_cost": 0}


class AnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = synthetic()
        cls.r = analyze.analyze(cls.raw, dict(MAPPING), "synthetic.csv")

    def test_describe_suggests_the_right_columns(self):
        s = analyze.describe(self.raw, "synthetic.csv")["suggest"]
        self.assertEqual((s["group"], s["control"], s["revenue"], s["pre"]), ("campaign_arm", "control", "order_value", "prior_spend"))
        self.assertEqual(s["outcomes"], ["purchased", "order_value"])
        self.assertNotIn("customer_id", s["features"])

    def test_finds_the_known_lift_with_a_consistent_interval(self):
        o = self.r["outcomes"][0]
        a = next(e for e in o["effects"] if e["arm"] == "email_a")
        self.assertAlmostEqual(a["diff"], 0.05, delta=0.03)
        self.assertLess(a["ci"][0], a["diff"]); self.assertGreater(a["ci"][1], a["diff"])
        self.assertGreaterEqual(a["p_adj"], 0.0); self.assertLessEqual(a["p_adj"], 1.0)
        self.assertGreater(a["ci"][0], 0)                                   # a 5-point lift on 4,000 customers is detectable

    def test_cuped_does_not_change_the_average_effect_much(self):
        o = self.r["outcomes"][0]
        e = next(e for e in o["effects"] if e["arm"] == "email_a")
        self.assertIsNotNone(o["cuped"])
        self.assertAlmostEqual(e["adj"]["diff"], e["diff"], delta=0.02)

    def test_profit_policies_and_best(self):
        pr = self.r["profit"]
        self.assertEqual([p["key"] for p in pr["policies"]][0], "none")
        self.assertIn(pr["best"], [p["name"] for p in pr["policies"]])
        none = pr["policies"][0]
        self.assertEqual(none["gain_total"], 0)
        for p in pr["policies"]:
            self.assertLessEqual(p["ci_total"][0], p["profit_total"]); self.assertGreaterEqual(p["ci_total"][1], p["profit_total"])

    def test_segments_show_the_north_only_effect(self):
        rows = {r["level"]: r for r in self.r["segments"]["rows"]}
        arms = self.r["segments"]["arms"]
        b = arms.index("email_b")
        self.assertGreater(rows["region = North"]["cells"][b]["diff"], rows["region = South"]["cells"][b]["diff"])

    def test_no_revenue_means_no_profit_section(self):
        m = dict(MAPPING, outcomes=["purchased"], revenue="", pre="")
        r = analyze.analyze(self.raw, m, "synthetic.csv")
        self.assertIsNone(r["profit"])

    def test_pure_noise_is_not_called_clear(self):
        rnd = random.Random(1)
        out = io.StringIO(); w = csv.writer(out); w.writerow(["arm", "bought", "region"])
        for _ in range(3000):
            w.writerow([rnd.choice(["control", "treat"]), int(rnd.random() < 0.1), rnd.choice("ab")])
        r = analyze.analyze(out.getvalue().encode(), {"group": "arm", "control": "control", "outcomes": ["bought"], "features": ["region"]}, "n.csv")
        self.assertFalse(r["ok"])

    def test_sample_ratio_mismatch_is_flagged(self):
        rnd = random.Random(2)
        out = io.StringIO(); w = csv.writer(out); w.writerow(["arm", "bought", "region"])
        for _ in range(3000):
            w.writerow([rnd.choices(["control", "treat"], [0.7, 0.3])[0], int(rnd.random() < 0.1), rnd.choice("ab")])
        r = analyze.analyze(out.getvalue().encode(), {"group": "arm", "control": "control", "outcomes": ["bought"], "features": ["region"]}, "n.csv")
        self.assertTrue(r["srm"]["flag"])

    def test_validation_errors_are_plain_sentences(self):
        bad = [(b"", "empty"), (b"a,b\n1,2\n", "at least 3"), (b"a,b,c\n" + b"x,1,2\n" * 10, "only 10")]
        for raw, word in bad:
            with self.assertRaises(analyze.DataError) as cm:
                analyze.describe(raw)
            self.assertIn(word, str(cm.exception).lower() if word == "empty" else str(cm.exception))
        for change, word in [({"control": "nope"}, "does not appear"), ({"group": "purchased"}, "group"), ({"outcomes": []}, "outcome"),
                             ({"margin_pct": "abc"}, "number"), ({"margin_pct": 500}, "between"), ({"outcomes": ["campaign_arm"]}, "outcome"),
                             ({"revenue": "purchased"}, "revenue"), ({"pre": "region"}, "pre-period")]:
            with self.assertRaises(analyze.DataError, msg=str(change)) as cm:
                analyze.analyze(self.raw, dict(MAPPING, **change), "s.csv")
            self.assertIn(word, str(cm.exception).lower(), str(change))

    def test_tiny_group_is_rejected(self):
        raw = b"arm,bought,x\n" + b"control,1,a\n" * 3 + b"treat,0,b\n" * 60 + b"treat,1,b\n" * 60
        with self.assertRaises(analyze.DataError) as cm:
            analyze.analyze(raw, {"group": "arm", "control": "control", "outcomes": ["bought"]}, "t.csv")
        self.assertIn("at least", str(cm.exception))

    def test_handles_semicolons_bom_blanks_and_currency(self):
        rnd = random.Random(3)
        lines = ["﻿arm;bought;spend;region"]
        for i in range(600):
            arm = rnd.choice(["A", "B"])
            lines.append(f"{arm};{rnd.choice(['yes', 'no'])};{'' if i % 50 == 0 else '$' + str(round(rnd.random() * 100, 2))};{rnd.choice(['n', 's'])}")
        r = analyze.analyze("\r\n".join(lines).encode("utf-8"), {"group": "arm", "control": "A", "outcomes": ["bought", "spend"], "revenue": "spend", "features": ["region"],
                                                              "margin_pct": 30, "cost_per_contact": 0.1, "list_size": 1000}, "x.csv")
        self.assertEqual(r["dataset"]["rows_used"], 600)
        self.assertGreater(r["outcomes"][1]["dropped"], 0)

    def test_hillstrom_matches_the_main_analysis(self):
        f = ROOT / "data" / "raw" / "hillstrom.csv"
        if not f.exists():
            self.skipTest("Hillstrom file not present")
        raw = f.read_bytes()
        d = analyze.describe(raw, "hillstrom.csv")["suggest"]
        t0 = time.time()
        r = analyze.analyze(raw, dict(d, margin_pct=40, cost_per_contact=0.10, list_size=100000, fixed_cost=0), "hillstrom.csv")
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(r["dataset"]["rows_used"], 64000)
        visit = r["outcomes"][0]
        by = {e["arm"]: e for e in visit["effects"]}
        self.assertAlmostEqual(by["Mens E-Mail"]["diff"], 0.0766, delta=0.002)        # known Hillstrom visit rates: 10.62%, 18.28%, 15.14%
        self.assertAlmostEqual(by["Womens E-Mail"]["diff"], 0.0452, delta=0.002)
        self.assertFalse(r["srm"]["flag"])


class DataServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "d.db")
        cls.srv = server.make_server("127.0.0.1", 0, cls.db, mailer=FakeMailer())
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.raw = synthetic(2500)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.tmp.cleanup()

    def user(self, email):
        c = Client(self.port)
        self.assertEqual(c.register(email)[0], 303)
        return c, c.csrf()

    def upload(self, c, tok, raw=None, name="synthetic.csv"):
        return c.request("POST", "/api/dataset", raw or self.raw, {"Content-Type": "text/csv", "X-CSRF-Token": tok, "X-Filename": name})

    def test_upload_analyze_report_history(self):
        c, tok = self.user("data-flow@example.com")
        s, _, body = self.upload(c, tok); self.assertEqual(s, 200)
        info = json.loads(body)
        self.assertEqual(info["rows"], 2500); self.assertEqual(info["suggest"]["control"], "control")
        s, res = c.api("/api/analyze", dict(MAPPING, dataset_id=info["dataset_id"], label=""), tok)
        self.assertEqual(s, 200)
        self.assertIn("Results for purchased", res["html"]); self.assertIn("Profit and break-even", res["html"])
        self.assertNotRegex(res["html"], r"<p[ >]")
        s, h, rep = c.request("GET", f"/report/{res['id']}")
        self.assertEqual(s, 200)
        for text in ["Experiment Analysis Report", "EIS-D-", "Dataset and setup", "Data quality checks", "Authorisation", "synthetic.csv"]:
            self.assertIn(text, rep)
        self.assertEqual(re.findall(r"<script[^>]*>", rep), ['<script src="/static/print.js">'])     # only the print button's script, never inline code
        s, h, dl = c.request("GET", f"/report/{res['id']}/download")
        self.assertEqual(s, 200); self.assertIn("attachment", h.get("Content-Disposition", ""))
        self.assertNotIn("<script", dl)                                    # the downloaded file is fully self-contained and inert
        s, _, hist = c.request("GET", "/history"); self.assertIn("Your data", hist)
        s, _, run = c.request("GET", f"/history/{res['id']}"); self.assertEqual(s, 200)

    def test_data_page_and_script_are_served(self):
        c, _ = self.user("data-page@example.com")
        s, h, page = c.request("GET", "/data"); self.assertEqual(s, 200)
        self.assertIn("/static/data.js", page); self.assertNotIn("<style", page)
        s, h, _ = c.request("GET", "/static/data.js"); self.assertEqual(s, 200); self.assertIn("javascript", h["Content-Type"])

    def test_requires_login_and_csrf(self):
        anon = Client(self.port)
        self.assertEqual(anon.request("GET", "/data")[0], 303)
        self.assertEqual(anon.request("POST", "/api/dataset", self.raw, {"Content-Type": "text/csv"})[0], 401)
        c, tok = self.user("data-csrf@example.com")
        self.assertEqual(c.request("POST", "/api/dataset", self.raw, {"Content-Type": "text/csv"})[0], 403)
        self.assertEqual(c.request("POST", "/api/dataset", self.raw, {"Content-Type": "text/csv", "X-CSRF-Token": "x" * 40})[0], 403)

    def test_bad_files_get_a_plain_error(self):
        c, tok = self.user("data-bad@example.com")
        s, _, body = self.upload(c, tok, b"just one column\n1\n2\n")
        self.assertEqual(s, 400); self.assertIn("error", json.loads(body))
        s, _, body = self.upload(c, tok, b"\x00\x01\x02\xff" * 500)
        self.assertEqual(s, 400)

    def test_oversized_upload_is_refused(self):
        c, tok = self.user("data-big@example.com")
        big = b"a,b,c\n" + b"x,1,2\n" * (analyze.MAX_BYTES // 6 + 10)
        try:
            s, _, _ = self.upload(c, tok, big)
        except (ConnectionError, OSError):
            return                                                         # the server closed the connection early: also a refusal
        self.assertIn(s, (400, 413))

    def test_uploads_belong_to_one_user(self):
        a, ta = self.user("data-a@example.com"); b, tb = self.user("data-b@example.com")
        info = json.loads(self.upload(a, ta)[2])
        s, res = b.api("/api/analyze", dict(MAPPING, dataset_id=info["dataset_id"]), tb)
        self.assertEqual(s, 400); self.assertIn("no longer stored", res["error"])
        s, res = b.api("/api/analyze", dict(MAPPING, dataset_id="../../etc/passwd"), tb)
        self.assertEqual(s, 400)
        ok, res = a.api("/api/analyze", dict(MAPPING, dataset_id=info["dataset_id"]), ta)
        self.assertEqual(ok, 200)
        self.assertEqual(b.request("GET", f"/report/{res['id']}")[0], 404)  # nor can the other user read the report

    def test_analyze_validation_goes_back_as_a_message(self):
        c, tok = self.user("data-val@example.com")
        info = json.loads(self.upload(c, tok)[2])
        s, res = c.api("/api/analyze", dict(MAPPING, dataset_id=info["dataset_id"], control="nope"), tok)
        self.assertEqual(s, 400); self.assertIn("does not appear", res["error"])
        s, res = c.api("/api/analyze", {"dataset_id": info["dataset_id"], "outcomes": "x"}, tok)
        self.assertEqual(s, 400)

    def test_hostile_cell_text_is_escaped_in_results_and_report(self):
        rnd = random.Random(5)
        out = io.StringIO(); w = csv.writer(out); w.writerow(["arm", "bought", "seg"])
        for _ in range(800):
            w.writerow([rnd.choice(["control", "<img src=x onerror=alert(1)>"]), int(rnd.random() < 0.2), rnd.choice(["<script>alert(2)</script>", "ok"])])
        c, tok = self.user("data-xss@example.com")
        info = json.loads(self.upload(c, tok, out.getvalue().encode(), name="<b>x</b>.csv")[2])
        self.assertNotIn("<", info["name"])
        s, res = c.api("/api/analyze", {"dataset_id": info["dataset_id"], "group": "arm", "control": "control", "outcomes": ["bought"], "features": ["seg"]}, tok)
        self.assertEqual(s, 200)
        rep = c.request("GET", f"/report/{res['id']}")[2]
        for page in (res["html"], rep):
            self.assertNotIn("<script>alert", page); self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img", res["html"])


class LandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = server.make_server("127.0.0.1", 0, str(Path(cls.tmp.name) / "l.db"), mailer=FakeMailer())
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.tmp.cleanup()

    def test_visitors_see_the_landing_page_with_login_and_signup(self):
        s, h, page = Client(self.port).request("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn('href="/login"', page); self.assertIn('href="/register"', page)
        self.assertIn("/static/landing.css", page); self.assertNotIn("<script", page); self.assertNotIn("style=", page)
        self.assertIn("form-action 'none'", h["Content-Security-Policy"])
        self.assertEqual(Client(self.port).request("GET", "/static/landing.css")[0], 200)

    def test_signed_in_users_go_straight_to_the_app(self):
        c = Client(self.port); c.register("landing-user@example.com")
        s, h, _ = c.request("GET", "/")
        self.assertEqual((s, h["Location"]), (303, "/planner"))


class MigrationTests(unittest.TestCase):
    def test_old_database_gains_the_data_kind_and_keeps_its_runs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.db"
            con = sqlite3.connect(p)
            con.executescript("""
CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE runs(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('plan','score')), title TEXT NOT NULL, created_at TEXT NOT NULL, inputs TEXT NOT NULL, result TEXT NOT NULL);
INSERT INTO users VALUES(1,'a@example.com','A','x','2026-01-01');
INSERT INTO runs VALUES(7,1,'plan','Old run','2026-01-01','{}','{}');""")
            con.commit(); con.close()
            s = store.Store(str(p))
            con = sqlite3.connect(p)
            self.assertEqual(con.execute("SELECT id,title FROM runs").fetchall(), [(7, "Old run")])
            con.execute("INSERT INTO runs(user_id,kind,title,created_at,inputs,result) VALUES(1,'data','New','2026-02-01','{}','{}')")
            con.commit(); con.close()
            store.Store(str(p))                                             # a second start must not fail or rebuild again


if __name__ == "__main__":
    unittest.main(verbosity=2)
