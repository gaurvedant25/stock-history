"""Write swing_brief.md in each stock folder of a portfolio: every unique
multi-day swing on the page, with the daily moves, filings, pinned days and
market notes inside it. Research agents then follow
references/swing_research.md and write swing_research.json per stock.

    python make_swing_brief.py portfolios/pms            # every stock
    python make_swing_brief.py portfolios/pms NYKAA KEI  # just these
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_portfolio as bp  # noqa: E402

bp.KEEP_ALL_SWINGS = True  # the page hides unresearched swings; the brief needs them

HERE = os.path.abspath(sys.argv[1])
cal = bp._calendar(os.path.join(HERE, "_"))  # reads HERE/market_calendar.md
port = json.load(open(os.path.join(HERE, "portfolio.json"), encoding="utf-8"))
want = set(sys.argv[2:])
total = 0
for e in port["stocks"]:
    if not e.get("dir") or (want and e["dir"] not in want):
        continue
    folder = os.path.join(HERE, e["dir"])
    D = bp.stock_data(folder, e)
    cur, bn = D["meta"]["cur"] or "", D["meta"]["bench"]  # e.g. "₹" and "NIFTY 50", or "$" and "S&P 500"
    S = json.load(open(os.path.join(folder, "history.json"), encoding="utf-8"))["series"]
    fl = json.load(open(os.path.join(folder, "filings.json"), encoding="utf-8"))["filings"]
    by_aff = {}
    for r in fl:
        if r.get("signal") != "routine" and r.get("affects"):
            by_aff.setdefault(r["affects"], []).append(r)
    spath = os.path.join(folder, "swing_research.json")
    SR = json.load(open(spath, encoding="utf-8")) if os.path.exists(spath) else {}
    done = set(SR.get("swings", {})) | set(SR.get("no_cause", []))
    swings = {}
    for w, W in D["win"].items():
        for sw in W["swings"]:
            if sw["key"] in done:
                continue  # already researched; only new spans go in the brief
            swings.setdefault(sw["key"], (sw, []))[1].append(W["chip"])
    out = ["# %s (%s): multi-day swings to explain" % (D["meta"]["name"], D["id"]), "",
           "Each swing is a stretch where the price moved a long way over several sessions. "
           "Explain the stretch as a whole. Prices close to close; %s over the same span." % bn, ""]
    for key, (sw, chips) in sorted(swings.items()):
        i0, i1 = sw["i0"], sw["i1"]
        a, b = S[i0], S[i1]
        bch = (b["bench"] / a["bench"] - 1) * 100 if a["bench"] and b["bench"] else None
        out += ["## %s  (%+.1f%%)" % (key, sw["chg"]), "",
                "- Windows: %s. From %s%s on %s to %s%s on %s over %d sessions. %s %s." % (
                    ", ".join(chips), cur, a["close"], a["date"], cur, b["close"], b["date"], i1 - i0,
                    bn, "%+.1f%%" % bch if bch is not None else "n/a"),
                "- Daily moves (date, stock %%, %s %%, volume vs the median of the previous 60 sessions):" % bn]
        for k in range(i0, i1 + 1):
            r, p = S[k], S[max(k - 1, 0)]  # the first day in the data has no previous close
            vols = [x["volume"] for x in S[max(0, k - 60):k] if x["volume"]]
            med = statistics.median(vols) if vols else 0
            nb = "%+.2f" % ((r["bench"] / p["bench"] - 1) * 100) if r["bench"] and p["bench"] else "n/a"
            mark = " (start, base close)" if k == i0 else ""
            pin = D["notes"].get(r["date"])
            out.append("    - %s: %+.2f%%, %s %s, %.1fx volume%s%s%s" % (
                r["date"], (r["close"] / p["close"] - 1) * 100, bn, nb, (r["volume"] or 0) / med if med else 0, mark,
                ", PINNED: " + pin["title"] if pin else "",
                ", MARKET: " + cal[r["date"]][0][:160] if r["date"] in cal else ""))
        fs = [r for d in [S[k]["date"] for k in range(i0, i1 + 1)] for r in by_aff.get(d, [])]
        if fs:
            out.append("- Company filings for sessions in the stretch:")
            for r in fs[:14]:
                out.append("    - %s %s%s -> session %s: %s | %s" % (
                    r["date"], r.get("time", ""), " after close" if r.get("after_close") else "", r["affects"],
                    r.get("category", "")[:60], (r.get("text") or "")[:200]))
        out.append("")
    bpath = os.path.join(folder, "swing_brief.md")
    if not swings:
        if os.path.exists(bpath):
            os.remove(bpath)  # nothing left to research
        print(D["id"], 0)
        continue
    open(bpath, "w", encoding="utf-8").write("\n".join(out))
    print(D["id"], len(swings))
    total += len(swings)
print("total", total)
