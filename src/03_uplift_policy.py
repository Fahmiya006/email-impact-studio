"""Part 3 - Uplift modelling and a profit-based targeting policy.

Because the experiment is randomized (each arm has probability 1/3), any targeting policy can be
evaluated off-policy with a self-normalised inverse-propensity (Hajek) estimator on out-of-fold
predictions: the value of policy pi is the average profit of customers whose randomly assigned arm
happened to equal pi(x). No model assumptions are needed for the evaluation itself.

Modelling note: only ~580 customers convert, so conversion-level uplift models are extremely noisy.
We use a two-stage model: P(visit | x, arm) carries the customer heterogeneity (18% base rate,
thousands of events), then constant conversion-per-visit and spend-per-conversion by arm.
"""
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import *

df = load()
X = features_flex(df).drop(columns=["zip_Rural", "ch_Web"])
Xs = ((X - X.mean()) / X.std()).values
arm, conv, spend, visit = (df[c].values for c in ["arm", "conversion", "spend", "visit"])
N = len(df)
SEND = np.array([0, 1, 1])          # arm 0 = no email costs nothing


# ------------------------------------------------------------------ out-of-fold predictions
def cross_fit(make_model, seed=1):
    """Expected spend per customer under each arm, predicted by a model that never saw the customer."""
    P = np.zeros((N, 3)); PV = np.zeros((N, 3))
    strat = arm * 2 + visit
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=seed).split(Xs, strat):
        m = spend[tr][conv[tr] == 1].mean()          # pooled $ per converter
        for a in range(3):
            tra = tr[arm[tr] == a]
            pv = make_model().fit(Xs[tra], visit[tra]).predict_proba(Xs[te])[:, 1]
            conv_per_visit = conv[tra].sum() / visit[tra].sum()
            PV[te, a] = pv
            P[te, a] = pv * conv_per_visit * m
    return P, PV


MODELS = {
    "Gradient boosting (primary)": lambda: HistGradientBoostingClassifier(
        max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=200, l2_regularization=5.0, random_state=0),
    "Logistic regression (check)": lambda: LogisticRegression(C=0.1, max_iter=500),
}
PRED = {k: cross_fit(f) for k, f in MODELS.items()}
PRIMARY = "Gradient boosting (primary)"
P, PV = PRED[PRIMARY]


# ------------------------------------------------------------------ off-policy evaluation
def policy_value(pi, margin=GROSS_MARGIN, cost=COST_PER_EMAIL, idx=None):
    """Self-normalised IPW profit per customer for policy pi (array of arm indices)."""
    a, s, p = (arm, spend, pi) if idx is None else (arm[idx], spend[idx], pi[idx])
    match = a == p
    prof = margin * s - cost * SEND[p]
    return prof[match].sum() / match.sum()


def best_arm(Pm, margin=GROSS_MARGIN, cost=COST_PER_EMAIL):
    return (margin * Pm - cost * SEND).argmax(1)


def paired_boot(policies, B=500, seed=SEED, **kw):
    rng = np.random.default_rng(seed)
    out = {k: [] for k in policies}
    for _ in range(B):
        idx = rng.integers(0, N, N)
        for k, pi in policies.items():
            out[k].append(policy_value(pi, idx=idx, **kw))
    return {k: np.array(v) for k, v in out.items()}


mens_flag, womens_flag = df["mens"].values, df["womens"].values
affinity = np.where((mens_flag == 1) & (womens_flag == 0), 1,
                    np.where((mens_flag == 0) & (womens_flag == 1), 2, 1))   # mens-only->mens, womens-only->womens, else mens
rng0 = np.random.default_rng(SEED)
policies = {
    "Send nothing": np.zeros(N, int),
    "Blanket women's email": np.full(N, 2),
    "Blanket men's email": np.full(N, 1),
    "Affinity rule (match past purchases)": affinity,
    "Random arm": rng0.integers(0, 3, N),
    "Men's email to 'bought both' only": np.where((mens_flag == 1) & (womens_flag == 1), 1, 0),
    "Uplift model, gradient boosting": best_arm(PRED[PRIMARY][0]),
    "Uplift model, logistic": best_arm(PRED["Logistic regression (check)"][0]),
}
boots = paired_boot(policies)
ref = "Blanket men's email"
table = {}
for k, pi in policies.items():
    v = policy_value(pi); b = boots[k]
    d = b - boots[ref]
    table[k] = {"profit_per_customer": v, "ci": list(np.percentile(b, [2.5, 97.5])),
                "vs_blanket_mens": float(np.mean(b) - np.mean(boots[ref])) if k != ref else 0.0,
                "vs_blanket_mens_ci": list(np.percentile(d, [2.5, 97.5])),
                "share_none": float((pi == 0).mean()), "share_mens": float((pi == 1).mean()),
                "share_womens": float((pi == 2).mean())}
    table[k]["vs_blanket_mens"] = float(policy_value(pi) - policy_value(policies[ref]))

