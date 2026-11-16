"""HTML rendering (server side). Every dynamic value is escaped; charts are server-drawn SVG so reports are self-contained."""
import html
from pathlib import Path

from engine import money, POLICY_NAMES

APP_NAME = "Email Impact Studio"
CSS_PATH = Path(__file__).parent / "static" / "app.css"
e = html.escape


# ----------------------------------------------------------------------------------- layout
TAGLINE = "Compare email campaigns and score customers to see which email earns the most profit."
FONT_DIR = Path(__file__).parent / "static" / "fonts"
LOGO = '<img class="logo" src="/static/logo-128.png" alt="" width="44" height="44">'
FAVICONS = ('<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/static/favicon-64.png">'
            '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">')


NAV_ICONS = {'planner': '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg>', 'scorer': '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></svg>', 'history': '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>', 'evidence': '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17l5-6 4 3 8-9"/><path d="M3 21h18"/></svg>', 'data': '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/></svg>'}


def header_html(user=None, csrf="", active="", auth=False):
    """Side menu. Used by every app page and injected into the Evidence page so the menu is always there."""
    if auth or not user:
        return ""
    links = [("planner", "/planner", "Campaign planner"), ("scorer", "/scorer", "Customer scorer"), ("data", "/data", "Your data"),
             ("history", "/history", "History"), ("evidence", "/evidence", "Evidence")]
    items = ""
    for k, h, t in links:
        cur = ' aria-current="page"' if k == active else ""
        items += '<a href="' + h + '"' + cur + '>' + NAV_ICONS[k] + t + '</a>'
    initial = e((user["name"] or "?")[:1].upper())
    account = (f'<form method="post" action="/logout" class="logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<span class="avatar">{initial}</span><span class="who">{e(user["name"])}</span><button type="submit" class="link">Sign out</button></form>')
    return (f'<aside class="side"><a class="brand" href="/">{LOGO}<span>{APP_NAME}</span></a>'
            f'<nav class="nav" aria-label="Main">{items}</nav><div class="side-foot">{account}</div></aside>')


def layout(title, body, user=None, csrf="", active="", wide=False, shell=False, auth=False, source="Hillstrom email test, 64,000 customers, randomized"):
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf" content="{e(csrf)}"><title>{e(title)} | {APP_NAME}</title>
{FAVICONS}<link rel="stylesheet" href="/static/header.css"><link rel="stylesheet" href="/static/app.css"></head>
<body>{header_html(user, csrf, active, auth)}
<main class="{'shell' if shell else ''}">{body}</main>
<footer class="foot">Source: {e(source)}. Estimates, not guarantees.</footer>
<script src="/static/app.js"></script></body></html>"""


def table(headers, rows, num=(), item_first=False, cls=""):
    """Ruled table. Cells are already-escaped HTML. A row may be (cells, "best")."""
    head = "".join(f'<th class="{"num" if i in num else ""}">{e(h)}</th>' for i, h in enumerate(headers))
    out = []
    for row in rows:
        rc = ""
        if isinstance(row, tuple):
            row, rc = row
        tds = ""
        for i, c in enumerate(row):
            klass = ("num " if i in num else "") + ("item" if item_first and i == 0 else "")
            tds += '<td class="' + klass.strip() + '">' + c + '</td>'
        out.append(f'<tr class="{rc}">{tds}</tr>')
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{"".join(out)}</tbody></table></div>'


def reason_table(rows, headers=("Item", "Result", "Why")):
    return table(list(headers), [[e(a), c, e(r)] for a, c, r in rows], item_first=True)


def kpi(label, value, sub, grad, extra=""):
    return f'<div class="kpi {grad}"><span class="k">{e(label)}</span><span class="v">{e(value)}</span><span class="s">{e(sub)}</span>{extra}</div>'


def tick_strip(values, cols=20, rows=6):
    """Dot-matrix strip, like the reference: each column is a stack of dots, taller means more profit. Amber dots are columns with a loss."""
    if not values:
        return ""
    step = len(values) / cols
    picked = [values[min(len(values) - 1, int(i * step))] for i in range(cols)]
    lo, hi = min(min(picked), 0.0), max(max(picked), 0.0)
    span = (hi - lo) or 1.0
    cw, ch, r = 300 / cols, 52 / rows, min(300 / cols, 52 / rows) * .32
    out = []
    for i, v in enumerate(picked):
        lit = max(1, round((v - lo) / span * rows))
        for j in range(rows):
            on = j < lit
            cls = ("on neg" if v < 0 else "on") if on else "off"
            out.append(f'<circle class="{cls}" cx="{i * cw + cw / 2:.2f}" cy="{52 - (j * ch + ch / 2):.2f}" r="{r:.2f}"/>')
    return f'<svg class="ticks" viewBox="0 0 300 52" role="img" aria-label="Profit per customer as the cost per email rises, drawn as dots">{"".join(out)}</svg>'


def auth_stage(inner):
    """Sign-in screens: the project title on top, the box in the middle, the headline below."""
    return (f'<div class="auth-stage"><h1 class="auth-title"><img class="auth-logo" src="/static/logo-512.png" alt="" width="96" height="96">{APP_NAME}</h1>{inner}<h2 class="auth-headline">{e(TAGLINE)}</h2></div>')


def auth_page(kind, pre_csrf, error="", email="", name="", allow_register=True, note=""):
    is_login = kind == "login"
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    if note:
        err += f'<p class="notice" role="status">{e(note)}</p>'
    name_field = "" if is_login else (f'<label for="name">Your name</label><input id="name" name="name" autocomplete="name" '
                                      f'maxlength="80" required value="{e(name)}">')
    pw_hint = '<span class="hint"><a href="/forgot">Forgot your password?</a></span>' if is_login else '<span class="hint">At least 10 characters</span>'
    other = ('<span class="hint">New here? <a href="/register">Create an account</a></span>' if allow_register else "") if is_login else \
        '<span class="hint">Already registered? <a href="/login">Sign in</a></span>'
    body = f"""<section class="auth card">
<h2>{'Sign in' if is_login else 'Create your account'}</h2>
{err}
<form method="post" action="/{kind}" autocomplete="on">
<input type="hidden" name="csrf" value="{e(pre_csrf)}">
{name_field}
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required value="{e(email)}">
<label for="password">Password</label><input id="password" name="password" type="password"
 autocomplete="{'current-password' if is_login else 'new-password'}" required>{pw_hint}
<button type="submit" class="primary">{'Sign in' if is_login else 'Create account'}</button>
</form>{other}</section>"""
    return layout("Sign in" if is_login else "Create account", auth_stage(body), shell=True, auth=True)


def forgot_page(pre_csrf, sent=False, error=""):
    if sent:
        t = reason_table([("Result", "If an account exists, a reset link was sent", "The same message appears for every address, so nobody can check who has an account"),
                          ("Link", "Works once, expires in 60 minutes", "Limits the damage if the email is seen by someone else"),
                          ("Next step", '<a href="/login">Back to sign in</a>', "Use the new password after you save it")])
        body = f'<section class="auth card"><h2>Check your email</h2>{t}</section>'
        return layout("Check your email", auth_stage(body), shell=True, auth=True)
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = f"""<section class="auth card"><h2>Reset your password</h2>{err}
<form method="post" action="/forgot"><input type="hidden" name="csrf" value="{e(pre_csrf)}">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required><span class="hint">We email you a reset link</span>
<button type="submit" class="primary">Send reset link</button></form>
<span class="hint"><a href="/login">Back to sign in</a></span></section>"""
    return layout("Reset your password", auth_stage(body), shell=True, auth=True)


def reset_page(token, pre_csrf, error=""):
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = f"""<section class="auth card"><h2>Choose a new password</h2>{err}
<form method="post" action="/reset/{e(token)}"><input type="hidden" name="csrf" value="{e(pre_csrf)}">
<label for="password">New password</label><input id="password" name="password" type="password" autocomplete="new-password" required>
<span class="hint">At least 10 characters</span>
<label for="confirm">Repeat the new password</label><input id="confirm" name="confirm" type="password" autocomplete="new-password" required>
<button type="submit" class="primary">Save new password</button></form>
<span class="hint">Saving signs you out of every device</span></section>"""
    return layout("Choose a new password", auth_stage(body), shell=True, auth=True)


def _field(id_, label, value, hint="", **attrs):
    a = " ".join(f'{k.replace("_", "-")}="{e(str(v))}"' for k, v in attrs.items())
    h = f'<span class="hint">{hint}</span>' if hint else ""
    return f'<div class="field"><label for="{id_}">{label}</label><input id="{id_}" name="{id_}" value="{e(str(value))}" {a}>{h}</div>'


def _select(id_, label, options, value):
    opts = "".join(f'<option value="{e(str(v))}"{" selected" if str(v) == str(value) else ""}>{e(t)}</option>' for v, t in options)
    return f'<div class="field"><label for="{id_}">{label}</label><select id="{id_}" name="{id_}">{opts}</select></div>'


def _check(id_, label, checked, hint=""):
    h = f'<span class="hint">{hint}</span>' if hint else ""
    return (f'<div class="field check"><label><input type="checkbox" id="{id_}" name="{id_}"{" checked" if checked else ""}> {label}</label>{h}</div>')


def _empty(rows):
    return ('<div class="card span"><h2>Results appear here</h2>' + reason_table(rows, ("Output", "What you get", "Why")) + '</div>')


def planner_page(user, csrf, d):
    d = {"list_size": 100000, "margin_pct": 40, "cost_per_contact": 0.10, "fixed_cost": 0, "share_both_pct": 10.1, **d}
    empty = _empty([("Best policy", "One of four email plans", "The plan with the highest expected profit"),
                    ("Expected profit", "A total with a 95% interval", "Shows how sure the estimate is"),
                    ("Break-even cost", "The most one email can cost", "Above it, emailing everyone loses money")])
    body = f"""<div class="titlebar"><h1>Campaign planner</h1></div>
