"""Part 4 - Assemble the self-contained interactive dashboard from the saved analysis outputs."""
import json
import numpy as np

from common import *

df = load()
ab, obs, up = load_json("01_ab_test.json"), load_json("02_observational.json"), load_json("03_uplift_policy.json")

# per-arm means with 95% CIs (descriptive bars)
arm_stats = {}
for m in ["visit", "conversion", "spend"]:
    arm_stats[m] = []
    for a in ARMS:
        v = df.loc[df.segment == a, m].values.astype(float)
        se = v.std(ddof=1) / np.sqrt(len(v))
        arm_stats[m].append({"arm": a, "mean": v.mean(), "lo": v.mean() - 1.96 * se, "hi": v.mean() + 1.96 * se, "n": len(v)})

# revenue per customer under the "bought both only" policy (same off-policy estimator as Part 3)
arm, spend = df["arm"].values, df["spend"].values
both = ((df["mens"] == 1) & (df["womens"] == 1)).values
pi = np.where(both, 1, 0); match = arm == pi
R_both = float(spend[match].sum() / match.sum())

data = {
    "assumptions": up["assumptions"], "arm_stats": arm_stats, "effects": ab["effects"],
    "power": ab["power"], "cuped": ab["cuped"], "srm_p": ab["srm"]["p"], "max_smd": ab["balance_smd_max_abs"],
    "obs": {"sim": obs["simulation"], "bench": obs["benchmark_randomized"], "reps": obs["config"]["repetitions"]},
    "policy": up["policy_table"], "high_cost": up["high_cost_scenario"], "curve": up["curve"],
    "sens": up["sensitivity"], "segs": up["segments"], "arm_rev": up["arm_revenue"],
    "R_both": R_both, "share_both": float(both.mean()), "bc": up["business_case"],
    "deciles": up["deciles"], "both_int": up["both_interaction"],
}

import base64

STATIC = ROOT / "app" / "static"


def b64(path):
    return base64.b64encode(path.read_bytes()).decode("ascii")


# Calibri (or its metric-identical stand-in Carlito) is embedded so the page looks the same on any device
fonts = ""
for weight, file, local in ((400, "Carlito-Regular.subset.ttf", "Calibri"), (700, "Carlito-Bold.subset.ttf", "Calibri Bold")):
    fonts += ('@font-face{font-family:"Calibri";font-weight:' + str(weight) + ';src:local("' + local + '"),url(data:font/ttf;base64,'
              + b64(STATIC / "fonts" / file) + ') format("truetype")}\n')

tpl = (ROOT / "src" / "dashboard_template.html").read_text()
html = (tpl.replace("__DATA__", json.dumps(data, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else list(o)))
        .replace("__FONT_FACE__", fonts)
        .replace("__FAVICON__", "data:image/png;base64," + b64(STATIC / "favicon-64.png"))
        .replace("__BG_LIGHT__", "data:image/svg+xml;base64," + b64(STATIC / "bg-light.svg"))
        .replace("__BG_DARK__", "data:image/svg+xml;base64," + b64(STATIC / "bg-dark.svg")))
out = ROOT / "dashboard" / "index.html"
out.write_text(html)
print("wrote", out, f"{len(html)/1024:.0f} KB")
