"""Deterministic SVG charts for the quote report. Pure Python, no plotting
library: the same calculation archive always gives byte-identical charts.

    loss_distribution_svg(calc)   annual-loss histogram with EL / P90 / P99.5 markers
    tornado_svg(calc)             premium swing per uncertain parameter
    controls_svg(calc)            premium under each change that would lower it
"""
from __future__ import annotations

from html import escape

FONT = "font-family='-apple-system, Segoe UI, Helvetica, Arial, sans-serif'"
INK, MUTED, GRID = "#1f2328", "#59636e", "#d8dee4"
BLUE, ORANGE, GREEN, RED = "#2f6fdf", "#d97a1f", "#1f8a5b", "#c93c37"


def money(x: float) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:.2f}M"
    if abs(x) >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:,.0f}"


def _svg(w: int, h: int, body: list[str], title: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {w} {h}' width='100%' "
            f"role='img' aria-label='{escape(title)}' {FONT}>"
            f"<rect width='{w}' height='{h}' fill='#ffffff'/>" + "".join(body) + "</svg>")


def _text(x, y, s, size=12, fill=INK, anchor="start", weight="normal"):
    return (f"<text x='{x:.1f}' y='{y:.1f}' font-size='{size}' fill='{fill}' text-anchor='{anchor}' "
            f"font-weight='{weight}'>{escape(str(s))}</text>")


LABELS = {"severity.body.median": "claim size (median)", "severity.body.p90": "claim size (P90)",
          "severity.tail.prob": "large-claim share", "severity.tail.alpha": "large-claim tail shape",
          "frequency.p_fail": "red-team failure share", "frequency.p_harm_given_failure": "P(harm | failure)",
          "frequency.p_claim_given_harm": "P(claim | harm)", "frequency.adversarial_to_live": "adversarial-to-live ratio",
          "frequency.rate": "claim rate", "annual_prob": "event probability",
          "share_affected": "share of insureds hit", "loss_share_of_limit": "loss per hit (share of limit)"}


def _short(path: str) -> str:
    """Plain-English label for a parameter path, e.g. 'data leakage: claim size (P90)'."""
    parts = path.split(".")
    if parts[0] == "systemic":
        return "systemic: " + LABELS.get(parts[1], parts[1].replace("_", " "))
    peril, rest = parts[1], ".".join(parts[2:])
    return f"{peril.replace('_', ' ')}: {LABELS.get(rest, rest.replace('_', ' '))}"


# ---------------------------------------------------------------- loss distribution

def loss_distribution_svg(calc: dict) -> str:
    ld = calc["loss_distribution"]
    h = ld["histogram"]
    W, H, L, R, T, B = 720, 330, 64, 20, 36, 64
    pw, ph = W - L - R, H - T - B
    n, edges, counts = h["n_years"], h["edges"], h["counts"]
    agg = edges[-1]
    shares = [c / n for c in counts]
    zero = h["zero_years"] / n
    top = max(shares) if shares else 1.0
    top = top * 1.15 or 1.0
    bw = pw / len(counts)
    x = lambda v: L + pw * v / agg
    y = lambda s: T + ph * (1 - s / top)

    body = [_text(L, 22, "Annual loss to the policy (simulated years)", 14, weight="600")]
    for i in range(5):
        s = top * i / 4
        body.append(f"<line x1='{L}' x2='{W - R}' y1='{y(s):.1f}' y2='{y(s):.1f}' stroke='{GRID}'/>")
        body.append(_text(L - 6, y(s) + 4, f"{s:.1%}", 10, MUTED, "end"))
    for i, s in enumerate(shares):
        if s > 0:
            body.append(f"<rect x='{L + i * bw + 0.5:.1f}' y='{y(s):.1f}' width='{bw - 1:.1f}' "
                        f"height='{T + ph - y(s):.1f}' fill='{BLUE}' opacity='0.85'/>")
    for v in (0, agg / 4, agg / 2, 3 * agg / 4, agg):
        body.append(_text(x(v), T + ph + 16, money(v), 10, MUTED, "middle"))
    body.append(_text(L + pw / 2, H - 10, "Annual loss after deductible and limits "
                      f"(share of years with no loss: {zero:.1%})", 11, MUTED, "middle"))

    per_claim = calc["policy"]["limit_per_claim"]
    if per_claim < agg:
        xp = x(per_claim)
        body.append(f"<line x1='{xp:.1f}' x2='{xp:.1f}' y1='{T}' y2='{T + ph}' stroke='{MUTED}' "
                    f"stroke-dasharray='2 3'/>")
        body.append(_text(xp, T + ph + 29, "per-claim limit", 9, MUTED, "middle"))
    body.append(_text(W - R, T + ph + 29, "aggregate limit", 9, MUTED, "end"))

    p = ld["percentiles"]
    marks = [("Expected", ld["expected_loss"], ORANGE), ("P90", p["90"], INK), ("P99.5", p["99.5"], RED)]
    for k, (label, v, color) in enumerate(marks):
        xv = min(x(v), W - R - 1)
        body.append(f"<line x1='{xv:.1f}' x2='{xv:.1f}' y1='{T}' y2='{T + ph}' stroke='{color}' "
                    f"stroke-width='2' stroke-dasharray='5 3'/>")
        anchor = "end" if xv > W - R - 120 else "start"
        dx = -4 if anchor == "end" else 4
        body.append(_text(xv + dx, T + 12 + 14 * k, f"{label} {money(v)}", 11, color, anchor, "600"))
    return _svg(W, H, body, "Annual loss distribution")