<form class="card form" data-api="/api/plan" novalidate>
<h2>Your campaign</h2>
<div class="formgrid">
{_field("list_size", "Customers on list", d["list_size"], "Active buyers in the last year", type="number", min="100", step="1", required="required")}
{_field("margin_pct", "Gross margin (%)", d["margin_pct"], "Profit share of sales, before email cost", type="number", min="1", max="100", step="0.1", required="required")}
{_field("cost_per_contact", "Cost per email ($)", d["cost_per_contact"], "Raise it for mail or offers", type="number", min="0", step="0.01", required="required")}
{_field("fixed_cost", "Fixed cost ($)", d["fixed_cost"], "Creative or setup, optional", type="number", min="0", step="1")}
{_field("share_both_pct", "Bought both lines (%)", d["share_both_pct"], "Men's and women's, 10.1% in the test", type="number", min="0", max="100", step="0.1", required="required")}
{_field("label", "Run name", "", "Optional", maxlength="80")}
<div class="field submit"><button type="submit" class="primary">Compare policies</button></div>
</div>
<p class="error" id="formError" role="alert" hidden></p>
</form>
<div id="result" class="result" aria-live="polite">{empty}</div>"""
    return layout("Campaign planner", body, user, csrf, "planner", wide=True)


def scorer_page(user, csrf, d):
    d = {"recency": 3, "history": 150, "mens": True, "womens": False, "newbie": False, "zip": "Urban", "channel": "Web",
         "margin_pct": 40, "cost_per_contact": 0.10, **d}
    rec = [(i, f"{i} month{'s' if i > 1 else ''}") for i in range(1, 13)]
    empty = _empty([("Verdict", "Men's email, women's email or none", "Based on expected profit for this customer"),
                    ("Expected gain", "Dollars per customer with a 95% interval", "Shows whether the gain is clear"),
                    ("Similar customers", "What the test showed for their group", "A check against real behaviour")])
    body = f"""<div class="titlebar"><h1>Customer scorer</h1></div>
<form class="card form" data-api="/api/score" novalidate>
<h2>The customer</h2>
<div class="formgrid">
{_select("recency", "Months since last purchase", rec, d["recency"])}
{_field("history", "Past-year spend ($)", d["history"], "Total spent in the last 12 months", type="number", min="0", step="0.01", required="required")}
<fieldset><legend>Product lines bought</legend>
{_check("mens", "Men's items", d["mens"])}{_check("womens", "Women's items", d["womens"])}</fieldset>
{_check("newbie", "New customer", d["newbie"], "First purchase in the last 12 months")}
{_select("zip", "Area", [("Urban", "Urban"), ("Suburban", "Suburban"), ("Rural", "Rural")], d["zip"])}
{_select("channel", "Channel", [("Web", "Web"), ("Phone", "Phone"), ("Multichannel", "Web and phone")], d["channel"])}
{_field("margin_pct", "Gross margin (%)", d["margin_pct"], "Profit share of sales", type="number", min="1", max="100", step="0.1", required="required")}
{_field("cost_per_contact", "Cost per email ($)", d["cost_per_contact"], "Charged if the email is sent", type="number", min="0", step="0.01", required="required")}
{_field("label", "Run name", "", "Optional", maxlength="80")}
<div class="field submit"><button type="submit" class="primary">Score customer</button></div>
</div>
<p class="error" id="formError" role="alert" hidden></p>
</form>
<div id="result" class="result" aria-live="polite">{empty}</div>"""
    return layout("Customer scorer", body, user, csrf, "scorer", wide=True)


def history_page(user, csrf, runs):
    if runs:
        rows = []
        for r in runs:
            act = (f'<a href="/report/{r["id"]}">Report</a><form method="post" action="/history/{r["id"]}/delete" data-confirm="Delete this run?">'
                   f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="link" type="submit">Delete</button></form>')
            rows.append([f'<a href="/history/{r["id"]}">{e(r["title"])}</a>', {"plan": "Campaign plan", "score": "Customer score", "data": "Your data"}.get(r["kind"], r["kind"]),
                         e(r["created_at"]), act])
        t = table(["Run", "Type", "Saved", "Actions"], rows)
        t = t.replace("<td data-label=\"Actions\" class=\"\">", "<td data-label=\"Actions\" class=\"act\">")
    else:
        t = reason_table([("Saved runs", "None yet", "Every run you make is saved here"),
                          ("Next step", '<a href="/planner">Campaign planner</a> or <a href="/scorer">Customer scorer</a> or <a href="/data">Your data</a>', "Run one to see it listed")])
    return layout("History", f'<div class="titlebar"><h1>History</h1></div><div class="card">{t}</div>', user, csrf, "history")


def run_page(user, csrf, run):
    inner = render_result(run)
    body = (f'<div class="titlebar"><h1>{e(run["title"])}</h1><span class="hint">Saved {e(run["created_at"])}</span></div>'
            f'<div class="actions"><a class="btn" href="/history">All runs</a><a class="btn" href="/report/{run["id"]}">Open report</a>'
            f'<a class="btn" href="/report/{run["id"]}/download">Download report</a></div><div class="result">{inner}</div>')
    return layout(run["title"], body, user, csrf, "history", source=("your uploaded file" if run["kind"] == "data" else "Hillstrom email test, 64,000 customers, randomized"))


def message_page(user, csrf, title, text, status_note=""):
    t = reason_table([("What happened", e(title), text), ("Next step", '<a href="/">Back to the app</a>', "Everything else is unchanged")])
    if user is None:
        return layout(title, auth_stage(f'<section class="auth card"><h2>{e(title)}</h2>{t}</section>'), shell=True, auth=True)
    return layout(title, f'<section class="card"><h1>{e(title)}</h1>{t}</section>', user, csrf)


# ----------------------------------------------------------------------------------- SVG charts
def _nice(lo, hi, n=5):
    """Round axis: returns (min, max, step, ticks) with steps of 1, 2, 2.5, 5 times a power of ten."""
    import math
    span = (hi - lo) or 1.0
    raw = span / n
    p = 10 ** math.floor(math.log10(raw))
    f = raw / p
    step = (1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10) * p
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    ticks, v = [], a
    while v <= b + step / 2:
        ticks.append(round(v, 10))
        v += step
    return a, b, step, ticks


def _decimals(step, floor=0):
    frac = ("%.10f" % step).rstrip("0").split(".")[1] if step % 1 else ""
    return max(floor, len(frac))


SHORT_NAMES = {"none": "Send nothing", "mens_all": "Men's to all", "womens_all": "Women's to all", "both_only": "Both-lines buyers"}


def _at(costs, ys, c):
    """Value of a line at cost c (straight-line interpolation between the computed points)."""
    if c <= costs[0]:
        return ys[0]
    for i in range(1, len(costs)):
        if c <= costs[i]:
            t = (c - costs[i - 1]) / (costs[i] - costs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


def _cross(costs, ys):
    """Cost at which a line crosses $0, or None if it never does."""
    for i in range(1, len(costs)):
        a, b = ys[i - 1], ys[i]
        if (a > 0 > b) or (a < 0 < b):
            return costs[i - 1] + a / (a - b) * (costs[i] - costs[i - 1])
    return None


def _spread(ys, gap):
    """Move label positions apart so no two are closer than `gap`, keeping their order."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for k in range(1, len(order)):
        i, j = order[k - 1], order[k]
        if out[j] - out[i] < gap:
            out[j] = out[i] + gap
    return out


