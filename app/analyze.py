"""Analysis of an uploaded randomized-test file. Pure standard library, so the app still starts with `python3 app/server.py`.

A file has one row per customer: which group the customer was in (for example no email, men's email, women's email), one or more outcomes
(visited, bought, spend) and, optionally, a pre-period column and customer attributes. The analysis returns plain JSON-friendly dictionaries:

  * how many customers are in each group and whether the split looks like a clean random assignment (sample-ratio check),
  * the lift of every group against the control for every outcome, with 95% intervals and p-values adjusted for several groups,
  * the same lift with CUPED variance reduction when a pre-period column is given,
  * covariate balance between groups,
  * who responds: lift by customer segment, with an adjustment for the number of segments looked at,
  * profit, gain vs control and break-even cost per email when a revenue column, a margin and a cost are given.

Intervals are normal-approximation intervals on differences of means (or proportions). They are accurate for the group sizes this kind of
test has (hundreds of customers or more per group) and fast enough to run while the user waits. Groups smaller than 30 are refused.
"""
import csv
import io
import math
import re
from collections import Counter

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 300_000
MAX_CELLS = 6_000_000
MAX_COLUMNS = 200
MIN_ROWS = 100
MIN_GROUP = 30
Z95 = 1.959963984540054
MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "#n/a"}
TRUE_WORDS = {"1", "true", "yes", "y", "t"}
FALSE_WORDS = {"0", "false", "no", "n", "f"}


class DataError(ValueError):
    """A problem with the file or the column choices, written so the person can fix it."""


# --------------------------------------------------------------------------------------------- reading the file
def _decode(raw):
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_table(raw):
    """Bytes of a CSV file -> (header, rows). Raises DataError with a plain explanation."""
    if len(raw) > MAX_BYTES:
        raise DataError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Remove columns you do not need or upload a sample.")
    if not raw.strip():
        raise DataError("The file is empty.")
    text = _decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise DataError("The file has no rows.")
    seen, names = Counter(), []
    for i, h in enumerate(header):
        h = h.strip() or f"column {i + 1}"
        seen[h] += 1
        names.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    if len(names) < 3:
        raise DataError("The first row must name at least 3 columns: the group, an outcome and one more. Is the first row a header?")
    if len(names) > MAX_COLUMNS:
        raise DataError(f"The file has {len(names)} columns; the limit is {MAX_COLUMNS}.")
    width, rows = len(names), []
    for r in reader:
        if not r or not any(c.strip() for c in r):
            continue
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width]
        rows.append(r)
        if len(rows) > MAX_ROWS:
            raise DataError(f"The file has more than {MAX_ROWS:,} rows. Upload a sample of {MAX_ROWS:,} rows or fewer.")
    if len(rows) < MIN_ROWS:
        raise DataError(f"The file has only {len(rows)} data rows. At least {MIN_ROWS} customers are needed.")
    if len(rows) * width > MAX_CELLS:
        raise DataError("The file is too wide for its length. Remove columns you do not need.")
    return names, rows


def num(v):
    """A number from a cell, or None. Accepts $1,234.50 and 12%."""
    s = v.strip()
    if s.lower() in MISSING:
        return None
    s = s.replace("$", "").replace("%", "")
    try:
        x = float(s)
    except ValueError:
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            return None
    return x if math.isfinite(x) else None


def as_binary(v):
    """0.0 or 1.0 from a cell (1/0, yes/no, true/false), or None."""
    s = v.strip().lower()
    if s in TRUE_WORDS:
        return 1.0
    if s in FALSE_WORDS:
        return 0.0
    return None


def numeric_or_binary(v, kind):
    return as_binary(v) if kind == "binary" else num(v)