# ------------------------------------------------------------------ costly-channel scenario
HC_COST = 0.50   # e.g. direct mail / a discount-funded offer
hc_pol = {"Send nothing": policies["Send nothing"], "Blanket men's email": policies[ref],
          "Men's email to 'bought both' only": policies["Men's email to 'bought both' only"],
          "Uplift model, gradient boosting": best_arm(P, GROSS_MARGIN, HC_COST)}
hc_boot = paired_boot(hc_pol, cost=HC_COST)
hc_table = {}
for k, pi in hc_pol.items():
    b = hc_boot[k]; d = b - hc_boot["Send nothing"]
    hc_table[k] = {"profit_per_customer": policy_value(pi, cost=HC_COST), "ci": list(np.percentile(b, [2.5, 97.5])),
                   "vs_nothing": float(policy_value(pi, cost=HC_COST) - policy_value(hc_pol["Send nothing"], cost=HC_COST)),
                   "vs_nothing_ci": list(np.percentile(d, [2.5, 97.5])), "share_emailed": float((pi > 0).mean())}

# ------------------------------------------------------------------ targeting curve (ranking-based)
rev_best_email = P[:, 1:].max(1)
best_email_arm = 1 + P[:, 1:].argmax(1)
uplift_rev = rev_best_email - P[:, 0]                          # predicted incremental revenue of best email
order = np.argsort(-uplift_rev)
rank = np.empty(N, int); rank[order] = np.arange(N)
fracs = np.round(np.arange(0, 1.0001, 0.05), 2)


def revenue_under(f, idx=None):
    pi = np.where(rank < f * N, best_email_arm, 0)
    a, s, p = (arm, spend, pi) if idx is None else (arm[idx], spend[idx], pi[idx])
    m = a == p
    return s[m].sum() / m.sum()


R_curve = np.array([revenue_under(f) for f in fracs])
rng = np.random.default_rng(SEED)
Rb = np.array([[revenue_under(f, rng.integers(0, N, N)) for f in fracs] for _ in range(200)])
curve = {"fractions": fracs, "revenue_per_customer": R_curve,
         "ci_lo": np.percentile(Rb, 2.5, axis=0), "ci_hi": np.percentile(Rb, 97.5, axis=0)}

# Blanket-mens revenue for reference (arm mean)
R_none = spend[arm == 0].mean(); R_mens = spend[arm == 1].mean(); R_womens = spend[arm == 2].mean()

# ------------------------------------------------------------------ uplift by decile
dec = pd.qcut(rank, 10, labels=False)       # 0 = highest predicted uplift
rows = []
for d in range(10):
    m = dec == d
    t = (arm[m] > 0).astype(int)
    vis_d, vis_se = diff_means(visit[m].astype(float), t)
    sp_d, sp_se = diff_means(spend[m], t)
    rows.append({"decile": d + 1, "n": int(m.sum()), "pred_rev_uplift": float(uplift_rev[m].mean()),
                 "visit_lift_pp": 100 * vis_d, "visit_lift_ci": [100 * (vis_d - 1.96 * vis_se), 100 * (vis_d + 1.96 * vis_se)],
                 "spend_lift": sp_d, "spend_lift_ci": [sp_d - 1.96 * sp_se, sp_d + 1.96 * sp_se],
                 "share_mens": float((best_email_arm[m] == 1).mean())})
deciles = rows
rho = stats.spearmanr([r["decile"] for r in rows], [r["visit_lift_pp"] for r in rows])[0]

# ------------------------------------------------------------------ sensitivity (economics)
margins = [0.20, 0.30, 0.40, 0.50, 0.60]
costs = [0.05, 0.10, 0.25, 0.50, 1.00, 2.00]
sens = []
for m_ in margins:
    for c_ in costs:
        pi_model = best_arm(P, m_, c_)
        v_none = policy_value(policies["Send nothing"], m_, c_)
        v_mens = policy_value(policies[ref], m_, c_)
        v_model = policy_value(pi_model, m_, c_)
        sens.append({"margin": m_, "cost": c_, "none": v_none, "blanket_mens": v_mens, "model": v_model,
                     "model_minus_blanket": v_model - v_mens,
                     "share_emailed": float((pi_model > 0).mean()),
                     "best": ["none", "blanket_mens", "model"][int(np.argmax([v_none, v_mens, v_model]))]})