def line_chart(costs, series, current, labels, best=None, w=760, h=440, short=True):
    """Profit per customer against cost per email. Green zone above $0, red zone below, each line named at its end,
    a dot on every line at the user's cost, and a ring where each line crosses $0 (its break-even cost)."""
    L, R, T, B = 78, 152, 50, 64
    xmax = costs[-1]
    vals = [v for s in series.values() for v in s]
    y0, y1, ystep, yticks = _nice(min(0, min(vals)), max(vals) * 1.04 if max(vals) > 0 else 0.1, 6)
    yd = _decimals(ystep, 2)
    X = lambda c: L + (w - L - R) * c / xmax
    Y = lambda v: T + (h - T - B) * (1 - (v - y0) / (y1 - y0))
    g = [f'<rect class="zone-good" x="{L}" y="{T}" width="{w - L - R}" height="{Y(0) - T:.1f}"/>',
         f'<rect class="zone-bad" x="{L}" y="{Y(0):.1f}" width="{w - L - R}" height="{h - B - Y(0):.1f}"/>']
    for t in yticks:
        g.append(f'<line class="grid" x1="{L}" x2="{w - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><line class="axis" x1="{L - 5}" x2="{L}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text x="{L - 9}" y="{Y(t) + 4.5:.1f}" text-anchor="end">{money(t, yd)}</text>')
    _, _, xstep, xticks = _nice(0, xmax, 6)
    xd = _decimals(xstep, 2)
    for c in [t for t in xticks if t <= xmax + 1e-9]:
        g.append(f'<line class="axis" x1="{X(c):.1f}" x2="{X(c):.1f}" y1="{h - B}" y2="{h - B + 5}"/><text x="{X(c):.1f}" y="{h - B + 21}" text-anchor="middle">{money(c, xd)}</text>')
    g.append(f'<line class="zero" x1="{L}" x2="{w - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    g.append(f'<line class="axis2" x1="{L}" x2="{L}" y1="{T}" y2="{h - B}"/><line class="axis2" x1="{L}" x2="{w - R}" y1="{h - B}" y2="{h - B}"/>')
    g.append(f'<text class="zone lbl" x="{w - R - 10}" y="{T + 20}" text-anchor="end">PROFIT: above $0 you earn money</text>')
    g.append(f'<text class="zone lbl" x="{w - R - 10}" y="{h - B - 12}" text-anchor="end">LOSS: below $0 you lose money</text>')
    g.append(f'<text x="{(L + w - R) / 2:.0f}" y="{h - 14}" text-anchor="middle" class="lbl ax">Cost per email ($)</text>')
    g.append(f'<text transform="translate(18 {(T + h - B) / 2:.0f}) rotate(-90)" text-anchor="middle" class="lbl ax">Profit per customer ($)</text>')
    for k, ys in series.items():
        pts = " ".join(f"{X(c):.1f},{Y(v):.1f}" for c, v in zip(costs, ys))
        g.append(f'<polyline class="line s-{k}{" best" if k == best else ""}" points="{pts}"/>')
    for k, ys in series.items():
        z = _cross(costs, ys)
        if z is not None:
            g.append(f'<circle class="ring s-{k}" cx="{X(z):.1f}" cy="{Y(0):.1f}" r="7"/>'
                     f'<text class="halo lbl" x="{X(z):.1f}" y="{Y(0) + 25:.1f}" text-anchor="middle">{money(z, 2)}</text>')
    cx = X(min(current, xmax))
    g.append(f'<line class="marker" x1="{cx:.1f}" x2="{cx:.1f}" y1="{T}" y2="{h - B}"/>')
    for k, ys in series.items():
        g.append(f'<circle class="pt s-{k}" cx="{cx:.1f}" cy="{Y(_at(costs, ys, min(current, xmax))):.1f}" r="6"/>')
    anchor = "end" if cx > (L + w - R) * 0.6 else "start"
    g.append(f'<text x="{cx + (-8 if anchor == "end" else 8):.1f}" y="{T - 14}" text-anchor="{anchor}" class="lbl">Your cost: {money(current)} per email</text>')
    keys = list(series)
    ends = [Y(series[k][-1]) for k in keys]
    for k, y in zip(keys, _spread(ends, 17)):
        nm = SHORT_NAMES.get(k, labels.get(k, k)) if short else labels.get(k, k)
        nm = nm if len(nm) <= 18 else nm[:17] + "…"
        g.append(f'<circle class="pt s-{k}" cx="{w - R + 10}" cy="{y:.1f}" r="5"/>'
                 f'<text class="lbl end{" top" if k == best else ""}" x="{w - R + 22}" y="{y + 4.5:.1f}">{e(nm)}</text>')
    return (f'<figure><svg viewBox="0 0 {w} {h}" role="img" aria-label="Profit per customer by cost per email for each policy. Lines above the zero line earn money, lines below lose money.">{"".join(g)}</svg></figure>')


def chart_guide(costs, series, current, labels, best=None, baseline="Baseline: no email and no email cost"):
    """Two plain tables under the chart: how to read it, and the numbers at the user's own cost."""
    xmax = costs[-1]
    how = [["Each line", "One email policy: its profit per customer at every cost per email"],
           ["Above or below $0", "Above the $0 line a policy earns money; below it, the policy loses money"],
           ["Vertical line and dots", "Your cost per email, " + money(current) + ". Compare the dots to see which policy is highest at your cost"],
           ["Rings on the $0 line", "Break-even cost: the price per email at which that policy stops earning money"]]
    if best:
        how.append(["Thick line", "The recommended policy at your cost"])
    rows = []
    at = {k: _at(costs, ys, min(current, xmax)) for k, ys in series.items()}
    top = max(at, key=at.get)
    for k in sorted(at, key=at.get, reverse=True):
        ys = series[k]
        z = _cross(costs, ys)
        if k == "none":
            be, why = "Not applicable", baseline
        else:
            be = money(z, 2) if z is not None else ("Above " + money(xmax, 2) if ys[0] > 0 else "Loses money at every cost")
            why = ("Highest profit at your cost" if k == top else
                   "Earns less than sending nothing" if at[k] < at.get("none", float("-inf")) else "Earns less than the best policy")
        rows.append(([e(labels.get(k, k)) + (' <span class="pill good">Best</span>' if k == top else ""), e(money(at[k], 3)), e(be), e(why)], "best" if k == top else ""))
    return ('<div class="guides"><div><h3>How to read this chart</h3>' + table(["Item", "What it means"], [[e(a), e(b)] for a, b in how], item_first=True) + '</div>'
            f'<div><h3>At your cost of {e(money(current))} per email</h3>' + table(["Policy", "Profit per customer", "Break-even cost", "Why"], rows, num=(1, 2)) + '</div></div>')


def gain_chart(arms, w=640):
    rows = [a for a in arms if a["arm"] != "No email"]
    L, R, T, B, rowh = 110, 30, 62, 62, 62
    h = T + B + rowh * len(rows)
    lo = min(0, *(a["gain_ci"][0] for a in rows))
    hi = max(0, *(a["gain_ci"][1] for a in rows))
    x0, x1, step, ticks = _nice(lo, hi * 1.03 if hi else 0.1, 6)
    d = _decimals(step, 2)
    X = lambda v: L + (w - L - R) * (v - x0) / (x1 - x0)
    g = [f'<rect class="zone-bad" x="{L}" y="{T}" width="{max(0, X(0) - L):.1f}" height="{h - B - T}"/>',
         f'<rect class="zone-good" x="{X(0):.1f}" y="{T}" width="{max(0, w - R - X(0)):.1f}" height="{h - B - T}"/>']
    for t in ticks:
        g.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{T}" y2="{h - B}"/><line class="axis" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{h - B}" y2="{h - B + 5}"/>'
                 f'<text x="{X(t):.1f}" y="{h - B + 20}" text-anchor="middle">{money(t, d)}</text>')
    g.append(f'<line class="axis2" x1="{L}" x2="{w - R}" y1="{h - B}" y2="{h - B}"/>')
    g.append(f'<line class="zero" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{T}" y2="{h - B}"/>')
    g.append(f'<text x="{X(0) - 10:.1f}" y="{T - 18}" text-anchor="end" class="lbl">Worse than no email</text><text x="{X(0) + 10:.1f}" y="{T - 18}" text-anchor="start" class="lbl">Better than no email</text>')
    g.append(f'<text x="{(L + w - R) / 2:.0f}" y="{h - 14}" text-anchor="middle" class="lbl ax">Expected gain per customer ($)</text>')
    for i, a in enumerate(rows):
        cy = T + rowh * i + rowh / 2
        cls = "s-mens_all" if a["arm"].startswith("Men") else "s-womens_all"
        lo_, hi_, v = a["gain_ci"][0], a["gain_ci"][1], a["gain_vs_none"]
        g.append(f'<text x="{L - 10}" y="{cy + 4:.1f}" text-anchor="end" class="lbl">{e(a["arm"])}</text>')
        g.append(f'<line class="whisker {cls}" x1="{X(lo_):.1f}" x2="{X(hi_):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>')
        for ex in (lo_, hi_):
            g.append(f'<line class="whisker {cls}" x1="{X(ex):.1f}" x2="{X(ex):.1f}" y1="{cy - 8:.1f}" y2="{cy + 8:.1f}"/>')
        g.append(f'<circle class="dot {cls}" cx="{X(v):.1f}" cy="{cy:.1f}" r="7"/>')
        g.append(f'<text class="halo lbl" x="{X(v):.1f}" y="{cy - 14:.1f}" text-anchor="middle">{money(v, 3)}</text>')
    return (f'<figure><svg viewBox="0 0 {w} {h}" role="img" aria-label="Expected profit gain vs no email, with 95% intervals. Right of the zero line beats no email.">{"".join(g)}</svg></figure>')


