#!/usr/bin/env python3
"""Build one searchable page covering several stocks.

    python build_portfolio.py portfolios/pms --out portfolio.html

The folder holds a portfolio.json manifest and one sub-folder per stock:

    portfolios/pms/
      portfolio.json          {"title": "PMS Holdings History",
                               "stocks": [{"dir": "NYKAA", "isin": "INE388Y01029",
                                           "name": "FSN E-Commerce (Nykaa)", "weight": 5.03}]}
      NYKAA/history.json      from fetch_history.py
      NYKAA/fundamentals.json from fetch_fundamentals.py
      NYKAA/research.json     your write-up (below)

research.json is the per-stock writing, the same research as a single report:

    {"pins": {"2026-07-20": {"title": "...", "detail": "...", "source": "...", "source_name": "..."}},
     "no_catalyst": ["2026-09-18"],
     "verdicts": {"1": "...", "3": "...", "6": "...", "12": "..."},
     "things": [{"heading": "...", "body": "..."}],
     "financial_points": {"quarterly": [...], "annual": [...], "shareholding": [...]},
     "caveats": [...], "sources": [{"name": "...", "url": "..."}]}

Adding a stock later is: a new folder with those three files, one more entry in
portfolio.json, and a rebuild. The page is a single self-contained file.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_history as fh  # noqa: E402
import fetch_filings  # noqa: E402

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LABEL = {"1": "1 month", "3": "3 months", "6": "6 months", "12": "1 year"}
MINUS = "−"
CHART_W, CHART_H = 636, 250


def short(iso, year=None):
    y, m, d = iso.split("-")
    return MON[int(m) - 1] + " " + str(int(d)) + ("" if y == year else ", " + y)


def nice_step(rng):
    for s in [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]:
        if rng / s <= 4.5:
            return s
    return 10000


def group(v, locale):
    """Thousands separators; Indian grouping (1,27,753) for en-IN."""
    neg, s = v < 0, str(int(round(abs(v))))
    if locale == "en-IN" and len(s) > 3:
        head, parts = s[:-3], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + parts + [s[-3:]])
    else:
        s = format(int(s), ",")
    return (MINUS if neg else "") + s


def chart(S, moves, win, cur, locale):
    rows = [dict(r) for r in S if r["date"] >= fh.months_before(S[-1]["date"], win)]
    last = next((r["bench"] for r in rows if r["bench"] is not None), None)
    for r in rows:  # carry the index across days it did not trade
        last = r["bench"] = r["bench"] if r["bench"] is not None else last
    c0, b0 = rows[0]["close"], rows[0]["bench"] or 1
    closes = [r["close"] for r in rows]
    bench = [c0 * (r["bench"] or b0) / b0 for r in rows]
    lo, hi = min(closes + bench), max(closes + bench)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    n = len(rows)
    X = lambda i: round(i / max(n - 1, 1) * CHART_W, 1)
    Y = lambda v: round(14 + (hi - v) / (hi - lo) * (CHART_H - 28), 1)
    line = "M" + " L".join("%s,%s" % (X(i), Y(v)) for i, v in enumerate(closes))
    step = nice_step(hi - lo)
    grid, t = [], math.ceil(lo / step) * step
    while t <= hi:
        grid.append({"top": Y(t), "label": cur + group(t, locale)})
        t += step
    last_year = rows[-1]["date"][:4]
    xl = [{"left": X(round(k * (n - 1) / 3)), "label": short(rows[round(k * (n - 1) / 3)]["date"], "x")} for k in range(4)]
    idx = {r["date"]: i for i, r in enumerate(rows)}
    hiR, loR = max(rows, key=lambda r: r["close"]), min(rows, key=lambda r: r["close"])
    chg = (closes[-1] / c0 - 1) * 100
    bchg = (rows[-1]["bench"] / b0 - 1) * 100 if rows[-1]["bench"] else 0.0
    return {
        "label": LABEL[str(win)],
        # Where this window starts in the full series, and its price scale, so
        # the page can map a click on the chart back to a day and draw a marker.
        "start": len(S) - n, "lo": round(lo, 4), "hi": round(hi, 4),
        "line": line,
        "bench": "M" + " L".join("%s,%s" % (X(i), Y(v)) for i, v in enumerate(bench)),
        "area": line + " L%s,%s L0,%s Z" % (CHART_W, CHART_H, CHART_H),
        "grid": grid, "xl": xl,
        "pins": [{"n": k, "x": X(idx[m["date"]]), "y": Y(closes[idx[m["date"]]])}
                 for k, m in enumerate(moves, 1) if m["date"] in idx],
        "stats": {"chg": round(chg, 2), "abs": round(closes[-1] - c0, 2), "hi": hiR["close"],
                  "hiD": short(hiR["date"], last_year), "lo": loR["close"], "loD": short(loR["date"], last_year),
                  "bench": round(bchg, 2), "vs": round(chg - bchg, 2)},
        "moves": [{"date": m["date"], "label": short(m["date"], last_year), "chg": m["change_pct"],
                   "bench": m["benchmark_pct"], "vol": m["volume_vs_avg"]} for m in moves if m["date"] in idx],
    }


# --- financial tables: a few key rows, not the whole statement --------------

KEY_ROWS = [  # (shown as, candidate labels in fetch_fundamentals output, kind)
    ("Revenue", ["Sales", "Revenue"], "money"),
    ("Revenue growth", ["Sales growth YoY %", "Revenue growth YoY %"], "growth"),
    ("Operating margin", ["OPM %", "Financing Margin %"], "pct0"),
    ("Net profit", ["Net Profit", "Net income"], "money"),
    ("Profit growth", ["Net Profit growth YoY %", "Net income growth YoY %"], "growth"),
    ("Gross NPA", ["Gross NPA %"], "pct2"),
]


def fmt(v, kind, locale, money_dec):
    if v is None:
        return "—"
    if kind == "growth":
        return ("+" if v >= 0 else MINUS) + "%.1f%%" % abs(v)
    if kind == "pct0":
        return ("" if v >= 0 else MINUS) + "%d%%" % round(abs(v))
    if kind == "pct2":
        return "%.2f%%" % v
    if money_dec:
        s = format(abs(v), ",.1f")
        return (MINUS if v < 0 else "") + s
    return group(v, locale)


def key_table(tab, keep, locale, points, drop_ttm=True):
    if not tab:
        return None
    cols = list(tab["columns"])
    idx = [i for i, c in enumerate(cols) if not (drop_ttm and c == "TTM")][-keep:]
    by_label = {r["label"]: r for r in tab["rows"]}
    money_dec = any(r["kind"] == "money" and any(v is not None and v % 1 for v in r["values"]) for r in tab["rows"])
    rows = []
    for shown, cands, kind in KEY_ROWS:
        src = next((by_label[c] for c in cands if c in by_label), None)
        if src and any(src["values"][i] is not None for i in idx):
            rows.append([shown, [fmt(src["values"][i], kind, locale, money_dec) for i in idx], 1 if kind == "growth" else 0])
    if not rows:
        return None
    return {"cols": [cols[i] for i in idx], "rows": rows, "points": points or [],
            "foot": " ".join(tab.get("notes", [])) + " Growth is year on year."}


def pts(d):
    return "no change" if abs(d) < 0.05 else ("+" if d > 0 else MINUS) + "%.1f pts" % abs(d)


def people(v):
    if v is None:
        return "—"
    if v >= 1e7:
        return "%.1f crore" % (v / 1e7)
    if v >= 1e5:
        return "%.1f lakh" % (v / 1e5)
    return group(v, "en-IN")


def ownership(sh, points):
    if not sh:
        return None
    cols, rows = sh["columns"], {r["label"]: r["values"] for r in sh["rows"]}
    if len(cols) == 1:  # a US-style snapshot from Yahoo
        bars = [{"label": r["label"].replace(" %", ""), "w": "%s%%" % r["values"][0], "text": "%.1f%%" % r["values"][0], "sub": ""}
                for r in sh["rows"] if r["kind"] == "pct" and r["values"][0] is not None]
        sections = [{"kind": "bars", "title": "Who holds the shares", "meta": "Latest filings", "bars": bars}]
        if sh.get("holders"):
            sections.append({"kind": "list", "title": "Largest holders", "meta": sh["holders"][0].get("as_of") or "",
                             "items": [{"name": h["name"], "value": "%.2f%%" % h["pct"]} for h in sh["holders"][:5]]})
        return {"sections": sections, "foot": " ".join(sh.get("notes", [])), "points": points or []}
    last, back = len(cols) - 1, max(0, len(cols) - 5)
    names = [("Promoters", "Promoters"), ("FIIs", "Foreign funds (FIIs)"), ("DIIs", "Indian funds (DIIs)")]
    bars = []
    for key, shown in names:
        v = rows.get(key)
        if v and v[last] is not None:
            d = v[last] - (v[back] or 0)
            bars.append({"label": shown, "w": "%.2f%%" % v[last], "text": "%.1f%%" % v[last], "sub": pts(d)})
    pub = [rows.get("Public"), rows.get("Government")]
    if pub[0]:
        now = sum((p[last] or 0) for p in pub if p)
        then = sum((p[back] or 0) for p in pub if p)
        bars.append({"label": "Retail and others", "w": "%.2f%%" % now, "text": "%.1f%%" % now, "sub": pts(now - then)})
    sections = [{"kind": "bars", "title": "Who owns the shares", "meta": cols[last] + " · change in a year", "bars": bars}]
    count = rows.get("No. of Shareholders")
    items = [{"name": "Promoter holding", "value": "None"}] if "Promoters" not in rows else []
    if count:
        items += [{"name": "Shareholders, " + cols[last], "value": people(count[last])},
                  {"name": "A year earlier", "value": people(count[back])}]
    if items:
        sections.append({"kind": "list", "title": "Shareholders", "meta": "Screener.in", "items": items})
    return {"sections": sections, "foot": " ".join(sh.get("notes", [])) + " Retail and others includes any government holding.",
            "points": points or []}


def shares(n):
    n = abs(n)
    return "%.2f crore shares" % (n / 1e7) if n >= 1e7 else "%.1f lakh shares" % (n / 1e5) if n >= 1e5 else "%s shares" % group(n, "en-IN")


def deal_line(ds):
    """One plain sentence on a day's NSE bulk and block deals, or ''."""
    if not ds:
        return ""
    parts = []
    churn = [d for d in ds if d["churn"]]
    if churn:
        names = " and ".join(d["client"].title() for d in churn[:2]) + (" and others" if len(churn) > 2 else "")
        vol = sum(d["share_of_day_volume"] or 0 for d in churn)
        parts.append("NSE bulk deal records show %s bought and sold equal amounts%s. That was trading, "
                     "so it accounts for volume, not the direction of the price."
                     % (names, ", about %d%% of the day's volume" % round(vol * 100) if 0 < vol <= 1 else ""))
    for d in [d for d in ds if not d["churn"]][:3]:
        parts.append("%s %s %s at about ₹%s (₹%s crore) in a %s deal."
                     % (d["client"].title(), "bought" if d["net"] > 0 else "sold", shares(d["net"]),
                        format(d["avg_price"], ",.2f"), format(d["value_cr"], ",.1f"), d["kind"]))
    return " ".join(parts)