# --------------------------------------------------------------------------------------------- understanding the columns
def profile(header, rows):
    cols = []
    for i, name in enumerate(header):
        raw = [r[i].strip() for r in rows]
        vals = [v for v in raw if v.lower() not in MISSING]
        info = {"name": name, "index": i, "missing": len(raw) - len(vals), "kind": "empty", "distinct": 0, "levels": [], "min": None, "max": None}
        if vals:
            counter = Counter(vals)
            info["distinct"] = len(counter)
            nums = [num(v) for v in vals]
            numeric = [x for x in nums if x is not None]
            lowered = {v.lower() for v in counter}
            if len(counter) == 1:
                info["kind"] = "constant"
            elif len(numeric) >= 0.98 * len(vals):
                info["kind"] = "binary" if {x for x in numeric} <= {0.0, 1.0} else "numeric"
                info["min"], info["max"] = min(numeric), max(numeric)
            elif lowered <= (TRUE_WORDS | FALSE_WORDS) and len(lowered) <= 2:
                info["kind"] = "binary"
            elif len(counter) <= 30:
                info["kind"] = "category"
            else:
                info["kind"] = "text"
            if len(counter) <= 12:
                info["levels"] = [[k, c] for k, c in counter.most_common()]
            elif info["kind"] == "numeric" or len(counter) <= 30:
                info["levels"] = []
            info["group_ok"] = 2 <= len(counter) <= 6
            if info["group_ok"]:
                info["levels"] = [[k, c] for k, c in counter.most_common()]
        else:
            info["group_ok"] = False
        cols.append(info)
    return cols


_GROUP_NAME = re.compile(r"group|segment|arm|variant|treat|test|cohort|campaign|email|cell|bucket", re.I)
_CONTROL_NAME = re.compile(r"^(control|ctrl|holdout|hold-out|baseline|untreated|no[\s_\-]*(e-?mail|mail|send|treatment|campaign|offer|message)?|none|off|0|false)$", re.I)
_REVENUE_NAME = re.compile(r"spend|revenue|sales|amount|value|order|gmv|price|total", re.I)
_PRE_NAME = re.compile(r"prior|pre_|pre-|^pre|before|previous|past|history|historical|baseline|last_", re.I)
_OUTCOME_NAME = re.compile(r"visit|convert|conversion|click|open|purchase|buy|bought|response|respond|signup|sign_up|churn|retain", re.I)


def suggest(cols):
    """A starting choice for every selector, so most people only press the button."""
    cands = [c for c in cols if c["group_ok"] and c["kind"] in ("category", "binary", "numeric")]
    pick = next((c for c in cands if _GROUP_NAME.search(c["name"])), cands[0] if cands else None)
    out = {"group": pick["name"] if pick else None, "control": None, "outcomes": [], "revenue": None, "pre": None, "features": []}
    if pick:
        lv = [k for k, _ in pick["levels"]]
        out["control"] = next((k for k in lv if _CONTROL_NAME.match(k.strip())), None) or next((k for k in lv if re.search(r"control|no e-?mail|holdout|none", k, re.I)), None) or lv[-1]
    rest = [c for c in cols if (not pick or c["name"] != pick["name"]) and c["kind"] in ("binary", "numeric")]
    pre = next((c for c in rest if _PRE_NAME.search(c["name"])), None)
    rest = [c for c in rest if c is not pre]
    rev = next((c for c in rest if c["kind"] == "numeric" and _REVENUE_NAME.search(c["name"])), None)
    prim = next((c for c in rest if c["kind"] == "binary" and _OUTCOME_NAME.search(c["name"])), None) or next((c for c in rest if c["kind"] == "binary"), None) or rev or (rest[0] if rest else None)
    if prim:
        out["outcomes"].append(prim["name"])
    if rev and rev["name"] not in out["outcomes"]:
        out["outcomes"].append(rev["name"])
    out["revenue"] = rev["name"] if rev else None
    out["pre"] = pre["name"] if pre else None
    used = set(out["outcomes"]) | ({pick["name"]} if pick else set())
    out["features"] = [c["name"] for c in cols if c["name"] not in used and c["kind"] in ("category", "binary", "numeric") and c["distinct"] >= 2 and c["distinct"] <= 30
                       and not re.search(r"id$|^id|_id", c["name"], re.I)][:6]
    return out


def describe(raw, filename="data.csv"):
    """Everything the mapping screen needs."""
    header, rows = read_table(raw)
    cols = profile(header, rows)
    return {"name": filename, "rows": len(rows), "columns": cols, "suggest": suggest(cols), "sample": rows[:5], "header": header}


# --------------------------------------------------------------------------------------------- statistics
def _mean_sd(xs):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = math.fsum(xs) / n
    if n < 2:
        return m, 0.0
    return m, math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (n - 1))


def p_from_z(z):
    return math.erfc(abs(z) / math.sqrt(2))