breakeven_cost = GROSS_MARGIN * (R_mens - R_none)

# ------------------------------------------------------------------ exploratory segments (randomized, direct)
def seg_table(mask, label):
    out = {"segment": label, "n": int(mask.sum())}
    for name, a in [("mens", 1), ("womens", 2)]:
        m2 = mask & ((arm == 0) | (arm == a)); t = (arm[m2] == a).astype(int)
        vd, vs = diff_means(visit[m2].astype(float), t); sd_, ss = diff_means(spend[m2], t)
        out[name] = {"visit_lift_pp": 100 * vd, "visit_ci": [100 * (vd - 1.96 * vs), 100 * (vd + 1.96 * vs)],
                     "spend_lift": sd_, "spend_ci": [sd_ - 1.96 * ss, sd_ + 1.96 * ss]}
    return out

def interaction(mask):
    """Does the men's-email lift differ in `mask` vs the rest? (difference of lifts, normal approx)"""
    out = {}
    for nm, y in [("visit_pp", visit.astype(float) * 100), ("spend", spend)]:
        ls, ses = [], []
        for m_ in (mask, ~mask):
            m2 = m_ & ((arm == 0) | (arm == 1)); t = (arm[m2] == 1).astype(int)
            d_, se_ = diff_means(y[m2], t); ls.append(d_); ses.append(se_)
        diff = ls[0] - ls[1]; se = np.hypot(*ses)
        out[nm] = {"diff": diff, "se": se, "p": 2 * (1 - stats.norm.cdf(abs(diff / se)))}
    return out
both_interaction = interaction((mens_flag == 1) & (womens_flag == 1))

segs = [seg_table(np.ones(N, bool), "All customers"),
        seg_table((mens_flag == 1) & (womens_flag == 0), "Bought men's only"),
        seg_table((mens_flag == 0) & (womens_flag == 1), "Bought women's only"),
        seg_table((mens_flag == 1) & (womens_flag == 1), "Bought both"),
        seg_table(df["newbie"].values == 1, "New customers"),
        seg_table(df["newbie"].values == 0, "Existing customers"),
        seg_table(df["channel"].values == "Web", "Channel: Web"),
        seg_table(df["channel"].values == "Phone", "Channel: Phone"),
        seg_table(df["channel"].values == "Multichannel", "Channel: Multichannel"),
        seg_table(df["recency"].values <= 3, "Recency 1-3 months"),
        seg_table(df["recency"].values >= 10, "Recency 10-12 months")]

# ------------------------------------------------------------------ headline business case
n_scale = 1_000_000
bc = {"customers": n_scale, "margin": GROSS_MARGIN, "cost_per_email": COST_PER_EMAIL,
      "profit_none": n_scale * table["Send nothing"]["profit_per_customer"],
      "profit_blanket_mens": n_scale * table[ref]["profit_per_customer"],
      "incremental_vs_nothing": n_scale * (table[ref]["profit_per_customer"] - table["Send nothing"]["profit_per_customer"]),
      "breakeven_cost_per_email": breakeven_cost,
      "mens_over_womens_per_customer_profit": GROSS_MARGIN * (R_mens - R_womens)}

res = {"assumptions": {"gross_margin": GROSS_MARGIN, "cost_per_email": COST_PER_EMAIL},
       "policy_table": table, "curve": curve, "deciles": deciles, "decile_spearman_visit_lift": rho,
       "sensitivity": sens, "segments": segs, "both_interaction": both_interaction,
       "high_cost_scenario": {"cost_per_contact": HC_COST, "table": hc_table}, "business_case": bc,
       "arm_revenue": {"none": R_none, "mens": R_mens, "womens": R_womens}}
save_json(res, "03_uplift_policy.json")

# ------------------------------------------------------------------ figures
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "text.parse_math": False})
names = list(table)
fig, ax = plt.subplots(figsize=(8.5, 4))
vals = [table[k]["profit_per_customer"] for k in names]
errs = [[table[k]["profit_per_customer"] - table[k]["ci"][0] for k in names],
        [table[k]["ci"][1] - table[k]["profit_per_customer"] for k in names]]
