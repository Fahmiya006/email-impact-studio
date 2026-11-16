"""Shared helpers: data loading, feature prep, assumptions, small stats utilities."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "raw" / "hillstrom.csv"
OUT = ROOT / "outputs"
FIG = OUT / "figures"
SEED = 42

# ---- Business assumptions (Hillstrom has no cost data; these are explicit and
# stress-tested in the sensitivity analysis) ----
GROSS_MARGIN = 0.40      # share of spend that is profit
COST_PER_EMAIL = 0.10    # USD per email sent

ARMS = ["No E-Mail", "Mens E-Mail", "Womens E-Mail"]
HIST_ORDER = {
    "1) $0 - $100": 0, "2) $100 - $200": 1, "3) $200 - $350": 2, "4) $350 - $500": 3,
    "5) $500 - $750": 4, "6) $750 - $1,000": 5, "7) $1,000 +": 6,
}


def load():
    df = pd.read_csv(DATA)
    df["treated"] = (df["segment"] != "No E-Mail").astype(int)
    df["arm"] = df["segment"].map({a: i for i, a in enumerate(ARMS)})
    return df


def features(df):
    """Numeric design matrix of pre-treatment covariates only."""
    X = pd.DataFrame(index=df.index)
    X["recency"] = df["recency"]
    X["history"] = df["history"]
    X["log_history"] = np.log1p(df["history"])
    X["mens"] = df["mens"]
    X["womens"] = df["womens"]
    X["newbie"] = df["newbie"]
    for z in ["Urban", "Surburban", "Rural"]:
        X["zip_" + z] = (df["zip_code"] == z).astype(int)
    for c in ["Web", "Phone", "Multichannel"]:
        X["ch_" + c] = (df["channel"] == c).astype(int)
    return X.astype(float)


def features_flex(df):
    """Adds categorical dummies for recency and history band (flexible adjustment set)."""
    X = features(df)
    for r in sorted(df["recency"].unique())[1:]:
        X[f"rec_{r}"] = (df["recency"] == r).astype(float)
    for h in sorted(HIST_ORDER, key=HIST_ORDER.get)[1:]:
        X["hist_" + h[:1]] = (df["history_segment"] == h).astype(float)
    return X


def diff_means(y, t):
    """Difference in means with Welch standard error."""
    y1, y0 = y[t == 1], y[t == 0]
    d = y1.mean() - y0.mean()
    se = np.sqrt(y1.var(ddof=1) / len(y1) + y0.var(ddof=1) / len(y0))
    return d, se


def bootstrap_ci(fn, n, B=1000, seed=SEED, alpha=0.05):
    """Percentile bootstrap of a statistic computed from resampled row indices."""
    rng = np.random.default_rng(seed)
    vals = np.array([fn(rng.integers(0, n, n)) for _ in range(B)])
    return np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)]), vals.std()


def save_json(obj, name):
    OUT.mkdir(exist_ok=True, parents=True)

    def conv(o):
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    with open(OUT / name, "w") as f:
        json.dump(obj, f, indent=2, default=conv)


def load_json(name):
    with open(OUT / name) as f:
        return json.load(f)
