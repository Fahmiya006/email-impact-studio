"""Part 2 - What if we could NOT randomize?

Hillstrom is a clean randomized trial, so the randomized effect is a trustworthy benchmark
("ground truth"). We deliberately break the randomization to mimic a typical business rollout
("email the loyal customers, hold back the rest") by thinning the data so that the chance of
being in the emailed group depends on customer loyalty. Then we ask: which methods recover the
benchmark, and which ones are fooled?

Selection depends on observed covariates only, so correct adjustment is possible in principle
(this is a controlled stress test, not a claim about real-world unobserved confounding).
Note: the dataset has no time dimension, so difference-in-differences is not applicable here.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import NearestNeighbors
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import *

df = load()
X = features_flex(df).drop(columns=["zip_Rural", "ch_Web"])
Xs = ((X - X.mean()) / X.std()).values
OUTCOMES = ["visit", "conversion", "spend"]

# ------------------------------------------------------------ benchmark (randomized)
bench = {o: diff_means(df[o].values.astype(float), df["treated"].values)[0] for o in OUTCOMES}

# ------------------------------------------------------------ engineered biased rollout
loyal = (1.0 * (df["newbie"] == 0) + 0.9 * (df["recency"] <= 3) + 0.8 * (df["history"] >= 200)
         + 0.4 * df["mens"]).values
z = (loyal - loyal.mean()) / loyal.std()
e_target = 1 / (1 + np.exp(-(0.7 + 1.2 * z)))                     # P(emailed | customer)
keep_t_raw = e_target / (2 / 3)                                    # original P(treated)=2/3
keep_c_raw = (1 - e_target) / (1 / 3)
k = 1 / max(keep_t_raw.max(), keep_c_raw.max())
keep_prob = np.where(df["treated"] == 1, keep_t_raw * k, keep_c_raw * k)   # keeps x-marginal unchanged


def draw_biased(seed):
    rng = np.random.default_rng(seed)
    return np.flatnonzero(rng.random(len(df)) < keep_prob)


# ------------------------------------------------------------ estimators
def ols_fit(A, y):
    A1 = np.column_stack([np.ones(len(A)), A])
    beta, *_ = np.linalg.lstsq(A1, y, rcond=None)
    return beta


def estimate_all(idx):
    d = df.iloc[idx]
    t = d["treated"].values
    Z = Xs[idx]
    ps = LogisticRegression(C=1.0, max_iter=500).fit(Z, t).predict_proba(Z)[:, 1]
    ps = np.clip(ps, 0.02, 0.98)
    logit = np.log(ps / (1 - ps))

    # matching on the propensity score (1-NN with replacement, caliper 0.2 SD of logit)
    cal = 0.2 * logit.std()
    nn1 = NearestNeighbors(n_neighbors=1).fit(logit[t == 1].reshape(-1, 1))
    nn0 = NearestNeighbors(n_neighbors=1).fit(logit[t == 0].reshape(-1, 1))
    d0, i0 = nn0.kneighbors(logit[t == 1].reshape(-1, 1))   # treated -> control match
    d1, i1 = nn1.kneighbors(logit[t == 0].reshape(-1, 1))   # control -> treated match
    ok0, ok1 = d0[:, 0] <= cal, d1[:, 0] <= cal

    out = {}
    for o in OUTCOMES:
        y = d[o].values.astype(float)
        yt, yc = y[t == 1], y[t == 0]
        r = {}
        r["Naive difference"] = yt.mean() - yc.mean()
        # regression adjustment
        beta = ols_fit(np.column_stack([t, Z]), y)
        r["Regression adjustment"] = beta[1]
        # inverse-probability weighting (Hajek)
        w1, w0 = t / ps, (1 - t) / (1 - ps)
        r["IPW"] = (w1 * y).sum() / w1.sum() - (w0 * y).sum() / w0.sum()
        # propensity-score matching (ATE)
        eff = np.concatenate([(yt[ok0] - yc[i0[ok0, 0]]), (yt[i1[ok1, 0]] - yc[ok1])])
        r["PS matching"] = eff.mean()
        # doubly robust (AIPW)
        b1 = ols_fit(Z[t == 1], yt); b0 = ols_fit(Z[t == 0], yc)
        mu1 = np.column_stack([np.ones(len(Z)), Z]) @ b1
        mu0 = np.column_stack([np.ones(len(Z)), Z]) @ b0
        r["Doubly robust (AIPW)"] = np.mean(mu1 - mu0 + t * (y - mu1) / ps - (1 - t) * (y - mu0) / (1 - ps))
        out[o] = r
    return out, ps, t, Z


METHODS = ["Naive difference", "Regression adjustment", "IPW", "PS matching", "Doubly robust (AIPW)"]

# ------------------------------------------------------------ simulation study
R = 300
sims = {o: {m: [] for m in METHODS} for o in OUTCOMES}
sizes = []
for r in range(R):
    idx = draw_biased(1000 + r)
    sizes.append(len(idx))
    est, *_ = estimate_all(idx)
    for o in OUTCOMES:
        for m in METHODS:
            sims[o][m].append(est[o][m])

summary = {}
for o in OUTCOMES:
    summary[o] = {"benchmark": bench[o]}
    for m in METHODS:
        v = np.array(sims[o][m])
        summary[o][m] = {"mean": v.mean(), "bias": v.mean() - bench[o], "sd": v.std(),
                         "rmse": np.sqrt(((v - bench[o]) ** 2).mean()),
                         "bias_pct_of_benchmark": 100 * (v.mean() - bench[o]) / bench[o]}

# ------------------------------------------------------------ one illustrative draw + bootstrap CI
idx0 = draw_biased(7)
est0, ps0, t0, Z0 = estimate_all(idx0)
d0_ = df.iloc[idx0]
rng = np.random.default_rng(SEED)
boot = {o: {m: [] for m in METHODS} for o in ["visit", "spend"]}
for _ in range(200):
    b = rng.integers(0, len(idx0), len(idx0))
    e_b, *_ = estimate_all(idx0[b])
    for o in ["visit", "spend"]:
        for m in METHODS:
            boot[o][m].append(e_b[o][m])
single = {}
for o in OUTCOMES:
    single[o] = {}
    for m in METHODS:
        row = {"estimate": est0[o][m]}
        if o in boot:
            row["ci"] = list(np.percentile(boot[o][m], [2.5, 97.5]))
        single[o][m] = row

# covariate balance (SMD) before / after IPW in the illustrative draw
def smd_w(x, t, w=None):
    w = np.ones_like(x) if w is None else w
    m1 = np.average(x[t == 1], weights=w[t == 1]); m0 = np.average(x[t == 0], weights=w[t == 0])
    s = np.sqrt((x[t == 1].var(ddof=1) + x[t == 0].var(ddof=1)) / 2)
    return (m1 - m0) / s
w_ipw = np.where(t0 == 1, 1 / ps0, 1 / (1 - ps0))
bal = {c: {"before": smd_w(Z0[:, j], t0), "after": smd_w(Z0[:, j], t0, w_ipw)} for j, c in enumerate(X.columns)}

res = {"config": {"repetitions": R, "mean_sample_size": float(np.mean(sizes)),
                  "share_emailed_in_biased_sample": float((df.iloc[idx0]["treated"]).mean())},
       "benchmark_randomized": bench, "simulation": summary, "single_draw": single, "balance": bal}
save_json(res, "02_observational.json")

# ------------------------------------------------------------ figures
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "text.parse_math": False})
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
for ax, o, title in zip(axes, ["visit", "spend"], ["Visit-rate lift (pp)", "Spend lift ($ per customer)"]):
    sc = 100 if o == "visit" else 1
    data = [np.array(sims[o][m]) * sc for m in METHODS]
    bp = ax.boxplot(data, orientation="horizontal", widths=0.6, patch_artist=True, showfliers=False)
    for patch, m in zip(bp["boxes"], METHODS):
        patch.set_facecolor("#c4572f" if m == "Naive difference" else "#2f6db5"); patch.set_alpha(0.6)
    ax.axvline(bench[o] * sc, color="k", ls="--", lw=1)
    ax.set_yticks(range(1, len(METHODS) + 1)); ax.set_yticklabels(METHODS if o == "visit" else [""] * 5)
    ax.invert_yaxis(); ax.set_title(title)
fig.suptitle(f"Biased rollout: {R} simulated draws vs randomized benchmark (dashed)", fontsize=10)
fig.tight_layout(); fig.savefig(FIG / "02_bias_recovery.png", dpi=150); plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
axes[0].hist(ps0[t0 == 0], bins=40, alpha=0.6, label="Not emailed", color="#8a8a8a")
axes[0].hist(ps0[t0 == 1], bins=40, alpha=0.6, label="Emailed", color="#2f6db5")
axes[0].set_title("Propensity score overlap"); axes[0].set_xlabel("P(emailed | covariates)"); axes[0].legend()
names = list(bal); order = np.argsort([abs(bal[n]["before"]) for n in names])
axes[1].scatter([bal[names[i]]["before"] for i in order], range(len(order)), color="#c4572f", label="Before weighting")
axes[1].scatter([bal[names[i]]["after"] for i in order], range(len(order)), color="#2f6db5", label="After IPW")
axes[1].axvline(0, color="k", lw=0.8); axes[1].axvline(-0.1, color="k", ls=":", lw=0.6); axes[1].axvline(0.1, color="k", ls=":", lw=0.6)
axes[1].set_yticks(range(len(order))); axes[1].set_yticklabels([names[i] for i in order], fontsize=8)
axes[1].set_title("Covariate balance (standardized mean difference)"); axes[1].legend(fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "03_overlap_balance.png", dpi=150); plt.close(fig)

# ------------------------------------------------------------ console summary
print(f"benchmark: {bench}")
print(f"mean sample size {np.mean(sizes):.0f}; emailed share {res['config']['share_emailed_in_biased_sample']:.2f}")
for o in OUTCOMES:
    print(f"\n[{o}] benchmark {bench[o]:.4f}")
    for m in METHODS:
        s = summary[o][m]
        print(f"  {m:24s} mean {s['mean']:.4f} bias {s['bias']:+.4f} ({s['bias_pct_of_benchmark']:+.0f}%) sd {s['sd']:.4f} rmse {s['rmse']:.4f}")
