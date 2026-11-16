"""Part 5 - Export the parameters the web app needs (app/model/model.json).

The app is pure standard-library Python, so it cannot import scikit-learn. This script distils the analysis into
plain numbers:
  * planner: mean spend per (customer group x email arm) with 500 bootstrap draws, so profit intervals can be
    computed for any list size / margin / cost / customer mix the user types in;
  * scorer: per-arm logistic regressions for P(visit | customer, arm) (coefficients + 200 bootstrap refits),
    conversion-per-visit by arm, and spend per converter.
"""
import json
import warnings
warnings.filterwarnings("ignore")
import numpy as np
from sklearn.linear_model import LogisticRegression

from common import *

df = load()
Xf = features_flex(df).drop(columns=["zip_Rural", "ch_Web"])
cols = list(Xf.columns)
mu, sd = Xf.mean().values, Xf.std().values
Xs = (Xf.values - mu) / sd
arm, visit, conv, spend = (df[c].values for c in ["arm", "visit", "conversion", "spend"])
both = ((df["mens"] == 1) & (df["womens"] == 1)).values
grp = both.astype(int)                       # 0 = bought one line only, 1 = bought both
N = len(df)
rng = np.random.default_rng(SEED)
B1, B2 = 500, 200

# ---------------------------------------------------------------- planner: group x arm mean spend
def group_arm_means(idx):
    key = grp[idx] * 3 + arm[idx]
    s = np.bincount(key, weights=spend[idx], minlength=6)
    n = np.bincount(key, minlength=6)
    return (s / np.maximum(n, 1)).reshape(2, 3)

point = group_arm_means(np.arange(N))
draws = np.array([group_arm_means(rng.integers(0, N, N)) for _ in range(B1)])

# ---------------------------------------------------------------- scorer
def fit_arm_models(idx):
    coefs, cpv = [], []
    m = spend[idx][conv[idx] == 1].mean()
    for a in range(3):
        ia = idx[arm[idx] == a]
        lr = LogisticRegression(C=0.1, max_iter=500).fit(Xs[ia], visit[ia])
        coefs.append(np.concatenate([[lr.intercept_[0]], lr.coef_[0]]))
        cpv.append(conv[ia].sum() / visit[ia].sum())
    return np.array(coefs), np.array(cpv), m

c0, cpv0, m0 = fit_arm_models(np.arange(N))
boot = []
for _ in range(B2):
    idx = rng.integers(0, N, N)
    c, cp, m = fit_arm_models(idx)
    boot.append({"coef": c, "cpv": cp, "m": m})

# history bands are categories with these lower cut-offs; verify the data really follows them
cuts = [100.0, 200.0, 350.0, 500.0, 750.0, 1000.0]
band_of = np.searchsorted(cuts, df["history"].values, side="right")
assert (band_of == df["history_segment"].map(HIST_ORDER).values).all(), "history cut-offs do not match data"

seg_rows = load_json("03_uplift_policy.json")["segments"]
model = {
    "meta": {"source": "Hillstrom MineThatData E-Mail Analytics Challenge (2008), 64,000 customers",
             "n": N, "bootstrap_planner": B1, "bootstrap_scorer": B2,
             "arms": ARMS, "mean_spend_by_arm": [float(spend[arm == a].mean()) for a in range(3)]},
    "planner": {"group_names": ["Bought one line only", "Bought both lines"],
                "share_both_in_test": float(both.mean()),
                "point": point.round(5).tolist(), "draws": draws.round(4).tolist()},
    "scorer": {"features": cols, "mean": mu.round(8).tolist(), "std": sd.round(8).tolist(),
               "coef": c0.round(6).tolist(), "conv_per_visit": cpv0.round(6).tolist(), "spend_per_converter": float(m0),
               "boot": [{"coef": b["coef"].round(5).tolist(), "cpv": b["cpv"].round(5).tolist(), "m": round(float(b["m"]), 3)} for b in boot],
               "history_cuts": cuts,
               "ranges": {"recency": [1, 12], "history": [float(df["history"].min()), float(df["history"].max())]},
               "recency_values": sorted(int(x) for x in df["recency"].unique())},
    "segments": seg_rows,
    "reference": {"gross_margin": GROSS_MARGIN, "cost_per_email": COST_PER_EMAIL},
}
out = ROOT / "app" / "model"
out.mkdir(parents=True, exist_ok=True)
with open(out / "model.json", "w") as f:
    json.dump(model, f, separators=(",", ":"))
print("wrote", out / "model.json", f"{(out / 'model.json').stat().st_size / 1024:.0f} KB")
print("history cuts verified against all rows:", cuts)
print("group x arm mean spend:\n", point.round(3))