def _calendar(folder):
    """Market-wide causes researched for the portfolio: {date: (text, url)}."""
    path = os.path.join(os.path.dirname(os.path.abspath(folder)), "market_calendar.md")
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"- (\d{4}-\d\d-\d\d): (.*)", line.strip())
            if m:
                text, url = m.group(2), ""
                u = re.search(r"\s*\((?:[^()]*?\s)?(https?://[^\s)]+)\)\s*$", text)
                if u:
                    url, text = u.group(1), text[:u.start()].rstrip()
                out[m.group(1)] = (text, url)
    return out


def _filing_text(r):
    text = re.sub(r"^.*?has (informed|submitted)( to)? the Exchange\s*(about|regarding|,)?\s*", "", r.get("text") or "", flags=re.I)
    text = text.strip(" .'\"") or r.get("category", "")
    when = ("%s, after the close" % r["time"]) if r.get("after_close") else r.get("time") or ""
    cat = r.get("category", "").strip()
    line = (cat + ": " if cat and cat.lower() not in text.lower() else "") + text[0].upper() + text[1:] if text else cat
    return ((when + " · ") if when else "") + line[:180]


def daily_data(folder, S, deals):
    """Per-day extras for the day picker: filings by the session they could move,
    deal summaries, and researched market-wide causes. Price, index and volume
    come from the series itself on the page."""
    fl = os.path.join(folder, "filings.json")
    filings = {}
    if os.path.exists(fl):
        for r in json.load(open(fl, encoding="utf-8")).get("filings", []):
            if r.get("signal") != "routine" and r.get("affects"):
                filings.setdefault(r["affects"], []).append(_filing_text(r))
    first = S[0]["date"]
    cal = {d: {"text": t, "url": u} for d, (t, u) in _calendar(folder).items() if d >= first}
    return {
        "d": [r["date"] for r in S], "c": [r["close"] for r in S],
        "v": [r["volume"] or 0 for r in S], "b": [r["bench"] for r in S],
        "filings": {d: v[:4] for d, v in filings.items()},
        "deals": {d: deal_line(v) for d, v in deals.items()},
        "market": cal,
        "typical": round(fh.typical_move(S), 2), "floor": round(fh.UNUSUAL_MULTIPLE * fh.typical_move(S), 2),
    }


