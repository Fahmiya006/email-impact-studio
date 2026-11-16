"""Part 1 - Analyse the randomized experiment: integrity checks, effects, power, CUPED."""
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import *

df = load()
X = features(df)
res = {}

# ---------------------------------------------------------------- integrity
counts = df["segment"].value_counts().reindex(ARMS)
chi2, p_srm = stats.chisquare(counts.values)
res["srm"] = {"counts": counts.to_dict(), "chi2": chi2, "p": p_srm}

def smd(x, t):
    a, b = x[t == 1], x[t == 0]
    return (a.mean() - b.mean()) / np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)

bal = {c: smd(X[c].values, df["treated"].values) for c in X.columns}
res["balance_smd_max_abs"] = max(abs(v) for v in bal.values())
res["balance_smd"] = bal

# ---------------------------------------------------------------- effects
def prop_test(y, t):
    n1, n0 = (t == 1).sum(), (t == 0).sum()
    p1, p0 = y[t == 1].mean(), y[t == 0].mean()
    pp = y.mean()
    se_pool = np.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n0))
    z = (p1 - p0) / se_pool
    se = np.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    d = p1 - p0
    return {"treat": p1, "control": p0, "diff": d, "ci": [d - 1.96 * se, d + 1.96 * se],
            "rel_lift": d / p0, "p": 2 * (1 - stats.norm.cdf(abs(z)))}

def mean_test(y, t):
    d, se = diff_means(y, t)
    tt = d / se
    return {"treat": y[t == 1].mean(), "control": y[t == 0].mean(), "diff": d,
            "ci": [d - 1.96 * se, d + 1.96 * se], "rel_lift": d / y[t == 0].mean(),
            "p": 2 * (1 - stats.norm.cdf(abs(tt)))}

comparisons = {
    "Any email vs No email": df[df.segment.isin(ARMS)].assign(t=df["treated"]),
    "Mens email vs No email": df[df.segment != "Womens E-Mail"].assign(t=lambda d: (d.segment == "Mens E-Mail").astype(int)),
    "Womens email vs No email": df[df.segment != "Mens E-Mail"].assign(t=lambda d: (d.segment == "Womens E-Mail").astype(int)),
    "Mens vs Womens email": df[df.segment != "No E-Mail"].assign(t=lambda d: (d.segment == "Mens E-Mail").astype(int)),
}
eff = {}
for name, d in comparisons.items():
    t = d["t"].values
    eff[name] = {
        "visit": prop_test(d["visit"].values.astype(float), t),
        "conversion": prop_test(d["conversion"].values.astype(float), t),
        "spend": mean_test(d["spend"].values, t),
    }
res["effects"] = eff

# Holm correction over the three spend tests against control + head-to-head
ps = {k: eff[k]["spend"]["p"] for k in eff}
order = sorted(ps, key=ps.get)
holm, running = {}, 0.0
for i, k in enumerate(order):
    running = max(running, min(1.0, (len(order) - i) * ps[k]))
    holm[k] = running
res["spend_p_holm"] = holm

# ---------------------------------------------------------------- power
def n_per_arm_two_prop(p0, rel_lift, alpha=0.05, power=0.80):
    p1 = p0 * (1 + rel_lift)
    za, zb = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(power)
    pbar = (p0 + p1) / 2
    num = (za * np.sqrt(2 * pbar * (1 - pbar)) + zb * np.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2
    return num / (p1 - p0) ** 2

def mde_rel(p0, n, alpha=0.05, power=0.80):
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if n_per_arm_two_prop(p0, mid, alpha, power) > n:
            lo = mid
        else:
            hi = mid
    return hi

p0 = df.loc[df.segment == "No E-Mail", "conversion"].mean()
n_arm = int(counts.min())
res["power"] = {
    "baseline_conversion": p0,
    "n_per_arm_actual": n_arm,
    "mde_relative_at_actual_n": mde_rel(p0, n_arm),
    "n_per_arm_needed": {f"{int(l * 100)}% relative lift": n_per_arm_two_prop(p0, l) for l in [0.10, 0.20, 0.50, 1.0]},
}

# ---------------------------------------------------------------- CUPED
# Pre-experiment covariate: past-12-month spend ("history"). Variance reduction for spend.
t = df["treated"].values
y = df["spend"].values
def cuped(y, x, t):
    theta = np.cov(x, y)[0, 1] / x.var(ddof=1)
    return y - theta * (x - x.mean()), theta

y_c, theta = cuped(y, df["history"].values, t)
d_raw, se_raw = diff_means(y, t)
d_c, se_c = diff_means(y_c, t)

# Richer adjustment: pooled regression on all pre-treatment covariates (CUPAC-style).
from sklearn.linear_model import Ridge
from sklearn.model_selection import cross_val_predict
pred = cross_val_predict(Ridge(alpha=1.0), X.values, y, cv=5)
theta2 = np.cov(pred, y)[0, 1] / pred.var(ddof=1)
y_c2 = y - theta2 * (pred - pred.mean())
d_c2, se_c2 = diff_means(y_c2, t)

res["cuped"] = {
    "corr_history_spend": float(np.corrcoef(df["history"], y)[0, 1]),
    "theta": theta,
    "raw": {"diff": d_raw, "se": se_raw},
    "cuped_history": {"diff": d_c, "se": se_c, "var_reduction_pct": 100 * (1 - (se_c / se_raw) ** 2)},
    "cuped_all_covariates": {"diff": d_c2, "se": se_c2, "var_reduction_pct": 100 * (1 - (se_c2 / se_raw) ** 2)},
}

# ---------------------------------------------------------------- figures
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "text.parse_math": False})
fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
colors = ["#8a8a8a", "#2f6db5", "#c4572f"]
for ax, (m, title, fmt) in zip(axes, [("visit", "Site visit rate", "{:.1%}"),
                                      ("conversion", "Conversion rate", "{:.2%}"),
                                      ("spend", "Avg spend per customer ($)", "${:.2f}")]):
    vals, errs = [], []
    for a in ARMS:
        v = df.loc[df.segment == a, m].values
        vals.append(v.mean()); errs.append(1.96 * v.std(ddof=1) / np.sqrt(len(v)))
    ax.bar(range(3), vals, yerr=errs, color=colors, capsize=3)
    ax.set_xticks(range(3)); ax.set_xticklabels(["None", "Mens", "Womens"])
    ax.set_title(title)
    for i, v in enumerate(vals):
        ax.text(i, v, fmt.format(v), ha="center", va="bottom", fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "01_ab_results.png", dpi=150); plt.close(fig)

save_json(res, "01_ab_test.json")

# ---------------------------------------------------------------- console summary
print(f"SRM p={p_srm:.3f} | max |SMD|={res['balance_smd_max_abs']:.3f}")
for k, v in eff.items():
    s, c = v["spend"], v["conversion"]
    print(f"{k:28s} conv diff {c['diff']*100:+.2f}pp (p={c['p']:.1e}) | spend diff ${s['diff']:+.3f} "
          f"CI[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}] p={s['p']:.1e} holm={holm[k]:.1e}")
print("Power:", res["power"])
print("CUPED:", res["cuped"])