def gain_guide():
    rows = [["Dot", "Expected gain per customer from that email, after its cost"],
            ["Line", "95% interval: the range the true gain probably falls in"],
            ["Right of the zero line", "Better than sending no email"],
            ["Line crosses zero", "The gain is not clear: it could be a loss or a gain"]]
    return table(["Item", "What it means"], [[e(a), e(b)] for a, b in rows], item_first=True)


# ----------------------------------------------------------------------------------- results
def _notes_table(notes):
    """Notes as coloured insight rows: what was noticed on the left, why it matters on the right."""
    rows = []
    for k, n in enumerate(notes):
        if isinstance(n, str):
            n = [n, ""]
        rows.append(f'<div class="insight i{k % 4 + 1}"><b>{e(n[0])}</b><span>{e(n[1])}</span></div>')
    return f'<div class="insights" role="list">{"".join(rows)}</div>'



def _range(lo, hi, d=0):
    return f"{money(lo, d)} to {money(hi, d)}"


def _policy_reason(p, best, none_total, ties):
    if p["key"] == "none":
        return "Baseline: no email and no cost"
    if p["key"] == best:
        return "Highest expected profit of the four policies"
    if p["name"] in ties:
        return "Interval overlaps the best policy, so it is not clearly worse"
    if p["profit_total"] < none_total:
        return "Expected to earn less than sending nothing"
    return "Earns less than the best policy"


def render_plan_result(r, report=False):
    pol, best = r["policies"], r["best"]
    ok = best != "none"
    i = r["inputs"]
    bp = next(p for p in pol if p["key"] == best)
    none_total = next(p for p in pol if p["key"] == "none")["profit_total"]
    ties = r.get("ties", [])
    be = r["breakeven"]
    rows = []
    if ok:
        gain = bp["vs_none"]
        clear = gain["ci_total"][0] > 0
        rows.append(("Recommended policy", e(bp["name"]), "Highest expected profit of the four policies"))
        rows.append(("Expected profit", e(money(bp["profit_total"], 0)) + " for " + format(int(i["list_size"]), ",") + " customers",
                     "After margin and email cost" + (" and fixed cost" if i.get("fixed_cost") else "")))
        rows.append(("Gain vs sending nothing", e(money(gain["total"], 0)) + " (95%: " + e(_range(*gain["ci_total"])) + ")",
                     "Interval is above zero, so the gain is clear" if clear else "Interval includes zero, so the gain is not clear"))
        if ties:
            rows.append(("Not clearly better than", e("; ".join(ties)), "Their intervals overlap the best policy"))
    else:
        rows.append(("Recommended policy", "Send nothing", "No email policy is expected to beat sending nothing at this cost"))
    rows.append(("Break-even cost, email to all", e(money(be["value"])) + " (95%: " + e(money(be["ci"][0]) + " to " + money(be["ci"][1])) + ")",
                 "Above this cost, emailing everyone loses money"))
    ptab = []
    for p in pol:
        cells = [e(p["name"]) + (' <span class="pill good">Best</span>' if p["key"] == best else ""), format(p["share_contacted"], ".0%"), e(money(p["profit_total"], 0)), e(_range(*p["ci_total"])),
                 e(money(p["vs_none"]["total"], 0)) + " (" + e(_range(*p["vs_none"]["ci_total"])) + ")",
                 e(_policy_reason(p, best, none_total, ties))]
        ptab.append((cells, "best" if p["key"] == best else ""))
    cv = r["curves"]
    chart = line_chart(cv["costs"], cv["series"], cv["current_cost"], POLICY_NAMES, best if ok else None) + chart_guide(cv["costs"], cv["series"], cv["current_cost"], POLICY_NAMES, best if ok else None)
    strip = tick_strip(r["curves"]["series"].get(best if best != "none" else "mens_all", []))
    if ok:
        kp = [kpi("Expected profit", money(bp["profit_total"], 0), bp["name"] + ", " + format(int(i["list_size"]), ",") + " customers", "g1", strip),
              kpi("Gain vs no email", money(bp["vs_none"]["total"], 0), "95%: " + _range(*bp["vs_none"]["ci_total"]), "g2")]
    else:
        kp = [kpi("Recommended", "Send nothing", "No email beats doing nothing at this cost", "g1", strip),
              kpi("Profit if you do nothing", money(none_total, 0), format(int(i["list_size"]), ",") + " customers", "g2")]
    kp += [kpi("Break-even cost", money(be["value"]), "per email, 95%: " + money(be["ci"][0]) + " to " + money(be["ci"][1]), "g3"),
           kpi("Cost per email", money(i["cost_per_contact"]), format(int(i["list_size"]), ",") + " customers, " + format(i["margin"], ".0%") + " margin", "g4")]
    badge = f'<span class="pill {"good" if ok else "warn"}">{"Send an email" if ok else "Do not email"}</span>'
    inputs_t = f'<div class="kpis">{"".join(kp)}</div>'
    if report:
        return {"ok": ok, "headline": r["headline"],
                "decision": ("Recommended policy: " + bp["name"]) if ok else "Do not email: no policy is expected to beat sending nothing at this cost",
                "summary": reason_table(rows),
                "policy": table(["Policy", "Emailed", "Expected profit", "95% interval", "Gain vs nothing", "Why"], ptab, num=(1, 2)),
                "chart": chart, "notes": _notes_table(r["notes"])}
    return f"""<div class="card span {'good' if ok else 'warn'}"><h2>{e(r['headline'])} {badge}</h2>{inputs_t}{reason_table(rows)}</div>
<div class="card span"><h2>Policy comparison</h2>{table(["Policy", "Emailed", "Expected profit", "95% interval", "Gain vs nothing", "Why"], ptab, num=(1, 2))}</div>
<div class="card span"><h2>Profit vs cost per email</h2>{chart}</div>
<div class="card"><h2>Notes</h2>{_notes_table(r["notes"])}</div>"""