# Multi-day swings: gradual moves that no single-day pin catches, like a 9%
# slide made of 1-2% days. A swing must span 3-20 sessions, move at least
# SWING_MULTIPLE normal days' worth (and 5%), and not be mostly one listed day.
SWING_MULTIPLE = 5.0
SWING_MIN_PCT = 5.0
SWING_SPAN = (3, 20)
SWINGS_PER_WINDOW = {"1": 1, "3": 2, "6": 3, "12": 4}
# The largest move against the swing's direction inside it, as a share of the
# swing, above which the stretch reads as a round trip rather than a slide.
SWING_MAX_COUNTER = 0.35
# make_swing_brief.py turns this on to see the swings that still need research.
KEEP_ALL_SWINGS = False


def counter_move(rows, ch):
    """Biggest % move against the direction of `ch` between two days in rows."""
    worst, ext = 0.0, rows[0]["close"]
    for r in rows[1:]:
        c = r["close"]
        worst = max(worst, (c / ext - 1) * 100 if ch < 0 else (1 - c / ext) * 100)
        ext = min(ext, c) if ch < 0 else max(ext, c)
    return worst


def results_sessions(folder):
    """Sessions that could react to a results filing, from filings.json."""
    fl = os.path.join(folder, "filings.json")
    out = set()
    if os.path.exists(fl):
        for r in json.load(open(fl, encoding="utf-8")).get("filings", []):
            # Only the results themselves: con-call notices, clarifications
            # and intimations mention "financial results" too.
            cat, t = r.get("category", "").lower(), (r.get("text") or "").lower()
            real = cat in ("outcome of board meeting", "press release", "integrated filing- financial")
            if r.get("affects") and real and "financial result" in cat + " " + t \
                    and not any(w in t for w in ("newspaper", "intimation", "conference call", "con. call")):
                out.add(r["affects"])
    return out