def _gammq(a, x):
    """Regularized upper incomplete gamma Q(a, x), for the chi-square p-value."""
    if x <= 0:
        return 1.0
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(500):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    b, c, d = x + 1 - a, 1e300, 1.0 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-14:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h)


def chi2_p(x, df):
    return _gammq(df / 2.0, x / 2.0)


def effect(a, b, binary, pooled=True):
    """Difference b - a between two samples summarised as (n, mean, sd). Returns diff, se, 95% interval and p-value."""
    n0, m0, s0 = a
    n1, m1, s1 = b
    diff = m1 - m0
    se = math.sqrt(s0 ** 2 / n0 + s1 ** 2 / n1)
    if binary and pooled:
        p = (m0 * n0 + m1 * n1) / (n0 + n1)
        se0 = math.sqrt(max(p * (1 - p), 1e-12) * (1 / n0 + 1 / n1))
    else:
        se0 = se
    z = diff / se0 if se0 > 0 else 0.0
    return {"diff": diff, "se": se, "ci": [diff - Z95 * se, diff + Z95 * se], "p": p_from_z(z)}


def _nice_up(x):
    if x <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(x))
    f = x / p
    return (1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10) * p


# --------------------------------------------------------------------------------------------- the analysis
def _col(cols, name, what):
    for c in cols:
        if c["name"] == name:
            return c
    raise DataError(f"The {what} column '{name}' is not in the file.")


def _need(mapping, key, label):
    v = mapping.get(key)
    if v in (None, ""):
        raise DataError(f"Choose the {label}.")
    return v


def _pos(mapping, key, label, lo, hi, default=None):
    v = mapping.get(key)
    if v in (None, ""):
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise DataError(f"{label} must be a number.")
    if not (lo <= x <= hi) or not math.isfinite(x):
        raise DataError(f"{label} must be between {lo:g} and {hi:g}.")
    return x