def render_score_result(r, report=False):
    c = r["customer"]
    v = r["verdict"]
    good = v.startswith("Send") or v.startswith("Probably")
    arms = r["arms"]
    best = next((a for a in arms if a["arm"] == r.get("best_arm")), arms[1])
    diff = r["mens_vs_womens"]
    if v.startswith("Do not"):
        why = "Both emails earn less than doing nothing at " + money(r.get("cost", 0)) + " per email"
    elif v.startswith("Send"):
        why = "The gain interval is above zero"
    else:
        why = "The gain is positive, but its interval includes zero"
    both_clear = not (diff["ci"][0] <= 0 <= diff["ci"][1])
    summary = [("Verdict", e(v), why),
               ("Expected gain", e(money(best["gain_vs_none"], 3)) + " per customer (95%: " + e(money(best["gain_ci"][0], 3) + " to " + money(best["gain_ci"][1], 3)) + ")",
                "Profit after email cost, vs sending nothing"),
               ("Men's vs women's email", ("The " + ("men's" if diff["value"] > 0 else "women's") + " email is clearly better") if both_clear else "Not clearly different",
                "The interval of the difference (" + money(diff["ci"][0], 3) + " to " + money(diff["ci"][1], 3) + ") " + ("excludes zero" if both_clear else "includes zero"))]
    arm_rows = []
    for a in arms:
        if a["arm"] == "No email":
            gain, w = "-", "Baseline: no email and no cost"
        else:
            gain = e(money(a["gain_vs_none"], 3)) + " (" + e(money(a["gain_ci"][0], 3) + " to " + money(a["gain_ci"][1], 3)) + ")"
            w = ("Gain interval is above zero" if a["gain_ci"][0] > 0 else
                 "Gain interval is below zero" if a["gain_ci"][1] < 0 else "Gain interval includes zero")
        arm_rows.append(([e(a["arm"]), format(a["p_visit"], ".1%"), e(money(a["expected_revenue"], 2)), e(money(a["expected_profit"], 3)), gain, e(w)],
                         "best" if a["arm"] == r.get("best_arm") and good else ""))
    seg, sn = r.get("segment"), r["segment_name"]
    seg_html, seg_inner = "", ""
    if seg:
        srows = []
        for lab, k in (("Men's email", "mens"), ("Women's email", "womens")):
            sk = seg[k]
            lo, hi = sk["visit_ci"]
            srows.append([lab, f'{sk["visit_lift_pp"]:+.1f}', f"{lo:+.1f} to {hi:+.1f}", e(money(sk["spend_lift"])),
                          "Interval is above zero: a clear lift in visits" if lo > 0 else "Interval includes zero: no clear lift in visits"])
        seg_inner = (f'<span class="hint">Group: {e(sn)}, {seg["n"]:,} customers in the test</span>'
                     + table(["Email", "Visit lift (points)", "95% interval", "Spend lift", "Why"], srows, num=(1, 2, 3), item_first=True))
        seg_html = f'<div class="card"><h2>Similar customers</h2>{seg_inner}</div>'
    lines = [("Months since last purchase", c["recency"]), ("Past-year spend", money(c["history_used"])),
             ("Product lines", ("men's " if c["mens"] else "") + ("and " if c["mens"] and c["womens"] else "") + ("women's" if c["womens"] else "")),
             ("New customer", "yes" if c["newbie"] else "no"), ("Area", c["zip"]), ("Channel", c["channel"])]
    none_arm = next(a for a in arms if a["arm"] == "No email")
    kp = [kpi("Expected gain", money(best["gain_vs_none"], 3), "per customer, 95%: " + money(best["gain_ci"][0], 3) + " to " + money(best["gain_ci"][1], 3), "g1"),
          kpi("Visit chance", format(best["p_visit"], ".1%"), best["arm"] + " vs " + format(none_arm["p_visit"], ".1%") + " with no email", "g2"),
          kpi("Profit after cost", money(best["expected_profit"], 3), best["arm"] + " vs " + money(none_arm["expected_profit"], 3) + " with no email", "g3")]
    chips = "".join(f'<span class="chip"><b>{e(k)}</b>{e(str(x))}</span>' for k, x in lines)
    cust_t = f'<div class="kpis">{"".join(kp)}</div><div class="chips">{chips}</div>'
    badge = f'<span class="pill {"good" if good else "warn"}">{"Send" if good else "Do not send"}</span>'
    if report:
        return {"good": good, "verdict": v, "summary": reason_table(summary),
                "arms": table(["Email", "Visit chance", "Two-week revenue", "Profit after cost", "Gain vs no email", "Why"], arm_rows, num=(1, 2, 3)),
                "chart": gain_chart(arms) + gain_guide(), "segment": seg_inner, "notes": _notes_table(r["notes"])}
    return f"""<div class="card span {'good' if good else 'warn'}"><h2>{e(v)} {badge}</h2>{cust_t}{reason_table(summary)}</div>
<div class="card span"><h2>Results per customer</h2>{table(["Email", "Visit chance", "Two-week revenue", "Profit after cost", "Gain vs no email", "Why"], arm_rows, num=(1, 2, 3))}</div>
<div class="card span"><h2>Expected gain vs no email</h2>{gain_chart(arms)}{gain_guide()}</div>
{seg_html}<div class="card"><h2>Notes</h2>{_notes_table(r["notes"])}</div>"""


# ----------------------------------------------------------------------------------- uploaded data
def _fv(x, typ, is_money=False):
    """A value: a rate for yes/no columns, dollars for revenue, a plain number otherwise."""
    if typ == "binary":
        return format(x, ".2%")
    return money(x, 2) if is_money else format(x, ",.4g")


def _fd(x, typ, is_money=False):
    """A difference with its sign: percentage points, dollars or a plain number."""
    if typ == "binary":
        return f"{x * 100:+.2f} points"
    if is_money:
        return ("+" if x >= 0 else "-") + money(abs(x), 2)
    return format(x, "+,.4g")


def _fr(lo, hi, typ, is_money=False):
    return _fd(lo, typ, is_money) + " to " + _fd(hi, typ, is_money)


def _tick_fmt(typ, is_money):
    def f(t, step):
        if typ == "binary":
            return format(t * 100, f".{_decimals(step * 100, 0)}f")
        if is_money:
            return money(t, _decimals(step, 2))
        return format(t, ".4g")
    return f


def forest_chart(rows, xlabel, tick, aria, w=720, left="Worse than control", right="Better than control"):
    """Dots with 95% intervals. rows: dicts with name, v, lo, hi, cls, text. Left of the zero line is worse than the control."""
    L, R, T, B, rowh = 190, 40, 62, 64, 48
    h = T + B + rowh * len(rows)
    lo = min([0.0] + [r["lo"] for r in rows])
    hi = max([0.0] + [r["hi"] for r in rows])
    x0, x1, step, ticks = _nice(lo, hi * 1.03 if hi > 0 else 0.1, 6)
    X = lambda v: L + (w - L - R) * (v - x0) / (x1 - x0)
    g = [f'<rect class="zone-bad" x="{L}" y="{T}" width="{max(0, X(0) - L):.1f}" height="{h - B - T}"/>',
         f'<rect class="zone-good" x="{X(0):.1f}" y="{T}" width="{max(0, w - R - X(0)):.1f}" height="{h - B - T}"/>']
    for t in ticks:
        g.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{T}" y2="{h - B}"/><line class="axis" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{h - B}" y2="{h - B + 5}"/>'
                 f'<text x="{X(t):.1f}" y="{h - B + 21}" text-anchor="middle">{e(tick(t, step))}</text>')
    g.append(f'<line class="axis2" x1="{L}" x2="{w - R}" y1="{h - B}" y2="{h - B}"/><line class="zero" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{T}" y2="{h - B}"/>')
    g.append(f'<text x="{X(0) - 10:.1f}" y="{T - 18}" text-anchor="end" class="lbl">{e(left)}</text><text x="{X(0) + 10:.1f}" y="{T - 18}" text-anchor="start" class="lbl">{e(right)}</text>')
    g.append(f'<text x="{(L + w - R) / 2:.0f}" y="{h - 14}" text-anchor="middle" class="lbl ax">{e(xlabel)}</text>')
    for i, r in enumerate(rows):
        cy = T + rowh * i + rowh / 2
        nm = r["name"] if len(r["name"]) <= 26 else r["name"][:25] + "…"
        g.append(f'<text x="{L - 10}" y="{cy + 4.5:.1f}" text-anchor="end" class="lbl">{e(nm)}</text>')
        g.append(f'<line class="whisker {r["cls"]}" x1="{X(r["lo"]):.1f}" x2="{X(r["hi"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>')
        for ex in (r["lo"], r["hi"]):
            g.append(f'<line class="whisker {r["cls"]}" x1="{X(ex):.1f}" x2="{X(ex):.1f}" y1="{cy - 8:.1f}" y2="{cy + 8:.1f}"/>')
        g.append(f'<circle class="dot {r["cls"]}" cx="{X(r["v"]):.1f}" cy="{cy:.1f}" r="7"/>')
        g.append(f'<text class="halo lbl" x="{X(r["v"]):.1f}" y="{cy - 14:.1f}" text-anchor="middle">{e(r["text"])}</text>')
    return f'<figure><svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(aria)}">{"".join(g)}</svg></figure>'


def forest_guide(control):
    rows = [["Dot", "The measured lift of that group over the control group, '" + control + "'"],
            ["Line", "95% interval: the range the true lift probably falls in"],
            ["Right of the zero line", "Better than the control group"],
            ["Line crosses zero", "The lift is not clear: it could be a gain or a loss"]]
    return table(["Item", "What it means"], [[e(a), e(b)] for a, b in rows], item_first=True)


def _why_effect(ef, k):
    lo, hi = ef["ci"]
    if lo > 0 and ef["p_adj"] < 0.05:
        return "Clearly higher than the control"
    if hi < 0 and ef["p_adj"] < 0.05:
        return "Clearly lower than the control"
    if lo > 0 or hi < 0:
        return f"Clear on its own, but not after allowing for {k} groups"
    return "Interval includes zero: no clear difference"


