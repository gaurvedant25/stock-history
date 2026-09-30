#!/usr/bin/env python3
"""Turn history.json into a self-contained SVG price chart with numbered pins.

    python render_chart.py history.json --out chart.svg

The pins are the point of the chart: each numbered marker sits on the day of a
notable move, so the reader's eye goes from a spike straight to the numbered
explanation underneath. The script prints the pin legend to stdout -- write
your explanations against those numbers so the two line up.

The SVG carries its own light/dark styling, so it renders correctly inline in
chat, inside an artifact, or opened as a bare file.
"""

import argparse
import json
import math
from datetime import datetime

W, H = 820, 370
L, R, T, B = 62, 18, 26, 46          # plot margins
PW, PH = W - L - R, H - T - B

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

STYLE = """
  .sh-bg    { fill: var(--sh-bg, transparent); }
  .sh-grid  { stroke: var(--sh-grid, #e2e4e9); stroke-width: 1; }
  .sh-axis  { fill: var(--sh-muted, #6b7280); font-size: 11px;
              font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
  .sh-line  { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .sh-up    { stroke: var(--sh-up, #14894f); }
  .sh-down  { stroke: var(--sh-down, #c0392b); }
  .sh-fill-up   { fill: var(--sh-up, #14894f); opacity: 0.09; }
  .sh-fill-down { fill: var(--sh-down, #c0392b); opacity: 0.09; }
  .sh-pin-up    { fill: var(--sh-up, #14894f); }
  .sh-pin-down  { fill: var(--sh-down, #c0392b); }
  .sh-pin-txt   { fill: #fff; font-size: 11px; font-weight: 500; text-anchor: middle;
                  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
  .sh-stem  { stroke: var(--sh-muted, #6b7280); stroke-width: 1; stroke-dasharray: 2 2; opacity: .5; }
  .sh-title { fill: var(--sh-text, #111827); font-size: 13px; font-weight: 600;
              font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
  @media (prefers-color-scheme: dark) {
    .sh-grid  { stroke: #2f3540; }
    .sh-axis  { fill: #9aa3b2; }
    .sh-title { fill: #e8eaed; }
    .sh-line.sh-up   { stroke: #34d17f; }
    .sh-line.sh-down { stroke: #ff6b5e; }
    .sh-fill-up   { fill: #34d17f; }
    .sh-fill-down { fill: #ff6b5e; }
    .sh-pin-up    { fill: #34d17f; }
    .sh-pin-down  { fill: #ff6b5e; }
    .sh-pin-txt   { fill: #10151c; }
  }
"""


def money(value, currency):
    sign = SYMBOLS.get(currency, "")
    if value >= 1000:
        return sign + format(value, ",.0f")
    return sign + format(value, ",.2f")


def nice_ticks(lo, hi, count=4):
    """Round gridline values so the axis reads 1,250 / 1,300 rather than 1,247.32.

    Picks the round step that lands closest to the target tick count rather than
    the first step above it -- a stock trading in a narrow band otherwise ends up
    with two gridlines and no sense of scale.
    """
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    exp = int(math.floor(math.log10(raw))) if raw > 0 else 0
    candidates = [m * (10 ** e) for e in (exp - 1, exp, exp + 1) for m in (1, 2, 2.5, 5)]

    def ticks_for(step):
        out, v = [], math.floor(lo / step) * step
        while v <= hi + step:
            if lo <= v <= hi:
                out.append(round(v, 2))
            v += step
        return out

    # Score the ticks that actually land inside the range, not span/step: a step
    # can divide the span neatly and still strand most of its lines outside it.
    best = min(candidates, key=lambda s: (abs(len(ticks_for(s)) - count), -s))
    return ticks_for(best) or [round(lo, 2), round(hi, 2)]