# Filing kinds that are paperwork rather than news, left out of a swing's list.
SWING_SKIP = ("con. call", "shareholders meeting", "esop", "record date", "allotment of securities",
              "monitoring agency", "trading window", "newspaper", "loss of share certificate")


def swing_filings(folder):
    """{session: [{"text", "url"}]} of the company's news-like filings."""
    fl = os.path.join(folder, "filings.json")
    out = {}
    if os.path.exists(fl):
        for r in json.load(open(fl, encoding="utf-8")).get("filings", []):
            cat = r.get("category", "").lower()
            high = r.get("signal") == "high" or fetch_filings.classify(r.get("category", ""), r.get("text") or "") == "high"
            if not high or not r.get("affects") or any(k in cat for k in SWING_SKIP):
                continue
            t = re.sub(r"^.*?has (informed|submitted)( to)? the Exchange\s*(about|regarding|,)?\s*", "",
                       r.get("text") or "", flags=re.I).strip(" .'\"") or r.get("category", "")
            t = t[0].upper() + t[1:]
            if len(t) > 90:
                t = t[:89].rsplit(" ", 1)[0] + "…"
            out.setdefault(r["affects"], []).append({"text": t, "url": r.get("url", "")})
    return out


def find_swings(S, W, w, typical, results, notes, cur, bench_name="the index", filings=None, known=()):
    start, rows = W["start"], S[W["start"]:]
    n, need = len(rows), max(SWING_MIN_PCT, SWING_MULTIPLE * typical)
    listed = {m["date"] for m in W["moves"]}
    cands = []
    for i in range(n):
        for j in range(i + SWING_SPAN[0], min(n, i + SWING_SPAN[1] + 1)):
            ch = (rows[j]["close"] / rows[i]["close"] - 1) * 100
            if abs(ch) >= need:
                cands.append((abs(ch), i, j, ch))
    cands.sort(reverse=True)
    # Swings already researched go first, so a data refresh that nudges the
    # window does not swap them for a near-identical span with a new key.
    pos = {r["date"]: k for k, r in enumerate(rows)}
    first = [(abs(ch), i, j, ch) for key in known for a, b in [key.split("/")]
             if a in pos and b in pos for i, j in [(pos[a], pos[b])]
             for ch in [(rows[j]["close"] / rows[i]["close"] - 1) * 100]]
    cands = sorted(first, reverse=True) + cands
    picked = []
    for _, i, j, ch in cands:
        if any(not (j <= p[0] or i >= p[1]) for p in picked):
            continue
        days = [(rows[k]["close"] / rows[k - 1]["close"] - 1) * 100 for k in range(i + 1, j + 1)]
        big = max(days, key=abs)
        if abs(big) >= 0.6 * abs(ch):
            continue  # one day did most of it, and that day has its own pin
        if counter_move(rows[i:j + 1], ch) > SWING_MAX_COUNTER * abs(ch):
            continue  # a fall, a rebound and a second fall is not one steady swing
        picked.append((i, j, ch, big, days.index(big) + i + 1))
        if len(picked) >= SWINGS_PER_WINDOW.get(w, 2):
            break
    year = S[-1]["date"][:4]
    out = []
    for i, j, ch, big, k in sorted(picked):
        a, b = rows[i], rows[j]
        bench = [r["bench"] for r in rows[i:j + 1] if r["bench"] is not None]
        bch = (bench[-1] / bench[0] - 1) * 100 if len(bench) > 1 else None
        verb = "Rose" if ch > 0 else "Fell"
        text = "%s %.1f%% over %d sessions, from %s%s on %s to %s%s on %s" % (
            verb, abs(ch), j - i, cur, format(a["close"], ",.2f"), short(a["date"], year),
            cur, format(b["close"], ",.2f"), short(b["date"], year))
        if bch is not None:
            text += ", while the %s %s %.1f%%" % (bench_name, "rose" if bch >= 0 else "fell", abs(bch))
            text += ". Much of it matched the market" if (bch > 0) == (ch > 0) and abs(bch) >= 0.5 * abs(ch) else ""
        text += ". The biggest single day inside was %s%.1f%% on %s%s." % (
            "+" if big >= 0 else MINUS, abs(big), short(rows[k]["date"], year),
            ", a listed big move" if rows[k]["date"] in listed else "")
        inside = [r["date"] for r in rows[i + 1:j + 1]]
        res = [d for d in inside if d in results]
        if res:
            text += " Results landed inside it (%s)." % ", ".join(short(d, year) for d in res)
        # Cite the biggest researched day that moved the same way as the swing.
        pos = {r["date"]: idx for idx, r in enumerate(rows)}
        move = lambda d: rows[pos[d]]["close"] / rows[pos[d] - 1]["close"] - 1
        known = sorted((d for d in inside if d in notes and (move(d) > 0) == (ch > 0)), key=lambda d: -abs(move(d)))
        if known:
            text += " Researched day inside: %s, %s." % (short(known[0], year), notes[known[0]]["title"])
        # Include the start day's session: news that day can set off the swing.
        fs = [dict(f, date=short(d, year)) for d in [a["date"]] + inside for f in (filings or {}).get(d, [])]
        out.append({"i0": start + i, "i1": start + j, "chg": round(ch, 2), "key": a["date"] + "/" + b["date"],
                    "label": "%s to %s" % (short(a["date"], year), short(b["date"], year)), "text": text,
                    "filings": fs[:4], "more": max(0, len(fs) - 4)})
    return out