def render_data_result(r, report=False):
    ds, srm, prof = r["dataset"], r["srm"], r["profit"]
    outs, control = r["outcomes"], ds["control"]
    prim = outs[0]
    pm = prim["revenue"]
    ok = r["ok"]
    arms = [g["name"] for g in r["groups"] if not g["control"]]
    cls_of = {g: f"s-g{i + 1}" for i, g in enumerate(arms)}
    sm = r["summary"]
    kp = [kpi(f"Lift in {prim['col']}", _fd(sm["diff"], prim["type"], pm), f"{sm['arm']} vs {control}, 95%: {_fr(*sm['ci'], prim['type'], pm)}", "g1"),
          kpi(f"{control}", _fv(prim["arms"][0]["mean"], prim["type"], pm), f"{prim['col']} in the control group, {prim['arms'][0]['n']:,} customers", "g2")]
    if prof:
        bp = next(x for x in prof["policies"] if x["name"] == prof["best"])
        if bp["key"] != "none":
            kp += [kpi("Break-even cost", money(bp["breakeven"]), f"per email for {bp['name']}, 95%: {money(bp['breakeven_ci'][0])} to {money(bp['breakeven_ci'][1])}", "g3"),
                   kpi("Expected gain", money(bp["gain_total"], 0), f"vs {control} for {format(int(prof['list_size']), ',')} customers", "g4")]
        else:
            kp += [kpi("Recommended", "Send nothing", "No group beats the control after costs", "g3"),
                   kpi("Profit if you do nothing", money(prof["policies"][0]["profit_total"], 0), f"for {format(int(prof['list_size']), ',')} customers", "g4")]
    else:
        kp += [kpi("Customers analysed", format(ds["rows_used"], ","), f"in {len(r['groups'])} groups", "g3"), kpi("Groups compared", str(len(arms)), f"each against {control}", "g4")]
    badge = f'<span class="pill {"good" if ok else "warn"}">{"Clear result" if ok else "Not clear"}</span>'
    summary_rows = [("Verdict", e(r["verdict"]), "Based on the primary outcome" + (" and profit" if prof else ""))]
    summary_rows.append(("Biggest lift", e(f"{sm['arm']}: {_fd(sm['diff'], prim['type'], pm)} in {prim['col']} (95%: {_fr(*sm['ci'], prim['type'], pm)})"),
                         "Adjusted p-value " + (format(sm["p_adj"], ".4f") if sm["p_adj"] >= 0.0001 else "below 0.0001") + (", so the lift is clear" if sm["p_adj"] < 0.05 else ", so the lift is not clear")))
    summary_rows.append(("Customers", e(f"{ds['rows_used']:,} in {len(r['groups'])} groups"), "; ".join(f"{g['name']}: {g['n']:,}" for g in r["groups"])))
    summary_rows.append(("Group split", "Looks unusual" if srm["flag"] else "Looks even", f"Sample-ratio check p = {srm['p']:.4f}"
                         + (": the groups are not the same size, so check how customers were assigned" if srm["flag"] else ": consistent with a clean random assignment")))
    summary = reason_table(summary_rows)
    # ---- one table (and chart) per outcome
    out_blocks = []
    for o in outs:
        mon = o["revenue"]
        typ = o["type"]
        has_adj = bool(o["cuped"])
        head = ["Group", "Customers", "Rate" if typ == "binary" else "Average", "Lift vs control (95% interval)", "Relative lift"] + (["With CUPED"] if has_adj else []) + ["Adjusted p-value", "Why"]
        rows = [[e(o["arms"][0]["name"]) + ' <span class="pill">Control</span>', format(o["arms"][0]["n"], ","), e(_fv(o["arms"][0]["mean"], typ, mon)), "-", "-"] + (["-"] if has_adj else []) + ["-", "Baseline group"]]
        for a in o["arms"][1:]:
            ef = next(x for x in o["effects"] if x["arm"] == a["name"])
            row = [e(a["name"]), format(a["n"], ","), e(_fv(a["mean"], typ, mon)), e(f"{_fd(ef['diff'], typ, mon)} ({_fr(*ef['ci'], typ, mon)})"),
                   e(f"{ef['rel'] * 100:+.0f}%") if ef["rel"] is not None else "-"]
            if has_adj:
                ad = ef.get("adj")
                row.append(e(f"{_fd(ad['diff'], typ, mon)} ({_fr(*ad['ci'], typ, mon)})") if ad else "-")
            row += [format(ef["p_adj"], ".4f") if ef["p_adj"] >= 0.0001 else "&lt; 0.0001", e(_why_effect(ef, len(arms)))]
            rows.append((row, "best" if ef["p_adj"] < 0.05 and ef["diff"] > 0 and ef["diff"] == max(x["diff"] for x in o["effects"]) else ""))
        t = table(head, rows, num=(1, 2))
        fr = [{"name": ef["arm"], "v": ef["diff"], "lo": ef["ci"][0], "hi": ef["ci"][1], "cls": cls_of[ef["arm"]], "text": _fd(ef["diff"], typ, mon)} for ef in o["effects"]]
        ch = forest_chart(fr, "Lift in " + o["col"] + (" (percentage points)" if typ == "binary" else ""), _tick_fmt(typ, mon), f"Lift in {o['col']} for each group against the control, with 95% intervals")
        cu = ""
        if has_adj:
            cu = reason_table([("CUPED", e((f"{o['cuped']['variance_reduction'] * 100:.1f}" if o['cuped']['variance_reduction'] < 0.1 else f"{o['cuped']['variance_reduction'] * 100:.0f}") + "% less variance"), "The pre-period column explains part of each customer's outcome, so the adjusted interval is narrower")])
        out_blocks.append((o["col"], t, ch, cu))
    # ---- data checks
    chk_rows = [("Group sizes", e("; ".join(f"{g['name']}: {g['n']:,}" for g in r["groups"])), "Every group needs enough customers for a stable estimate"),
                ("Split check", e(f"Chi-square {srm['chi2']:.2f}, p = {srm['p']:.4f}"), "A very small p-value means the split between groups is not what random assignment would give" if srm["flag"] else "Not small, so the split is consistent with random assignment")]
    for b in r["balance"]:
        chk_rows.append((b["feature"], e(b["check"]), b["why"]))
    for o in outs:
        if o["dropped"]:
            chk_rows.append((f"Missing '{o['col']}'", e(f"{o['dropped']:,} customers"), "Left out of that outcome only"))
    checks = reason_table(chk_rows)
    # ---- who responds
    sg = r["segments"]
    seg_tab, seg_chart = "", ""
    if sg["rows"]:
        typ, mon = sg["type"], any(o["revenue"] and o["col"] == sg["outcome"] for o in outs)
        hd = ["Segment", "Customers", "Control"] + [f"Lift: {a}" for a in sg["arms"]] + ["Why"]
        srows = []
        for sr in sg["rows"]:
            cells = []
            clear_for = []
            for c in sr["cells"]:
                if c is None:
                    cells.append("-")
                else:
                    cells.append(e(f"{_fd(c['diff'], typ, mon)} ({_fr(*c['ci'], typ, mon)})"))
                    if c["diff"] > 0 and c["p_adj"] < 0.05:
                        clear_for.append(c["arm"])
            why = ("Clear lift for " + ", ".join(clear_for) + f" after allowing for {sg['n_tests']} comparisons") if clear_for else f"No clear lift after allowing for {sg['n_tests']} comparisons"
            srows.append(([e(sr["level"]), format(sr["n"], ","), e(_fv(sr["control_mean"], typ, mon))] + cells + [e(why)], "best" if clear_for else ""))
        seg_tab = table(hd, srows, num=(1, 2))
        best_arm = sm["arm"]
        bi = sg["arms"].index(best_arm) if best_arm in sg["arms"] else 0
        fr = []
        for sr in sg["rows"]:
            c = sr["cells"][bi]
            if c:
                fr.append({"name": sr["level"], "v": c["diff"], "lo": c["ci"][0], "hi": c["ci"][1], "cls": cls_of.get(best_arm, "s-g1"), "text": _fd(c["diff"], typ, mon)})
        fr = fr[:24]
        if fr:
            seg_chart = forest_chart(fr, f"Lift in {sg['outcome']} for {best_arm}" + (" (percentage points)" if typ == "binary" else ""), _tick_fmt(typ, mon),
                                     f"Lift in {sg['outcome']} by customer segment for {best_arm}, with 95% intervals")
    # ---- profit
    profit_tab, profit_chart = "", ""
    if prof:
        ptab = []
        for pl in prof["policies"]:
            if pl["key"] == "none":
                why = "Baseline: the control group, no email cost"
            elif pl["name"] == prof["best"]:
                why = "Highest expected profit"
            elif pl["tied"]:
                why = "Interval overlaps the best group, so not clearly worse"
            elif pl["profit_pc"] < prof["policies"][0]["profit_pc"]:
                why = "Expected to earn less than the control"
            else:
                why = "Earns less than the best group"
            ptab.append(([e(pl["name"]) + (' <span class="pill good">Best</span>' if pl["name"] == prof["best"] else ""), e(money(pl["profit_total"], 0)), e(_range(*pl["ci_total"])),
                          e(money(pl["gain_total"], 0)) + ("" if pl["key"] == "none" else " (" + e(_range(*pl["gain_ci_total"])) + ")"),
                          "-" if pl["breakeven"] is None else e(money(pl["breakeven"])) + " (" + e(money(pl["breakeven_ci"][0]) + " to " + money(pl["breakeven_ci"][1])) + ")", e(why)],
                         "best" if pl["name"] == prof["best"] else ""))
        profit_tab = table(["Group", "Expected profit", "95% interval", "Gain vs control", "Break-even cost per email", "Why"], ptab, num=(1,))
        cv = prof["curves"]
        profit_chart = (line_chart(cv["costs"], cv["series"], cv["current_cost"], prof["labels"], prof["best_key"], short=False)
                        + chart_guide(cv["costs"], cv["series"], cv["current_cost"], prof["labels"], prof["best_key"], "Baseline: the control group, no email cost"))
    if report:
        return {"ok": ok, "verdict": r["verdict"], "summary": summary, "outcomes": out_blocks, "checks": checks, "segments_table": seg_tab, "segments_chart": seg_chart,
                "profit_table": profit_tab, "profit_chart": profit_chart, "notes": _notes_table(r["notes"]), "guide": forest_guide(control)}
    parts = [f'<div class="card span {"good" if ok else "warn"}"><h2>{e(r["verdict"])} {badge}</h2><div class="kpis">{"".join(kp)}</div>{summary}</div>']
    for k, (col, t, ch, cu) in enumerate(out_blocks):
        parts.append(f'<div class="card span"><h2>Results for {e(col)}</h2>{t}{cu}{ch}{forest_guide(control) if k == 0 else ""}</div>')
    parts.append(f'<div class="card span"><h2>Data checks</h2>{checks}</div>')
    if seg_tab:
        parts.append(f'<div class="card span"><h2>Who responds</h2>{seg_tab}{seg_chart}</div>')
    if prof:
        parts.append(f'<div class="card span"><h2>Profit and break-even</h2>{profit_tab}{profit_chart}</div>')
    parts.append(f'<div class="card span"><h2>Notes</h2>{_notes_table(r["notes"])}</div>')
    return "\n".join(parts)