def analyze(raw, mapping, filename="data.csv"):
    header, rows = read_table(raw)
    cols = profile(header, rows)
    idx = {c["name"]: c["index"] for c in cols}

    gname = _need(mapping, "group", "group column")
    gcol = _col(cols, gname, "group")
    control = str(_need(mapping, "control", "control group")).strip()
    outcomes = [o for o in (mapping.get("outcomes") or []) if o]
    if not outcomes:
        raise DataError("Choose at least one outcome column, such as visited, bought or spend.")
    outcomes = list(dict.fromkeys(outcomes))[:4]
    ocols = []
    for o in outcomes:
        c = _col(cols, o, "outcome")
        if o == gname:
            raise DataError("The group column cannot also be an outcome.")
        if c["kind"] not in ("binary", "numeric"):
            raise DataError(f"The outcome '{o}' must be a number or a yes/no column.")
        ocols.append(c)
    revenue = mapping.get("revenue") or None
    if revenue:
        if revenue not in outcomes:
            raise DataError("The revenue column must also be one of the outcomes.")
        if next(c for c in ocols if c["name"] == revenue)["kind"] != "numeric":
            raise DataError("The revenue column must hold amounts, not yes/no values.")
    pre = mapping.get("pre") or None
    if pre:
        pc = _col(cols, pre, "pre-period")
        if pc["kind"] not in ("numeric", "binary") or pre in outcomes or pre == gname:
            raise DataError("The pre-period column must be a number measured before the test, and not one of the outcomes.")
    features = [f for f in (mapping.get("features") or []) if f and f not in outcomes and f != gname][:6]
    for f in features:
        fc = _col(cols, f, "customer attribute")
        if fc["kind"] in ("empty", "constant", "text"):
            raise DataError(f"The attribute '{f}' cannot be used: it has {'no values' if fc['kind'] == 'empty' else 'a single value' if fc['kind'] == 'constant' else 'too many different values'}.")

    # ---- groups
    gi = idx[gname]
    counts = Counter()
    for r in rows:
        g = r[gi].strip()
        if g and g.lower() not in MISSING:
            counts[g] += 1
    if control not in counts:
        raise DataError(f"The control group '{control}' does not appear in '{gname}'. Groups found: {', '.join(list(counts)[:8])}.")
    if len(counts) < 2:
        raise DataError(f"'{gname}' has only one group, so nothing can be compared.")
    if len(counts) > 6:
        raise DataError(f"'{gname}' has {len(counts)} different values. Choose the column that says which email each customer received.")
    small = [f"{g} ({n})" for g, n in counts.items() if n < MIN_GROUP]
    if small:
        raise DataError(f"Every group needs at least {MIN_GROUP} customers. Too small: {', '.join(small)}.")
    order = [control] + [g for g, _ in counts.most_common() if g != control]
    arms = order[1:]
    n_in_groups = sum(counts.values())

    # ---- sample-ratio check against an equal split
    exp = n_in_groups / len(order)
    chi = sum((counts[g] - exp) ** 2 / exp for g in order)
    srm_p = chi2_p(chi, len(order) - 1)
    srm = {"chi2": chi, "df": len(order) - 1, "p": srm_p, "expected_each": exp, "flag": srm_p < 0.001}

    # ---- outcome samples per group
    if pre:
        pk = next(c for c in cols if c["name"] == pre)["kind"]
    results = []
    for oi, oc in enumerate(ocols):
        k = oc["kind"]
        per = {g: [] for g in order}
        pairs = {g: [] for g in order} if pre else None
        dropped = 0
        ci_, pi_ = oc["index"], idx.get(pre) if pre else None
        for r in rows:
            g = r[gi].strip()
            if g not in per:
                continue
            y = numeric_or_binary(r[ci_], k)
            if y is None:
                dropped += 1
                continue
            per[g].append(y)
            if pre:
                x = numeric_or_binary(r[pi_], pk)
                if x is not None:
                    pairs[g].append((y, x))
        for g in order:
            if len(per[g]) < MIN_GROUP:
                raise DataError(f"Group '{g}' has only {len(per[g])} customers with a value for '{oc['name']}'. Every group needs at least {MIN_GROUP}.")
        binary = k == "binary"
        stats = {g: (len(per[g]),) + _mean_sd(per[g]) for g in order}
        arm_rows = [{"name": g, "n": stats[g][0], "mean": stats[g][1], "sd": stats[g][2], "se": stats[g][2] / math.sqrt(stats[g][0]),
                     "ci": [stats[g][1] - Z95 * stats[g][2] / math.sqrt(stats[g][0]), stats[g][1] + Z95 * stats[g][2] / math.sqrt(stats[g][0])]} for g in order]
        effs = []
        for g in arms:
            e = effect(stats[control], stats[g], binary)
            e["arm"] = g
            e["p_adj"] = min(1.0, e["p"] * len(arms))
            e["rel"] = e["diff"] / stats[control][1] if stats[control][1] > 0 else None
            effs.append(e)
        cuped = None
        if pre:
            allp = [t for g in order for t in pairs[g]]
            if len(allp) >= 2 * MIN_GROUP and all(len(pairs[g]) >= MIN_GROUP for g in order):
                xs = [t[1] for t in allp]
                xbar, sx = _mean_sd(xs)
                if sx > 0:
                    ybar = math.fsum(t[0] for t in allp) / len(allp)
                    cov = math.fsum((t[0] - ybar) * (t[1] - xbar) for t in allp) / (len(allp) - 1)
                    theta = cov / sx ** 2
                    adj = {g: [y - theta * (x - xbar) for y, x in pairs[g]] for g in order}
                    astats = {g: (len(adj[g]),) + _mean_sd(adj[g]) for g in order}
                    v_raw = _mean_sd([t[0] for t in allp])[1] ** 2
                    v_adj = _mean_sd([y - theta * (x - xbar) for y, x in allp])[1] ** 2
                    cuped = {"theta": theta, "variance_reduction": 1 - v_adj / v_raw if v_raw > 0 else 0.0, "n": len(allp)}
                    for e in effs:
                        a = effect(astats[control], astats[e["arm"]], binary, pooled=False)
                        e["adj"] = {"diff": a["diff"], "ci": a["ci"], "p_adj": min(1.0, a["p"] * len(arms))}
        results.append({"col": oc["name"], "type": k, "primary": oi == 0, "revenue": oc["name"] == revenue, "arms": arm_rows, "effects": effs,
                        "cuped": cuped, "dropped": dropped})

    # ---- covariate balance
    balance = []
    chk = list(features) + ([pre] if pre and pre not in features else [])
    for f in chk:
        fc = next(c for c in cols if c["name"] == f)
        fi = fc["index"]
        if fc["kind"] in ("numeric", "binary"):
            per = {g: [] for g in order}
            for r in rows:
                g = r[gi].strip()
                if g in per:
                    x = numeric_or_binary(r[fi], fc["kind"])
                    if x is not None:
                        per[g].append(x)
            if any(len(per[g]) < MIN_GROUP for g in order):
                continue
            s = {g: _mean_sd(per[g]) for g in order}
            worst = 0.0
            for g in arms:
                pooled = math.sqrt((s[g][1] ** 2 + s[control][1] ** 2) / 2)
                if pooled > 0:
                    worst = max(worst, abs(s[g][0] - s[control][0]) / pooled)
            balance.append({"feature": f, "check": f"Largest standardized difference between a group and control: {worst:.3f}", "flag": worst > 0.1,
                            "why": "Above 0.1 the groups differ noticeably on this attribute" if worst > 0.1 else "Under 0.1, so the groups look alike on this attribute"})
        else:
            lv = {}
            for r in rows:
                g = r[gi].strip()
                v = r[fi].strip()
                if g in counts and v.lower() not in MISSING:
                    lv.setdefault(v, Counter())[g] += 1
            if len(lv) < 2 or len(lv) > 12:
                continue
            tot = {g: sum(lv[v][g] for v in lv) for g in order}
            grand = sum(tot.values())
            x2 = 0.0
            for v in lv:
                rowtot = sum(lv[v].values())
                for g in order:
                    e_ = rowtot * tot[g] / grand
                    if e_ > 0:
                        x2 += (lv[v][g] - e_) ** 2 / e_
            p = chi2_p(x2, (len(lv) - 1) * (len(order) - 1))
            balance.append({"feature": f, "check": f"Chi-square test of group by level: p = {p:.3f}", "flag": p < 0.01,
                            "why": "A small p-value means the groups were not formed evenly on this attribute" if p < 0.01 else "A large p-value means the groups look alike on this attribute"})

    # ---- who responds: lift by segment on the primary outcome
    seg = _segments(rows, cols, idx, gi, order, control, arms, ocols[0], features, counts)

    # ---- profit
    profit = _profit(results, order, control, arms, mapping, revenue)

    # ---- headline
    prim = results[0]
    label_p = prim["col"]
    binary_p = prim["type"] == "binary"

    def fmt(x):
        return f"{x * 100:+.2f} points" if binary_p else f"{x:+.4g}"
    best_eff = max(prim["effects"], key=lambda e: e["diff"])
    clear_pos = best_eff["diff"] > 0 and best_eff["p_adj"] < 0.05
    worst_eff = min(prim["effects"], key=lambda e: e["diff"])
    clear_neg = worst_eff["diff"] < 0 and worst_eff["p_adj"] < 0.05
    if profit:
        ok = profit["best"] != control and profit["clear"]
        if profit["best"] == control:
            verdict = f"Do not send: no group is expected to beat '{control}' after costs"
        elif profit["clear"]:
            verdict = f"Send: {profit['best']} (expected to earn more than '{control}' after costs)"
        else:
            verdict = f"Probably send {profit['best']}, but the gain over '{control}' is not clear"
    elif clear_pos:
        ok, verdict = True, f"{best_eff['arm']} clearly beats '{control}' on {label_p}"
    elif clear_neg:
        ok, verdict = False, f"{worst_eff['arm']} does clearly worse than '{control}' on {label_p}"
    else:
        ok, verdict = False, f"No clear difference from '{control}' on {label_p}"

    notes = _notes(rows, n_in_groups, order, srm, balance, results, seg, arms, profit, pre, control, filename)
    return {
        "dataset": {"name": filename, "rows_read": len(rows), "rows_used": n_in_groups, "columns": len(header), "group": gname, "control": control,
                    "outcomes": outcomes, "revenue": revenue, "pre": pre, "features": features},
        "groups": [{"name": g, "n": counts[g], "share": counts[g] / n_in_groups, "control": g == control} for g in order],
        "srm": srm, "outcomes": results, "balance": balance, "segments": seg, "profit": profit,
        "ok": ok, "verdict": verdict, "notes": notes,
        "headline": verdict,
        "summary": {"arm": best_eff["arm"], "diff": best_eff["diff"], "ci": best_eff["ci"], "p_adj": best_eff["p_adj"], "outcome": label_p, "type": prim["type"],
                    "control_mean": prim["arms"][0]["mean"], "text": fmt(best_eff["diff"])},
    }