# ---------------------------------------------------------------- tornado

def tornado_svg(calc: dict, top_n: int = 8) -> str:
    t = calc["tornado"]
    bars = t["bars"][:top_n]
    base = t["base_premium"]
    W, L, R, T, row = 720, 250, 70, 48, 30
    H = T + row * len(bars) + 40
    lo = min([base] + [min(b["premium_at_low"], b["premium_at_high"]) for b in bars])
    hi = max([base] + [max(b["premium_at_low"], b["premium_at_high"]) for b in bars])
    pad = (hi - lo) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    pw = W - L - R
    x = lambda v: L + pw * (v - lo) / (hi - lo)

    body = [_text(16, 22, "What moves the premium most (each parameter at its P10 and P90)", 14, weight="600"),
            _text(16, 38, f"Others held at their medians; point premium {money(base)} (excludes uncertainty load)",
                  11, MUTED)]
    for i, b in enumerate(bars):
        yy = T + i * row
        a, c = sorted((b["premium_at_low"], b["premium_at_high"]))
        body.append(_text(L - 8, yy + 19, _short(b["parameter"]), 11, INK, "end"))
        # Low-percentile value in green, high in red, split at the base premium.
        for v, color in ((b["premium_at_low"], GREEN), (b["premium_at_high"], RED)):
            x0, x1 = sorted((x(base), x(v)))
            body.append(f"<rect x='{x0:.1f}' y='{yy + 6}' width='{max(x1 - x0, 1):.1f}' height='{row - 12}' "
                        f"fill='{color}' opacity='0.8'/>")
        body.append(_text(x(c) + 4, yy + 19, money(b["swing"]), 10, MUTED))
    body.append(f"<line x1='{x(base):.1f}' x2='{x(base):.1f}' y1='{T}' y2='{T + row * len(bars)}' "
                f"stroke='{INK}' stroke-width='1.5'/>")
    yb = T + row * len(bars) + 16
    for v in (lo + pad, base, hi - pad):
        body.append(_text(x(v), yb, money(v), 10, MUTED, "middle"))
    body.append(_text(L, yb + 18, "■ parameter at P10", 10, GREEN))
    body.append(_text(L + 130, yb + 18, "■ parameter at P90", 10, RED))
    return _svg(W, H, body, "Tornado chart")


# ---------------------------------------------------------------- controls

def controls_svg(calc: dict, top_n: int = 8) -> str:
    c = calc["controls"]
    opts = c["options"][:top_n]
    base = c["base_premium"]
    W, L, R, T, row = 720, 300, 90, 48, 30
    H = T + row * (len(opts) + 1) + 24
    pw = W - L - R
    x = lambda v: L + pw * v / base

    def label(o):
        if o["kind"] == "red_team_remediation":
            return "Fix red-team failures, re-test"
        return f"{o['factor'].replace('_', ' ')}: {o['to_level'].replace('_', ' ')}"

    body = [_text(16, 22, "Premium after each change", 14, weight="600"),
            _text(16, 38, "Re-priced with the same simulation, so only the change moves the price", 11, MUTED)]
    pct = lambda o: "<1%" if o["saving_pct"] < 0.005 else f"−{o['saving_pct']:.0%}"
    rows = [("Current", base, INK, "")] + [(label(o), o["premium"], GREEN, pct(o)) for o in opts]
    for i, (name, v, color, note) in enumerate(rows):
        yy = T + i * row
        body.append(_text(L - 8, yy + 19, name, 11, INK, "end", "600" if i == 0 else "normal"))
        body.append(f"<rect x='{L}' y='{yy + 6}' width='{max(x(v) - L, 1):.1f}' height='{row - 12}' "
                    f"fill='{color}' opacity='{0.55 if i == 0 else 0.8}'/>")
        body.append(_text(x(v) + 4, yy + 19, f"{money(v)} {note}".strip(), 10, MUTED))
    return _svg(W, H, body, "Controls that lower the premium")