def stock_data(folder, entry):
    h = json.load(open(os.path.join(folder, "history.json"), encoding="utf-8"))
    fpath, rpath = os.path.join(folder, "fundamentals.json"), os.path.join(folder, "research.json")
    F = json.load(open(fpath, encoding="utf-8")) if os.path.exists(fpath) else {}
    R = json.load(open(rpath, encoding="utf-8")) if os.path.exists(rpath) else {}
    meta, S = h["meta"], h["series"]
    inr = meta.get("currency") == "INR"
    cur, locale = ("₹", "en-IN") if inr else ("$" if meta.get("currency") == "USD" else "", "en-US")
    # Recompute rather than trust the stored lists, so a change to which days
    # count as big applies to every stock at the next build without a refetch.
    bw = fh.moves_by_window(S, meta.get("context_months", 12), meta.get("months", 3))
    verdicts = R.get("verdicts", {})
    fl = os.path.join(folder, "filings.json")
    deals = (json.load(open(fl, encoding="utf-8")).get("deals") or {}) if os.path.exists(fl) else {}
    win = {}
    for w in ("1", "3", "6", "12"):
        if w not in bw:
            continue
        # A recent listing has no history before its first day: the first
        # window reaching past it becomes "since listing", longer ones repeat it.
        # Yahoo's first trade date says whether the stock is a recent listing;
        # without it, allow a week of slack, since the one-year range can start
        # a day or two after the exact mark.
        want_from = datetime.date.fromisoformat(fh.months_before(S[-1]["date"], int(w)))
        listed = meta.get("first_trade_date")
        short_history = (datetime.date.fromisoformat(listed) > want_from if listed
                         else datetime.date.fromisoformat(S[0]["date"]) > want_from + datetime.timedelta(days=7))
        if short_history and any(v.get("chip") == "All" for v in win.values()):
            continue
        win[w] = chart(S, bw[w], int(w), cur, locale)
        win[w]["chip"] = "All" if short_history else ("1Y" if w == "12" else w + "M")
        if short_history:
            win[w]["label"] = "since listing on " + short(S[0]["date"], S[-1]["date"][:4])
        win[w]["verdict"] = verdicts.get(w, "")
        for mv in win[w]["moves"]:
            mv["deal"] = deal_line(deals.get(mv["date"]))
    pins = R.get("pins", {})
    notes = {d: {"title": p.get("title", ""), "detail": p.get("detail", ""), "src": p.get("source", ""),
                 "srcName": p.get("source_name", "")} for d, p in pins.items()}
    typical, results, sfil = fh.typical_move(S), results_sessions(folder), swing_filings(folder)
    # Researched causes for whole swings live in their own file, keyed "start/end".
    spath = os.path.join(folder, "swing_research.json")
    SR = json.load(open(spath, encoding="utf-8")) if os.path.exists(spath) else {}
    swing_notes, swing_blank = SR.get("swings", {}), set(SR.get("no_cause", []))
    listed_moves = {mv["date"]: mv for w in win.values() for mv in w["moves"]}
    for w, W in win.items():
        # Only days with a researched cause get a pin and a row. A big day with
        # no cause found is left to the day details and the coverage caveat.
        keep = [k for k, mv in enumerate(W["moves"]) if mv["date"] in pins]
        W["moves"] = [W["moves"][k] for k in keep]
        W["pins"] = [dict(W["pins"][k], n=n) for n, k in enumerate(keep, 1)]
        W["swings"] = find_swings(S, W, w, typical, results, notes, cur, (h.get("benchmark") or {}).get("name", "index"), sfil,
                                  set(swing_notes) | swing_blank)
        for sw in W["swings"]:
            n = swing_notes.get(sw["key"])
            if n:
                sw["note"] = {"title": n.get("title", ""), "detail": n.get("detail", ""),
                              "src": n.get("source", ""), "srcName": n.get("source_name", "")}
        # Like pins, a band is drawn only when its reason is known; searched
        # (swing_blank) and not-yet-researched stretches stay off the chart.
        if not KEEP_ALL_SWINGS:
            W["swings"] = [sw for sw in W["swings"] if sw.get("note")]
    fp = R.get("financial_points", {})
    fin = {}
    q = key_table(F.get("quarterly"), 5, locale, fp.get("quarterly"))
    a = key_table(F.get("annual"), 5, locale, fp.get("annual"))
    if q:
        fin["q"] = q
    if a:
        fin["a"] = a
    # The coverage caveat is computed, never written by hand, so it stays true
    # whenever the lists change (a new rule for which days count, a refetch).
    blank = [mv for d, mv in listed_moves.items() if d not in pins]
    coverage = []
    if blank:
        top = sorted(blank, key=lambda mv: -abs(mv["chg"]))[:3]
        coverage = ["%d unusually big %s no pin because news and exchange filings showed no cause; the largest: %s. "
                    "Pick %s on the chart to see %s price, volume and filings." % (
                        len(blank), "day has" if len(blank) == 1 else "days have", ", ".join(
                            "%s (%s%.1f%%%s)" % (mv["label"], "+" if mv["chg"] >= 0 else MINUS, abs(mv["chg"]),
                                                 " on %.0fx volume" % mv["vol"] if (mv["vol"] or 0) >= 2 else "") for mv in top),
                        "it" if len(blank) == 1 else "any of them", "its" if len(blank) == 1 else "their")]
    exch = {"NSI": "NSE", "BSE": "BSE", "NMS": "Nasdaq", "NYQ": "NYSE"}.get(meta.get("exchange"), meta.get("exchange", ""))
    sym = meta["symbol"].rsplit(".", 1)[0] if inr else meta["symbol"]
    last = S[-1]
    listed = {m["date"] for ms in bw.values() for m in ms}
    return {
        "id": sym, "isin": entry.get("isin", ""), "weight": entry.get("weight"),
        "meta": {"ticker": sym + " · " + exch, "symbol": sym, "name": entry.get("name") or meta.get("name") or sym,
                 "price": cur + format(last["close"], ",.2f"), "cur": cur, "locale": locale,
                 "bench": (h.get("benchmark") or {}).get("name", "Index"),
                 "asOf": "Prices from Yahoo Finance through " + short(last["date"]) + ", fetched " + meta.get("fetched_at", "")},
        "win": win, "notes": notes,
        "things": [{"h": t.get("heading", ""), "b": t.get("body", "")} for t in R.get("things", [])],
        "fin": fin, "own": ownership(F.get("shareholding"), fp.get("shareholding")),
        "caveats": coverage + R.get("caveats", []), "sources": R.get("sources", []),
        "daily": daily_data(folder, S, deals),
        "_check": {"unresearched": sorted(d for d in listed if d not in pins and d not in set(R.get("no_catalyst", []))),
                   "verdicts": sorted(set(win) - {k for k, v in verdicts.items() if v}),
                   "things": len(R.get("things", []))},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="portfolio folder holding portfolio.json")
    ap.add_argument("--out", default="portfolio.html")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(args.folder, "portfolio.json"), encoding="utf-8"))
    stocks, problems, pending = [], [], 0
    for entry in manifest["stocks"]:
        if not entry.get("dir"):
            pending += 1  # listed for later, not researched yet
            continue
        folder = os.path.join(args.folder, entry["dir"])
        if not os.path.exists(os.path.join(folder, "history.json")):
            problems.append("%s: no history.json yet, left off the page" % entry["dir"])
            continue
        s = stock_data(folder, entry)
        c = s.pop("_check")
        tag = "%-12s %d windows, %d notes" % (s["id"], len(s["win"]), len(s["notes"]))
        if c["unresearched"]:
            problems.append("%s: not researched yet: %s" % (s["id"], ", ".join(c["unresearched"])))
        if c["verdicts"]:
            problems.append("%s: missing verdicts for windows %s" % (s["id"], ", ".join(c["verdicts"])))
        if c["things"] != 3:
            problems.append("%s: %d 'things to know' (want 3)" % (s["id"], c["things"]))
        print(tag)
        stocks.append(s)

    data = {"title": manifest.get("title", "Portfolio"), "stocks": stocks}
    template = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_template.html"), encoding="utf-8").read()
    html = (template.replace("__TITLE__", data["title"])
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    with open(args.out, "w", encoding="utf-8") as fh_out:
        fh_out.write(html)
    print("wrote %s (%d KB, %d stocks%s)" % (args.out, len(html.encode("utf-8")) // 1024, len(stocks),
                                            ", %d listed but not added yet" % pending if pending else ""))
    if problems:
        print("\nFix before publishing:")
        for p in problems:
            print("  " + p)


if __name__ == "__main__":
    main()