# --------------------------------------------------------------------------------------------- segments
def _bins(values):
    """Quartile bin edges for a numeric attribute."""
    s = sorted(values)
    n = len(s)
    return sorted({s[int(n * q)] for q in (0.25, 0.5, 0.75)})


def _num_label(x):
    return f"{x:,.4g}"


def _segments(rows, cols, idx, gi, order, control, arms, oc, features, counts):
    k = oc["kind"]
    oi = oc["index"]
    out = []
    n_tests = 0
    for f in features:
        fc = next(c for c in cols if c["name"] == f)
        fi = fc["index"]
        keyed = []
        if fc["kind"] == "numeric" and fc["distinct"] > 6:
            vals = [num(r[fi]) for r in rows]
            edges = _bins([v for v in vals if v is not None])
            if len(edges) < 2:
                continue

            def lab(v):
                for j, e_ in enumerate(edges):
                    if v <= e_:
                        return j, (f"{f} up to {_num_label(e_)}" if j == 0 else f"{f} {_num_label(edges[j - 1])} to {_num_label(e_)}")
                return len(edges), f"{f} above {_num_label(edges[-1])}"
            for r, v in zip(rows, vals):
                keyed.append(lab(v) if v is not None else None)
        else:
            if fc["distinct"] > 12:
                continue
            for r in rows:
                v = r[fi].strip()
                if v.lower() in MISSING:
                    keyed.append(None)
                else:
                    keyed.append((v, f"{f} = {v}"))
        groups = {}
        for r, kk in zip(rows, keyed):
            if kk is None:
                continue
            g = r[gi].strip()
            if g not in counts:
                continue
            y = numeric_or_binary(r[oi], k)
            if y is None:
                continue
            groups.setdefault(kk, {}).setdefault(g, []).append(y)
        for kk in sorted(groups, key=lambda t: (str(type(t[0])), t[0])):
            per = groups[kk]
            n_all = sum(len(v) for v in per.values())
            if n_all < 100 or len(per.get(control, [])) < MIN_GROUP:
                continue
            cs = (len(per[control]),) + _mean_sd(per[control])
            cells = []
            for g in arms:
                if len(per.get(g, [])) < MIN_GROUP:
                    cells.append(None)
                    continue
                e_ = effect(cs, (len(per[g]),) + _mean_sd(per[g]), k == "binary")
                e_["arm"], e_["n"] = g, len(per[g])
                cells.append(e_)
                n_tests += 1
            if any(cells):
                out.append({"feature": f, "level": kk[1], "n": n_all, "control_mean": cs[1], "cells": cells})
    out = sorted(out, key=lambda r: -r["n"])[:60] if len(out) > 60 else out
    for r in out:
        for c in r["cells"]:
            if c:
                c["p_adj"] = min(1.0, c["p"] * max(1, n_tests))
    return {"outcome": oc["name"], "type": k, "arms": arms, "rows": out, "n_tests": n_tests}