def build(data, title=None):
    series = data["series"]
    meta, stats = data["meta"], data["stats"]
    currency = meta.get("currency") or ""
    closes = [r["close"] for r in series]
    lo, hi = min(closes), max(closes)
    pad = (hi - lo) * 0.12 or (hi * 0.02 or 1)
    ylo, yhi = lo - pad, hi + pad

    n = len(series)
    x = lambda i: L + (PW * i / (n - 1) if n > 1 else PW / 2)
    y = lambda v: T + PH - (v - ylo) / (yhi - ylo) * PH

    rising = stats["change_pct"] >= 0
    tone = "sh-up" if rising else "sh-down"
    summary = "%s closing price, %s to %s, %s%% over the period" % (
        meta.get("symbol", "stock"), stats["start_date"], stats["end_date"],
        format(stats["change_pct"], "+"))
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="100%%" role="img">' % (W, H)]
    parts.append("<title>%s price history</title>" % esc(meta.get("symbol", "stock")))
    parts.append("<desc>%s. Numbered markers flag the largest single-day moves.</desc>"
                 % esc(summary))
    parts.append("<style>%s</style>" % STYLE)
    parts.append('<rect class="sh-bg" x="0" y="0" width="%d" height="%d"/>' % (W, H))

    if title:
        parts.append('<text class="sh-title" x="%d" y="16">%s</text>' % (L, esc(title)))

    # horizontal gridlines with price labels
    for tick in nice_ticks(ylo, yhi):
        ty = y(tick)
        if not (T - 2 <= ty <= T + PH + 2):
            continue
        parts.append('<line class="sh-grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                     % (L, ty, L + PW, ty))
        parts.append('<text class="sh-axis" x="%d" y="%.1f" text-anchor="end">%s</text>'
                     % (L - 8, ty + 4, esc(money(tick, currency))))

    # month labels along the bottom
    seen = set()
    for i, row in enumerate(series):
        month = row["date"][:7]
        if month in seen:
            continue
        seen.add(month)
        label = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%b")
        parts.append('<text class="sh-axis" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                     % (x(i), T + PH + 20, label))

    pts = " ".join("%.1f,%.1f" % (x(i), y(c)) for i, c in enumerate(closes))
    parts.append('<polygon class="sh-fill-%s" points="%.1f,%.1f %s %.1f,%.1f"/>'
                 % ("up" if rising else "down", L, T + PH, pts, L + PW, T + PH))
    parts.append('<polyline class="sh-line %s" points="%s"/>' % (tone, pts))

    # numbered pins, staggered so neighbouring markers do not collide
    index_of = {row["date"]: i for i, row in enumerate(series)}
    legend, last_x, flip = [], -999, False
    for num, move in enumerate(data.get("moves", []), start=1):
        i = index_of.get(move["date"])
        if i is None:
            continue
        px, py = x(i), y(series[i]["close"])
        up = move["change_pct"] >= 0
        flip = not flip if px - last_x < 54 else False
        last_x = px
        offset = 22 + (18 if flip else 0)
        cy = py - offset if up else py + offset
        cy = min(max(cy, T + 12), T + PH - 12)
        parts.append('<line class="sh-stem" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                     % (px, py, px, cy))
        parts.append('<circle class="sh-pin-%s" cx="%.1f" cy="%.1f" r="9.5"/>'
                     % ("up" if up else "down", px, cy))
        parts.append('<text class="sh-pin-txt" x="%.1f" y="%.1f">%d</text>'
                     % (px, cy + 3.7, num))
        legend.append((num, move["date"], move["change_pct"], move.get("likely")))

    parts.append("</svg>")
    return "\n".join(parts), legend


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("history", help="JSON produced by fetch_history.py")
    ap.add_argument("--out", help="write the SVG here (default: stdout)")
    ap.add_argument("--title", help="caption drawn at the top of the chart")
    args = ap.parse_args()

    with open(args.history, encoding="utf-8") as fh:
        data = json.load(fh)

    default_title = None
    if not args.title:
        m, s = data["meta"], data["stats"]
        default_title = "%s  %s  %s%% over %d months" % (
            m.get("symbol"), money(s["end_close"], m.get("currency") or ""),
            format(s["change_pct"], "+"), m.get("months", 3))
    svg, legend = build(data, args.title or default_title)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print("wrote " + args.out)
    else:
        print(svg)

    if legend:
        print("\nPin legend -- number your explanations to match:")
        for num, date, change, kind in legend:
            print("  %d  %s  %+.2f%%  (%s)" % (num, date, change, kind))


if __name__ == "__main__":
    main()