def data_page(user, csrf):
    fmt = reason_table([("One row per customer", "A CSV file with a header row", "Each row is one customer in the test"),
                        ("A group column", "Which email or treatment each customer got", "Lets the app compare groups; one group is the control"),
                        ("An outcome column", "Visited, bought or spend", "What you want to measure"),
                        ("Optional columns", "A pre-period spend, customer attributes", "Tighten the intervals and show who responds")])
    body = f"""<div class="titlebar"><h1>Your data</h1></div>
<section class="card" id="uploadCard"><h2>1. Choose a file</h2>{fmt}
<div class="actions"><input type="file" id="fileInput" accept=".csv,text/csv,.txt,.tsv"><button type="button" class="primary" id="readBtn">Read file</button></div>
<p class="error" id="uploadError" role="alert" hidden></p></section>
<form class="card" id="mapForm" hidden novalidate></form>
<div id="result" class="result" aria-live="polite"></div>
<script src="/static/data.js"></script>"""
    return layout("Your data", body, user, csrf, "data", wide=True, source="your uploaded file")


def render_result(run):
    if run["kind"] == "data":
        return render_data_result(run["result"])
    return render_plan_result(run["result"]) if run["kind"] == "plan" else render_score_result(run["result"])


# ----------------------------------------------------------------------------------- report
def font_face_css():
    """Calibri (or its metric-identical stand-in Carlito) embedded so a downloaded report looks the same anywhere."""
    import base64
    out = ""
    for weight, file, local in ((400, "Carlito-Regular.subset.ttf", "Calibri"), (700, "Carlito-Bold.subset.ttf", "Calibri Bold")):
        data = base64.b64encode((FONT_DIR / file).read_bytes()).decode("ascii")
        out += ('@font-face{font-family:"Calibri";font-weight:' + str(weight) + ';src:local("' + local + '"),url(data:font/ttf;base64,' + data + ') format("truetype")}')
    return out


def _logo_uri():
    import base64
    return "data:image/png;base64," + base64.b64encode((Path(__file__).parent / "static" / "logo-128.png").read_bytes()).decode("ascii")


def _report_inputs(run):
    r = run["result"]
    if run["kind"] == "plan":
        i = r["inputs"]
        rows = [("Customers on list", format(int(i["list_size"]), ","), "Number of customers you would email"),
                ("Gross margin", format(i["margin"], ".0%"), "Share of each sale kept as profit before email costs"),
                ("Cost per email", money(i["cost_per_contact"]), "What it costs to send one email"),
                ("Fixed campaign cost", money(i["fixed_cost"], 0), "One-off creative or set-up cost, charged to any policy that sends email"),
                ("Share who bought both lines", format(i["share_both"], ".1%"), "Customers who bought men's and women's items; 10.1% in the test")]
    else:
        c = r["customer"]
        rows = [("Months since last purchase", str(c["recency"]), "More recent buyers tend to respond more"),
                ("Past-year spend", money(c["history_used"]), "Spend used by the model (limited to the range it saw)"),
                ("Product lines bought", ("men's " if c["mens"] else "") + ("and " if c["mens"] and c["womens"] else "") + ("women's" if c["womens"] else "none"), "Which email suits past purchases"),
                ("New customer", "Yes" if c["newbie"] else "No", "Customers acquired in the last twelve months"),
                ("Area", str(c["zip"]), "Urban, suburban or rural"), ("Channel", str(c["channel"]), "Where the customer buys"),
                ("Gross margin", format(r["margin"], ".0%"), "Share of each sale kept as profit before email costs"),
                ("Cost per email", money(r["cost"]), "What it costs to send one email")]
    return table(["Input", "Value", "Meaning"], [[e(a), e(b), e(c_)] for a, b, c_ in rows], item_first=True)


REPORT_GLOSSARY = [
    ("95% interval", "The range that would contain the true value in about 95 of 100 repeats of the test; a wide range means a less certain estimate"),
    ("Gain vs no email", "Expected profit of a policy minus the expected profit of sending nothing"),
    ("Break-even cost", "The cost per email at which emailing everyone earns exactly as much as sending nothing"),
    ("Gross margin", "The share of each sale that is profit before the cost of the email"),
    ("Randomized test", "Customers were assigned to an email at random, so differences between groups are caused by the email"),
    ("Bootstrap", "Repeating the calculation on many resampled copies of the data to measure how much an estimate could move by chance")]


DATA_GLOSSARY = [
    ("Control group", "The customers who received nothing (or the old version); every other group is compared with it"),
    ("CUPED", "Adjusting each customer's outcome by what was known before the test, which narrows the intervals without changing the average effect"),
    ("Adjusted p-value", "The p-value multiplied by the number of comparisons made, so a chance result is less likely to look clear"),
    ("Sample-ratio check", "A test of whether the group sizes match what random assignment would give; a very small p-value suggests the assignment was not random")]


def _data_setup(run):
    r = run["result"]
    ds, prof = r["dataset"], r["profit"]
    rows = [("File", ds["name"], "The file the analysis was run on"),
            ("Customers", f"{ds['rows_read']:,} rows read, {ds['rows_used']:,} with a group", "Rows without a group value are left out"),
            ("Group column", ds["group"], "Which email or treatment each customer received"),
            ("Control group", ds["control"], "The baseline every other group is compared with"),
            ("Outcomes", ", ".join(ds["outcomes"]), "The first is the main outcome"),
            ("Revenue column", ds["revenue"] or "None", "Used for profit and break-even" if ds["revenue"] else "No profit calculation was made"),
            ("Pre-period column", ds["pre"] or "None", "Used for CUPED variance reduction" if ds["pre"] else "No pre-period adjustment"),
            ("Customer attributes", ", ".join(ds["features"]) or "None", "Used for the balance checks and for who responds")]
    if prof:
        rows += [("Gross margin", format(prof["margin"], ".0%"), "Share of each sale kept as profit before email costs"),
                 ("Cost per email", money(prof["cost"]), "Charged to every group except the control"),
                 ("Customers on list", format(int(prof["list_size"]), ","), "Profit totals are scaled to this list"),
                 ("Fixed campaign cost", money(prof["fixed"], 0), "Charged to every group except the control")]
    return table(["Item", "Value", "Meaning"], [[e(a), e(b), e(c)] for a, b, c in rows], item_first=True)