# --------------------------------------------------------------------------------------------- profit
def _profit(results, order, control, arms, mapping, revenue):
    if not revenue:
        return None
    margin = _pos(mapping, "margin_pct", "Gross margin", 0.1, 100, None)
    cost = _pos(mapping, "cost_per_contact", "Cost per email", 0, 1000, None)
    if margin is None or cost is None:
        return None
    margin /= 100
    n_list = _pos(mapping, "list_size", "Customers on list", 1, 1e9, 100000)
    fixed = _pos(mapping, "fixed_cost", "Fixed cost", 0, 1e10, 0.0)
    r = next(o for o in results if o["col"] == revenue)
    m0, se0 = r["arms"][0]["mean"], r["arms"][0]["se"]
    base = margin * m0
    pol = []
    pol.append({"name": control, "key": "none", "profit_pc": base, "ci_pc": [margin * (m0 - Z95 * se0), margin * (m0 + Z95 * se0)],
                "gain_pc": 0.0, "gain_ci_pc": [0.0, 0.0], "breakeven": None, "breakeven_ci": None, "share": 0.0})
    for i, g in enumerate(arms):
        a = next(x for x in r["arms"] if x["name"] == g)
        e = next(x for x in r["effects"] if x["arm"] == g)
        fx = fixed / n_list
        gain = margin * e["diff"] - cost - fx
        pol.append({"name": g, "key": f"g{i + 1}", "profit_pc": margin * a["mean"] - cost - fx,
                    "ci_pc": [margin * (a["mean"] - Z95 * a["se"]) - cost - fx, margin * (a["mean"] + Z95 * a["se"]) - cost - fx],
                    "gain_pc": gain, "gain_ci_pc": [margin * e["ci"][0] - cost - fx, margin * e["ci"][1] - cost - fx],
                    "breakeven": margin * e["diff"] - fx, "breakeven_ci": [margin * e["ci"][0] - fx, margin * e["ci"][1] - fx], "share": 1.0})
    best = max(pol, key=lambda p: p["profit_pc"])
    for p in pol:
        p["profit_total"] = p["profit_pc"] * n_list
        p["gain_total"] = p["gain_pc"] * n_list
        p["gain_ci_total"] = [x * n_list for x in p["gain_ci_pc"]]
        p["ci_total"] = [x * n_list for x in p["ci_pc"]]
        # tie with the best: the difference between this policy and the best includes zero
        if p is not best:
            sa = next(x for x in r["arms"] if x["name"] == p["name"])["se"]
            sb = next(x for x in r["arms"] if x["name"] == best["name"])["se"]
            d = p["profit_pc"] - best["profit_pc"]
            p["tied"] = abs(d) < Z95 * margin * math.sqrt(sa ** 2 + sb ** 2)
        else:
            p["tied"] = False
    bes = [p["breakeven"] for p in pol if p["breakeven"] is not None and p["breakeven"] > 0]
    cmax = _nice_up(max(bes + [cost, 0.05]) * 2)
    costs = [round(cmax * i / 30, 6) for i in range(31)]
    series = {"none": [base] * 31}
    fx = fixed / n_list
    for i, g in enumerate(arms):
        a = next(x for x in r["arms"] if x["name"] == g)
        series[f"g{i + 1}"] = [margin * a["mean"] - c - fx for c in costs]
    return {"revenue": revenue, "margin": margin, "cost": cost, "list_size": n_list, "fixed": fixed, "policies": pol, "best": best["name"], "best_key": best["key"],
            "clear": best["key"] != "none" and best["gain_ci_pc"][0] > 0, "curves": {"costs": costs, "series": series, "current_cost": cost},
            "labels": {"none": control, **{f"g{i + 1}": g for i, g in enumerate(arms)}}}