colmap = {"Send nothing": "#8a8a8a", "Blanket men's email": "#2f6db5"}
cols = [colmap.get(k, "#3a8f5c" if k.startswith("Uplift") or k.startswith("Men's email to") else "#c9a27a") for k in names]
ax.barh(range(len(names)), vals, xerr=errs, color=cols, capsize=3); ax.invert_yaxis()
ax.set_yticks(range(len(names))); ax.set_yticklabels(names)
ax.set_xlabel(f"Expected profit per customer ($), {int(GROSS_MARGIN*100)}% margin, ${COST_PER_EMAIL:.2f}/email (95% CI)")
fig.tight_layout(); fig.savefig(FIG / "04_policy_profit.png", dpi=150); plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
x = [r["decile"] for r in rows]
axes[0].bar(x, [r["visit_lift_pp"] for r in rows],
            yerr=[[r["visit_lift_pp"] - r["visit_lift_ci"][0] for r in rows], [r["visit_lift_ci"][1] - r["visit_lift_pp"] for r in rows]],
            color="#2f6db5", capsize=2)
axes[0].set_title("Observed visit-rate lift by predicted-uplift decile (pp)"); axes[0].set_xlabel("Decile (1 = highest predicted uplift)")
axes[1].bar(x, [r["spend_lift"] for r in rows],
            yerr=[[r["spend_lift"] - r["spend_lift_ci"][0] for r in rows], [r["spend_lift_ci"][1] - r["spend_lift"] for r in rows]],
            color="#c4572f", capsize=2)
axes[1].set_title("Observed spend lift by decile ($ per customer)"); axes[1].set_xlabel("Decile")
fig.tight_layout(); fig.savefig(FIG / "05_uplift_deciles.png", dpi=150); plt.close(fig)

fig, ax = plt.subplots(figsize=(7.5, 4))
for c_, col in [(0.10, "#2f6db5"), (0.50, "#c4572f"), (1.00, "#3a8f5c")]:
    prof = GROSS_MARGIN * R_curve - c_ * fracs
    ax.plot(fracs * 100, prof, color=col, label=f"Model-ranked targeting, ${c_:.2f}/contact")
    ax.axhline(GROSS_MARGIN * R_none, color="#8a8a8a", lw=0.6)
    ax.plot([0, 100], [GROSS_MARGIN * R_none, GROSS_MARGIN * R_mens - c_], color=col, ls=":", lw=1)
ax.set_xlabel("% of customers contacted (highest predicted uplift first)"); ax.set_ylabel("Profit per customer ($)")
ax.set_title("Dotted = random targeting at the same contact levels"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "06_targeting_curves.png", dpi=150); plt.close(fig)

# ------------------------------------------------------------------ console summary
print(f"arm revenue/customer: none ${R_none:.3f} mens ${R_mens:.3f} womens ${R_womens:.3f}")
for k, v in table.items():
    print(f"{k:38s} ${v['profit_per_customer']:.3f}  CI[{v['ci'][0]:.3f},{v['ci'][1]:.3f}]  vs mens {v['vs_blanket_mens']:+.3f} "
          f"CI[{v['vs_blanket_mens_ci'][0]:+.3f},{v['vs_blanket_mens_ci'][1]:+.3f}]  none/mens/womens {v['share_none']:.2f}/{v['share_mens']:.2f}/{v['share_womens']:.2f}")
print("HIGH COST", HC_COST)
for k, v in hc_table.items(): print(f"  {k:36s} ${v['profit_per_customer']:.3f} CI{[round(x,3) for x in v['ci']]} vs none {v['vs_nothing']:+.3f} CI{[round(x,3) for x in v['vs_nothing_ci']]} emailed {v['share_emailed']:.2f}")
print("both interaction", both_interaction)
print("decile visit-lift spearman:", round(rho, 2))
for r in rows: print(r["decile"], round(r["visit_lift_pp"], 1), [round(x, 1) for x in r["visit_lift_ci"]], round(r["spend_lift"], 2))
print("breakeven cost/email:", round(breakeven_cost, 3))
print(pd.DataFrame(sens).round(3).to_string())
for s in segs: print(s["segment"], s["n"], "mens visit", round(s["mens"]["visit_lift_pp"], 1), "womens", round(s["womens"]["visit_lift_pp"], 1),
                      "| spend", round(s["mens"]["spend_lift"], 2), round(s["womens"]["spend_lift"], 2))
print("curve R:", dict(zip(fracs, np.round(R_curve, 3))))
