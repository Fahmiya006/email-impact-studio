"""Decision engine for the web app. Pure standard library: no numpy, no scikit-learn.

plan()  - campaign planner: compares email policies for a whole list, with bootstrap intervals.
score() - single-customer scorer: predicted best email and expected profit gain, with bootstrap intervals.
"""
import json
import math
from bisect import bisect_right
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "model" / "model.json"
ARM_NAMES = ["No email", "Men's email", "Women's email"]

POLICY_NAMES = {
    "none": "Send nothing",
    "mens_all": "Men's email to everyone",
    "womens_all": "Women's email to everyone",
    "both_only": "Men's email only to customers who bought both lines",
}


def load_model(path=MODEL_PATH):
    with open(path) as f:
        return json.load(f)


def pct(sorted_vals, q):
    """Linear-interpolated percentile of an already sorted list, q in [0, 100]."""
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def interval(vals, level=95):
    s = sorted(vals)
    tail = (100 - level) / 2
    return [pct(s, tail), pct(s, 100 - tail)]


def money(x, d=2):
    a = f"{abs(x):,.{d}f}"
    return ("-" if x < 0 and float(a.replace(",", "")) != 0 else "") + "$" + a


# ----------------------------------------------------------------------------------- validation
def _num(d, key, lo, hi, label):
    try:
        v = float(d[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if math.isnan(v) or math.isinf(v) or not (lo <= v <= hi):
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
    return v


def _flag(d, key):
    v = d.get(key, False)
    if isinstance(v, str):
        return v.lower() in ("1", "true", "on", "yes")
    return bool(v)


# ----------------------------------------------------------------------------------- campaign planner
def plan(inputs, model):
    n = _num(inputs, "list_size", 100, 100_000_000, "List size")
    margin = _num(inputs, "margin_pct", 1, 100, "Gross margin") / 100
    cost = _num(inputs, "cost_per_contact", 0, 100, "Cost per contact")
    fixed = _num(inputs, "fixed_cost", 0, 1_000_000_000, "Fixed campaign cost") if inputs.get("fixed_cost") not in (None, "") else 0.0
    share = _num(inputs, "share_both_pct", 0, 100, "Share who bought both lines") / 100

    P = model["planner"]
    point, draws = P["point"], P["draws"]

    def revenue(M, arm):                         # expected 2-week spend per customer under one arm for everyone
        return share * M[1][arm] + (1 - share) * M[0][arm]

    def per_customer(M):
        r0, r1, r2 = revenue(M, 0), revenue(M, 1), revenue(M, 2)
        both_only_rev = share * M[1][1] + (1 - share) * M[0][0]
        send_fixed = fixed / n
        return {
            "none": (margin * r0, 0.0),
            "mens_all": (margin * r1 - cost - send_fixed, 1.0),
            "womens_all": (margin * r2 - cost - send_fixed, 1.0),
            "both_only": (margin * both_only_rev - cost * share - (send_fixed if share > 0 else 0), share),
        }

    base = per_customer(point)
    boot = [per_customer(M) for M in draws]
    keys = list(POLICY_NAMES)

    pol = []
    for k in keys:
        p_mean = base[k][0]
        p_draws = [b[k][0] for b in boot]
        vs_none = [b[k][0] - b["none"][0] for b in boot]
        vs_mens = [b[k][0] - b["mens_all"][0] for b in boot]
        pol.append({
            "key": k, "name": POLICY_NAMES[k], "share_contacted": base[k][1],
            "profit_per_customer": p_mean, "profit_total": p_mean * n,
            "ci_total": [x * n for x in interval(p_draws)],
            "vs_none": {"total": (p_mean - base["none"][0]) * n, "ci_total": [x * n for x in interval(vs_none)]},
            "vs_mens_all": {"total": (p_mean - base["mens_all"][0]) * n, "ci_total": [x * n for x in interval(vs_mens)]},
        })

    best = max(pol, key=lambda r: r["profit_total"])
    # Policies that cannot be told apart from the best one
    ties = []
    for r in pol:
        if r["key"] == best["key"]:
            continue
        d = [(b[best["key"]][0] - b[r["key"]][0]) * n for b in boot]
        lo, hi = interval(d)
        if lo <= 0 <= hi:
            ties.append(r["name"])

    # break-even cost per contact for the men's email to everyone vs sending nothing
    r0, r1 = revenue(point, 0), revenue(point, 1)
    be = margin * (r1 - r0) - fixed / n
    be_ci = interval([margin * (revenue(M, 1) - revenue(M, 0)) - fixed / n for M in draws])

    # profit-per-customer vs cost curves for the chart
    costs = [round(0.05 * i, 2) for i in range(0, 31)]
    curves = {k: [] for k in keys}
    for c in costs:
        for k in keys:
            if k == "none":
                v = margin * revenue(point, 0)
            elif k == "mens_all":
                v = margin * revenue(point, 1) - c - fixed / n
            elif k == "womens_all":
                v = margin * revenue(point, 2) - c - fixed / n
            else:
                v = margin * (share * point[1][1] + (1 - share) * point[0][0]) - c * share - (fixed / n if share > 0 else 0)
            curves[k].append(v)

    gain = best["vs_none"]
    if best["key"] == "none":
        headline = "Do not send this campaign"
        text = (f"At {money(cost)} per contact and a {margin:.0%} margin, none of the email policies is expected to earn more than "
                f"sending nothing. Emailing everyone breaks even at {money(be)} per contact (95% interval {money(be_ci[0])} to {money(be_ci[1])}).")
    else:
        headline = f"Run: {best['name']}"
        clear = gain["ci_total"][0] > 0
        text = (f"Expected profit {money(best['profit_total'], 0)} for {int(n):,} customers, "
                f"{money(gain['total'], 0)} more than sending nothing (95% interval {money(gain['ci_total'][0], 0)} to {money(gain['ci_total'][1], 0)}). "
                + ("The gain over sending nothing is statistically clear." if clear else
                   "The interval includes zero, so the gain over sending nothing is not statistically clear."))
        if ties:
            text += " It is not clearly better than: " + "; ".join(ties) + "."
    notes = [
        ["Based on one randomized test", "64,000 retail customers; spend is revenue in the two weeks after the email, so long-run value is not measured"],
        ["Margin, costs and bought-both share are your inputs", "Results shift if your customers differ from the test list"],
    ]
    if be < cost:
        notes.append(["Your cost is above break-even", f"{money(cost)} vs {money(be)}: mass emailing loses money, and narrow targeting needs a confirming test"])
    return {"inputs": {"list_size": n, "margin": margin, "cost_per_contact": cost, "fixed_cost": fixed, "share_both": share},
            "policies": pol, "best": best["key"], "headline": headline, "text": text, "ties": ties,
            "breakeven": {"value": be, "ci": be_ci}, "curves": {"costs": costs, "series": curves, "current_cost": cost},
            "notes": notes}


# ----------------------------------------------------------------------------------- single-customer scorer
def _features(c, S):
    hist = c["history"]
    band = bisect_right(S["history_cuts"], hist)               # 0..6
    x = {"recency": c["recency"], "history": hist, "log_history": math.log1p(hist), "mens": c["mens"], "womens": c["womens"],
         "newbie": c["newbie"], "zip_Urban": 1.0 * (c["zip"] == "Urban"), "zip_Surburban": 1.0 * (c["zip"] == "Suburban"),
         "ch_Phone": 1.0 * (c["channel"] == "Phone"), "ch_Multichannel": 1.0 * (c["channel"] == "Multichannel")}
    for r in range(2, 13):
        x[f"rec_{r}"] = 1.0 * (c["recency"] == r)
    for k in range(2, 8):
        x[f"hist_{k}"] = 1.0 * (band == k - 1)
    z = [(float(x[f]) - m) / s for f, m, s in zip(S["features"], S["mean"], S["std"])]
    return z


def _p_visit(coef, z):
    t = coef[0] + sum(w * v for w, v in zip(coef[1:], z))
    return 1 / (1 + math.exp(-max(min(t, 50), -50)))


def parse_customer(inputs):
    rec = int(_num(inputs, "recency", 1, 12, "Months since last purchase"))
    hist = _num(inputs, "history", 0, 1_000_000, "Past-year spend")
    mens, womens = _flag(inputs, "mens"), _flag(inputs, "womens")
    if not (mens or womens):
        raise ValueError("Select at least one product line the customer bought. The model was built on customers with a purchase in the last year.")
    zipc = inputs.get("zip", "")
    if zipc not in ("Urban", "Suburban", "Rural"):
        raise ValueError("Choose an area type.")
    ch = inputs.get("channel", "")
    if ch not in ("Web", "Phone", "Multichannel"):
        raise ValueError("Choose a purchase channel.")
    return {"recency": rec, "history": hist, "mens": 1.0 * mens, "womens": 1.0 * womens, "newbie": 1.0 * _flag(inputs, "newbie"),
            "zip": zipc, "channel": ch}


def score(inputs, model):
    c = parse_customer(inputs)
    margin = _num(inputs, "margin_pct", 1, 100, "Gross margin") / 100
    cost = _num(inputs, "cost_per_contact", 0, 100, "Cost per contact")
    S = model["scorer"]
    notes = []
    lo_h, hi_h = S["ranges"]["history"]
    used = dict(c)
    if c["history"] < lo_h or c["history"] > hi_h:
        used["history"] = min(max(c["history"], lo_h), hi_h)
        notes.append(["Spend is outside the model's range", f"The model saw ${lo_h:,.0f} to ${hi_h:,.0f}, so the spend was treated as {money(used['history'], 0)}"])
    z = _features(used, S)

    def profits(coefs, cpv, m):
        out = []
        for a in range(3):
            rev = _p_visit(coefs[a], z) * cpv[a] * m
            out.append((margin * rev - cost * (1 if a else 0), rev, _p_visit(coefs[a], z)))
        return out

    base = profits(S["coef"], S["conv_per_visit"], S["spend_per_converter"])
    boots = [profits(b["coef"], b["cpv"], b["m"]) for b in S["boot"]]

    arms = []
    for a in range(3):
        gains = [bt[a][0] - bt[0][0] for bt in boots]
        arms.append({"arm": ARM_NAMES[a], "p_visit": base[a][2], "expected_revenue": base[a][1], "expected_profit": base[a][0],
                     "gain_vs_none": base[a][0] - base[0][0], "gain_ci": interval(gains) if a else [0.0, 0.0]})
    best = 1 if base[1][0] >= base[2][0] else 2
    bg = arms[best]
    diff_draws = [bt[1][0] - bt[2][0] for bt in boots]
    diff = {"value": base[1][0] - base[2][0], "ci": interval(diff_draws)}
    if bg["gain_vs_none"] <= 0:
        verdict, text = "Do not email this customer", (
            f"Expected profit from either email ({money(bg['expected_profit'], 3)}) is below doing nothing at {money(cost)} per contact.")
    elif bg["gain_ci"][0] > 0:
        verdict, text = f"Send the {ARM_NAMES[best].lower()}", (
            f"Expected gain of {money(bg['gain_vs_none'], 3)} vs no email (95% interval {money(bg['gain_ci'][0], 3)} to {money(bg['gain_ci'][1], 3)}).")
    else:
        verdict, text = f"Probably send the {ARM_NAMES[best].lower()}", (
            f"Expected gain of {money(bg['gain_vs_none'], 3)} vs no email, but the 95% interval ({money(bg['gain_ci'][0], 3)} to {money(bg['gain_ci'][1], 3)}) includes zero.")
    if diff["ci"][0] <= 0 <= diff["ci"][1]:
        text += " The men's and women's emails are not clearly different for this customer."
    else:
        text += f" The {ARM_NAMES[1 if diff['value'] > 0 else 2].lower()} is clearly better than the other for this customer."

    # observed behaviour of the matching customer group in the randomized test
    gname = "Bought both" if c["mens"] and c["womens"] else ("Bought men's only" if c["mens"] else "Bought women's only")
    seg = next((s for s in model["segments"] if s["segment"] == gname), None)
    notes.append(["Individual scores are noisy", "Only about 580 of 64,000 test customers converted, so treat the verdict as a starting point, not a certainty"])
    return {"customer": {**c, "history_used": used["history"]}, "margin": margin, "cost": cost, "arms": arms, "best_arm": ARM_NAMES[best],
            "verdict": verdict, "text": text, "mens_vs_womens": diff, "segment": seg, "segment_name": gname, "notes": notes}