# --------------------------------------------------------------------------------------------- notes
def _notes(rows, used, order, srm, balance, results, seg, arms, profit, pre, control, filename):
    notes = []
    notes.append(["Causal only if assignment was random",
                  "The lift is the effect of the email only if customers were put in groups at random. Check how the groups in this file were formed"])
    skipped = len(rows) - used
    if skipped:
        notes.append([f"{skipped:,} rows had no group", "Rows without a group value were left out of every result"])
    for o in results:
        if o["dropped"]:
            notes.append([f"'{o['col']}' is missing for {o['dropped']:,} customers", "Those customers were left out of that outcome only"])
    if srm["flag"]:
        notes.append(["The group sizes look unusual", f"An equal split would be {srm['expected_each']:,.0f} each (chi-square p = {srm['p']:.4f}). If the split was meant to be uneven, ignore this; if not, assignment may be broken"])
    for b in balance:
        if b["flag"]:
            notes.append([f"Groups differ on '{b['feature']}'", "This can bias the comparison; check the assignment, or treat the result with caution"])
    smallest = min(a["n"] for a in results[0]["arms"])
    if smallest < 1000:
        notes.append([f"The smallest group has {smallest:,} customers", "Small groups give wide intervals, so only large effects can be detected"])
    if len(arms) > 1:
        notes.append([f"{len(arms)} groups were compared with {control!r}", "The adjusted p-value multiplies each p-value by the number of groups, so a chance result is less likely to look clear"])
    if seg["rows"]:
        notes.append([f"{seg['n_tests']} segment comparisons were made", "Segment results are exploratory: with many comparisons some look clear by chance. The adjusted p-value allows for that, and a confirming test is the safest check"])
    if profit:
        notes.append(["Profit uses the short-run revenue in this file", "Long-term value, returns and unsubscribes are not measured"])
        notes.append(["The control group is treated as receiving nothing", "Profit charges the cost per email only to the other groups. If the control also got a message, enter the extra cost of the other groups instead"])
    return notes
