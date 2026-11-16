"""Draws the faint data-and-money background tile used behind the app (solid thin lines, no dashes or dots)."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static"


def coin(cx, cy, r, sym, size):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}"/><circle cx="{cx}" cy="{cy}" r="{r - 8}"/>'
            f'<text x="{cx}" y="{cy + size * 0.35:.0f}" font-size="{size}" text-anchor="middle" stroke="none" fill="currentColor" font-weight="700">{sym}</text>')


def tile(color, opacity):
    parts = []
    parts.append(coin(90, 90, 44, "$", 46))
    parts.append(coin(330, 78, 34, "&#8377;", 36))
    # bar chart
    parts.append('<g transform="translate(430 36)"><path d="M0 0V100H130"/><rect x="14" y="56" width="18" height="44"/><rect x="42" y="36" width="18" height="64"/>'
                 '<rect x="70" y="50" width="18" height="50"/><rect x="98" y="12" width="18" height="88"/></g>')
    # line chart with markers
    parts.append('<g transform="translate(50 215)"><path d="M0 0V110H210"/><polyline points="12,88 55,60 100,74 150,30 200,8"/>'
                 '<circle cx="12" cy="88" r="6"/><circle cx="55" cy="60" r="6"/><circle cx="100" cy="74" r="6"/><circle cx="150" cy="30" r="6"/><circle cx="200" cy="8" r="6"/></g>')
    # banknote
    parts.append('<g transform="translate(300 215) rotate(-10 70 38)"><rect width="140" height="76" rx="8"/><circle cx="70" cy="38" r="20"/>'
                 '<text x="70" y="47" font-size="26" text-anchor="middle" stroke="none" fill="currentColor" font-weight="700">$</text>'
                 '<path d="M14 18h22M104 58h22"/></g>')
    # pie chart
    parts.append('<g transform="translate(520 300)"><circle r="46"/><path d="M0 0V-46M0 0L40 23"/><path d="M0 0L-46 0A46 46 0 0 1 0 -46Z"/></g>')
    # coin stack
    parts.append('<g transform="translate(70 440)"><ellipse cx="50" cy="70" rx="46" ry="14"/><path d="M4 70v-16M96 70v-16"/><ellipse cx="50" cy="54" rx="46" ry="14"/>'
                 '<path d="M4 54v-16M96 54v-16"/><ellipse cx="50" cy="38" rx="46" ry="14"/></g>')
    # envelope (the campaign email)
    parts.append('<g transform="translate(250 420)"><rect width="104" height="68" rx="6"/><polyline points="4,6 52,44 100,6"/></g>')
    parts.append(coin(500, 450, 38, "&#8364;", 40))
    parts.append('<text x="40" y="585" font-size="56" stroke="none" fill="currentColor" font-weight="700">%</text>')
    parts.append(coin(285, 560, 30, "&#163;", 32))
    # rising bars with arrow
    parts.append('<g transform="translate(410 520)"><path d="M0 70H120"/><rect x="8" y="46" width="16" height="24"/><rect x="36" y="32" width="16" height="38"/>'
                 '<rect x="64" y="18" width="16" height="52"/><path d="M92 40L112 6M100 6H112V18"/></g>')
    parts.append('<path d="M175 160v18M166 169h18M560 190v18M551 199h18M200 380v16M192 388h16" />')
    body = "".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">'
            f'<g fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" color="{color}" opacity="{opacity}" '
            f'font-family="Calibri, Carlito, Arial, sans-serif">{body}</g></svg>')


if __name__ == "__main__":
    (OUT / "bg-light.svg").write_text(tile("#6d4fd6", 0.15))
    (OUT / "bg-dark.svg").write_text(tile("#a78bfa", 0.17))
    print("wrote bg-light.svg, bg-dark.svg")