def report_html(run, user, download=False):
    import re
    app_css = re.sub(r'@font-face\{[^}]*url\("/static/fonts/[^}]*\}\n?', "", CSS_PATH.read_text())   # the report embeds its own fonts; no outside files
    css = (Path(__file__).parent / "static" / "header.css").read_text() + app_css + font_face_css()
    kind = run["kind"]
    plan, data = kind == "plan", kind == "data"
    parts = (render_data_result(run["result"], report=True) if data else
             render_plan_result(run["result"], report=True) if plan else render_score_result(run["result"], report=True))
    ref = f"EIS-{'P' if plan else 'D' if data else 'S'}-{int(run['id']):05d}"
    rtype = "Experiment Analysis Report" if data else "Campaign Profit Report" if plan else "Customer Targeting Report"
    logo = _logo_uri()
    print_btn = "" if download else '<div class="noprint actions"><button type="button" class="btn" id="printBtn">Print or save as PDF</button></div>'
    script = "" if download else '<script src="/static/print.js"></script>'
    method = table(["Item", "What was done", "Why it matters"], [[e(a), e(b), e(c)] for a, b, c in [
        ("Source", "Hillstrom MineThatData email test (2008): 64,000 customers randomly given no email (21,306), a men's email (21,307) or a women's email (21,387)",
         "Random assignment makes group differences a fair estimate of the email's effect"),
        ("Outcomes", "Site visits, purchases and spend in the two weeks after the email", "Spend is short-run; long-term value is not measured"),
        ("Profit", "Profit per customer = gross margin x expected spend - cost per email x share emailed; fixed campaign cost is charged to policies that send email",
         "Puts every policy on one dollar scale so they can be compared"),
        ("Uncertainty", "500 bootstrap resamples of the test data (planner), 200 model refits (scorer); intervals are the middle 95%",
         "Resampling shows how much each estimate could move by chance"),
        ("Policies" if plan else "Model", "Send nothing, men's email to everyone, women's email to everyone, men's email only to customers who bought both lines" if plan else
         "Models fitted to the randomized test estimate this customer's visit chance and two-week revenue under each email",
         "These are the choices a marketer can actually make" if plan else "The gain is the difference from the no-email estimate for the same customer"),
        ("Limits", "One past campaign by one retailer", "Other products, seasons or customer lists can behave differently")]], item_first=True)
    if data:
        ds, rd = run["result"]["dataset"], run["result"]
        method = table(["Item", "What was done", "Why it matters"], [[e(a), e(b), e(c)] for a, b, c in [
            ("Source", f"The uploaded file '{ds['name']}': {ds['rows_used']:,} customers in {len(rd['groups'])} groups", "Results describe this test only"),
            ("Comparison", f"Each group is compared with the control group, '{ds['control']}', by the difference in average outcome (or rate, for yes/no outcomes)",
             "Randomized groups differ only by what they received, so the difference is the effect"),
            ("Intervals", "95% intervals use the normal approximation for a difference of two means or proportions; p-values are adjusted for the number of groups compared (Bonferroni)",
             "Keeps a chance result from looking clear when several groups are compared"),
            ("Pre-period adjustment", f"Outcomes were adjusted with CUPED using '{ds['pre']}': each outcome minus theta times the pre-period value, where theta is the covariance divided by the variance" if ds["pre"]
             else "None", "Removes variation that was already known before the test" if ds["pre"] else "No pre-period column was chosen"),
            ("Who responds", "Each attribute level (numeric attributes in quartiles) is compared the same way, with the adjustment counting every comparison",
             "Shows who responds, but segment results are exploratory"),
            ("Profit", "Profit per customer = gross margin x average revenue - cost per email (every group except the control) - fixed cost / list size" if rd["profit"] else "Not calculated",
             "Puts every group on one dollar scale" if rd["profit"] else "No revenue column, margin or cost was supplied"),
            ("Limits", "One test; causal only if customers were assigned at random", "Other products, seasons or customer lists can behave differently")]], item_first=True)
    glossary = table(["Term", "Meaning"], [[e(a), e(b)] for a, b in (REPORT_GLOSSARY + (DATA_GLOSSARY if data else []))], item_first=True)
    if data:
        res_html = "".join(f"<h3>{e(col)}</h3>{t}{cu}{ch}" for col, t, ch, cu in parts["outcomes"]) + parts["guide"]
        sections = [("Executive summary", f'<div class="decision {"good" if parts["ok"] else "warn"}"><b>Decision:</b> {e(parts["verdict"])}</div>' + parts["summary"]),
                    ("Dataset and setup", _data_setup(run)), ("Data quality checks", parts["checks"]), ("Results by outcome", res_html)]
        if parts["segments_table"]:
            sections.append(("Who responds", parts["segments_table"] + parts["segments_chart"]))
        if parts["profit_table"]:
            sections.append(("Profit and break-even", parts["profit_table"] + parts["profit_chart"]))
        sections.append(("Notes and limitations", parts["notes"]))
    elif plan:
        decision = parts["decision"]
        sections = [("Executive summary", f'<div class="decision {"good" if parts["ok"] else "warn"}"><b>Decision:</b> {e(decision)}</div>' + parts["summary"]),
                    ("Inputs and assumptions", _report_inputs(run)), ("Policy comparison", parts["policy"]),
                    ("Profit vs cost per email", parts["chart"]), ("Notes and limitations", parts["notes"])]
    else:
        sections = [("Executive summary", f'<div class="decision {"good" if parts["good"] else "warn"}"><b>Decision:</b> {e(parts["verdict"])}</div>' + parts["summary"]),
                    ("Customer and assumptions", _report_inputs(run)), ("Results per customer", parts["arms"]),
                    ("Expected gain vs no email", parts["chart"])]
        if parts["segment"]:
            sections.append(("Similar customers in the test", parts["segment"]))
        sections.append(("Notes and limitations", parts["notes"]))
    sections += [("Methodology", method), ("Glossary", glossary)]
    body = "".join(f'<section class="rsec"><h2 class="sec"><span>{n}.</span> {e(t)}</h2>{h}</section>' for n, (t, h) in enumerate(sections, 1))
    n_sign = len(sections) + 1
    sign = table(["Role", "Name", "Signature", "Date"], [
        ["Prepared by", e(user["name"]), "Generated electronically", e(run["created_at"])],
        ["Reviewed by", "&nbsp;", "&nbsp;", "&nbsp;"], ["Approved by", "&nbsp;", "&nbsp;", "&nbsp;"]], item_first=True, cls="sign")
    control = (f'<table class="control"><tbody><tr><th>Reference</th><td>{ref}</td><th>Report type</th><td>{"Experiment analysis" if data else "Campaign planning" if plan else "Customer scoring"}</td></tr>'
               f'<tr><th>Prepared for</th><td>{e(user["name"])}</td><th>Date and time of run</th><td>{e(run["created_at"])}</td></tr>'
               f'<tr><th>Dataset</th><td>{e(run["result"]["dataset"]["name"]) + ", " + format(run["result"]["dataset"]["rows_used"], ",") + " customers" if data else "Hillstrom email test, 64,000 customers"}</td><th>Version</th><td>1.0, generated automatically</td></tr></tbody></table>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ref} {e(run['title'])} | Report</title><link rel="icon" type="image/png" href="{logo}"><style>{css}</style></head><body class="report">
<div class="paper"><header class="lh"><img class="lh-logo" src="{logo}" alt="" width="64" height="64"><div class="lh-name"><b>{APP_NAME}</b><span>Email campaign analytics</span></div>
<div class="lh-ref"><span>Reference</span><b>{ref}</b></div></header>
<div class="rtitle"><span class="eyebrow">{rtype}</span><h1>{e(run['title'])}</h1></div>{print_btn}
<section class="rsec"><h2 class="sec">Document control</h2>{control}</section>
{body}
<section class="rsec"><h2 class="sec"><span>{n_sign}.</span> Authorisation</h2>{sign}</section>
<footer class="rfoot"><span>{APP_NAME} | {ref} | Confidential</span><span>Source: {e("uploaded file " + run["result"]["dataset"]["name"]) if data else "Hillstrom email test, 64,000 customers, randomized"}. Estimates, not guarantees.</span></footer></div>
{script}</body></html>"""
