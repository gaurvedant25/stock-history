#!/usr/bin/env python3
"""Pull corporate filings from the exchange itself, to explain days the news missed.

    python fetch_filings.py --history history.json --out filings.json
    python fetch_filings.py --symbol RELIANCE.NS --from 2026-06-22 --to 2026-09-22

Filings are the primary source. A block deal, a stake change, a subsidiary sale
or a rating action is disclosed to the exchange and often never reaches a news
site, so a day that looks unexplained in search can be fully explained here.

Just as important: when the exchange shows nothing either, an empty result is
real evidence rather than a failed search, and the report can say so.

India uses NSE's announcements feed (which needs a session cookie first). The US
uses SEC EDGAR, which rejects browser user-agents and requires a declared tool
identifier -- set SEC_CONTACT to your own email to follow their fair-access
policy.
"""

import argparse
import gzip
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta

BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SEC_UA = "stock-history-skill " + os.environ.get("SEC_CONTACT", "contact@example.com")
NSE_REF = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
          "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}

# Categories that can actually reprice a company, versus the compliance traffic
# that files constantly and moves nothing. Getting this wrong in the loud
# direction is worse: a routine filing sitting near a big move is exactly the
# kind of coincidence that turns into a confidently wrong explanation.
HIGH_SIGNAL = (
    "outcome of board meeting", "financial result", "press release",
    "investor presentation", "acquisition", "amalgamation", "merger", "demerger",
    "scheme of arrangement", "buyback", "bonus", "split", "rights issue",
    "dividend", "credit rating", "news verification", "takeover", "open offer",
    "resignation", "appointment of", "fund raising", "preferential issue",
    "allotment of securities", "order", "penalty", "litigation", "shutdown",
    "capacity", "agreement", "joint venture", "divestment", "stake",
    "business update", "revenue update", "quarterly update", "operational update",
    "sales update", "production update",
)
ROUTINE = (
    "trading window", "copy of newspaper publication", "newspaper advertisement",
    "compliance certificate", "reconciliation of share capital", "investor complaint",
    "certificate under", "loss of share certificate", "duplicate share",
)


def fetch(url, headers, timeout=30, opener=None):
    req = urllib.request.Request(url, headers=headers)
    # An OpenerDirector exposes .open(); the module-level helper is .urlopen().
    resp = opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def classify(category, text):
    blob = (category + " " + text).lower()
    if any(k in blob for k in ROUTINE):
        return "routine"
    if any(k in blob for k in HIGH_SIGNAL):
        return "high"
    return "unclear"


def nse_symbol(symbol):
    return symbol.split(".")[0].upper()


def session_for(date, time_str, close_hhmm, trading_days):
    """Which trading session a filing could actually move.

    This is the single most useful thing filings give you. Indian results
    routinely land around 19:00, hours after the 15:30 close, so a rally on the
    filing date happened *before* the news existed -- it was anticipation, and
    the real reaction is the next session. Attributing the earlier move to the
    filing reverses cause and effect, and reads perfectly plausibly while doing
    it.
    """
    later = [d for d in trading_days if d > date]
    nxt = later[0] if later else shift(date, 1)
    if time_str:
        return (nxt, True) if time_str > close_hhmm else (date, False)
    # SEC's submissions feed carries no time of day, and US earnings are usually
    # released after the close. Without a timestamp the session is genuinely
    # ambiguous, so claim both rather than silently picking the wrong one.
    return date, None


def nse_filings(symbol):
    """NSE refuses the API until a page visit has set a session cookie."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    head = {"User-Agent": BROWSER, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    opener.open(urllib.request.Request(NSE_REF, headers=head), timeout=30).read()

    head_api = dict(head)
    head_api["Referer"] = NSE_REF
    url = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol="
           + urllib.parse.quote(nse_symbol(symbol)))
    raw = fetch(url, head_api, opener=opener)

    out = []
    for a in raw if isinstance(raw, list) else []:
        stamp = a.get("an_dt") or ""
        m = re.match(r"(\d{2})-(\w{3})-(\d{4})\s*(\d{2}:\d{2})?", stamp)
        if not m:
            continue
        date = "%s-%s-%s" % (m.group(3), MONTHS.get(m.group(2), "01"), m.group(1))
        cat = (a.get("desc") or "").strip()
        text = re.sub(r"\s+", " ", (a.get("attchmntText") or "")).strip()
        out.append(OrderedDict(
            date=date, time=m.group(4) or "", category=cat, text=text[:600],
            signal=classify(cat, text), url=a.get("attchmntFile") or "",
        ))
    return out, "NSE corporate announcements"


DEALS_REF = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
# A client that bought and sold within this ratio of each other the same day
# was trading, not building or cutting a stake.
CHURN_RATIO = 0.6


def nse_deals(symbol, start, end, volume_by_date):
    """Bulk and block deals on NSE, grouped by date and client.

    Heavy volume with no news is often one large investor, and NSE publishes
    every bulk deal (0.5%+ of a company's shares in a day) and block deal. A
    client that both bought and sold about the same amount is marked `churn`:
    that was trading (often high-frequency or arbitrage desks), which explains
    the volume but not the direction of the price.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    head = {"User-Agent": BROWSER, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    opener.open(urllib.request.Request(DEALS_REF, headers=head), timeout=30).read()
    head_api = dict(head)
    head_api["Referer"] = DEALS_REF
    rows = []
    t, stop = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    while t <= stop:  # NSE answers in bounded ranges; ask a quarter at a time
        t2 = min(t + timedelta(days=90), stop)
        for kind in ("bulk_deals", "block_deals"):
            url = ("https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=%s&symbol=%s&from=%s&to=%s"
                   % (kind, urllib.parse.quote(nse_symbol(symbol)), t.strftime("%d-%m-%Y"), t2.strftime("%d-%m-%Y")))
            for d in (fetch(url, head_api, opener=opener) or {}).get("data", []):
                day = datetime.strptime(d["BD_DT_DATE"].title(), "%d-%b-%Y").strftime("%Y-%m-%d")
                rows.append((day, kind.split("_")[0], d.get("BD_CLIENT_NAME", "").strip(), d.get("BD_BUY_SELL", "").upper(),
                             int(d.get("BD_QTY_TRD") or 0), float(d.get("BD_TP_WATP") or 0)))
        t = t2 + timedelta(days=1)
    by_day = OrderedDict()
    for day, kind, client, side, qty, price in sorted(rows):
        c = by_day.setdefault(day, OrderedDict()).setdefault(client, {"kind": kind, "bought": 0, "sold": 0, "value": 0.0})
        c["bought" if side == "BUY" else "sold"] += qty
        c["value"] += qty * price
    out = OrderedDict()
    for day, clients in by_day.items():
        vol = volume_by_date.get(day)
        items = []
        for client, c in clients.items():
            hi, lo = max(c["bought"], c["sold"]), min(c["bought"], c["sold"])
            traded = c["bought"] + c["sold"]
            items.append(OrderedDict(
                client=client, kind=c["kind"], bought=c["bought"], sold=c["sold"], net=c["bought"] - c["sold"],
                avg_price=round(c["value"] / traded, 2) if traded else None,
                value_cr=round(c["value"] / 1e7, 1),
                share_of_day_volume=round(traded / vol, 2) if vol else None,
                churn=hi > 0 and lo >= CHURN_RATIO * hi))
        items.sort(key=lambda x: -abs(x["net"]) - (0 if not x["churn"] else x["bought"]))
        out[day] = items
    return out


def sec_filings(symbol):
    head = {"User-Agent": SEC_UA, "Accept": "application/json,*/*",
            "Accept-Encoding": "gzip, deflate"}
    tickers = fetch("https://www.sec.gov/files/company_tickers.json", head)
    cik = None
    for v in tickers.values():
        if v.get("ticker", "").upper() == symbol.upper():
            cik = str(v["cik_str"]).zfill(10)
            break
    if not cik:
        return [], "SEC EDGAR (ticker not found)"

    sub = fetch("https://data.sec.gov/submissions/CIK%s.json" % cik, head)
    r = sub.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(r.get("form", [])):
        desc = (r.get("primaryDocDescription", [None] * (i + 1))[i] or "")
        acc = r.get("accessionNumber", [""] * (i + 1))[i].replace("-", "")
        out.append(OrderedDict(
            date=r.get("filingDate", [""] * (i + 1))[i],
            time="", category=form,
            text=(r.get("items", [""] * (i + 1))[i] or desc or "").strip()[:600],
            signal="high" if form in ("8-K", "10-Q", "10-K", "SC 13D", "SC 13G") else "routine",
            url="https://www.sec.gov/Archives/edgar/data/%s/%s/" % (int(cik), acc) if acc else "",
        ))
    return out, "SEC EDGAR"


def shift(date, days):
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="ticker, e.g. RELIANCE.NS or AAPL")
    ap.add_argument("--history", help="history.json -- takes the symbol and move dates from it")
    ap.add_argument("--from", dest="start", help="window start YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="window end YYYY-MM-DD")
    ap.add_argument("--lookback", type=int, default=1,
                    help="days before a move to search (default 1: filings land after the close)")
    ap.add_argument("--out", help="write JSON here")
    args = ap.parse_args()

    moves, symbol, trading_days, change, volume = [], args.symbol, [], {}, {}
    if args.history:
        with open(args.history, encoding="utf-8") as fh:
            hist = json.load(fh)
        symbol = symbol or hist["meta"]["symbol"]
        series = hist.get("series", [])
        # Every window the report can switch to lists its own biggest days, so
        # check filings for all of them, not only the analysis window's.
        by_window = hist.get("moves_by_window")
        if by_window is None and series:
            from fetch_history import moves_by_window
            by_window = moves_by_window(series, hist["meta"].get("context_months", 12))
        moves = sorted({m["date"] for ms in (by_window or {}).values() for m in ms}
                       | {m["date"] for m in hist.get("moves", [])})
        trading_days = [r["date"] for r in series]
        volume = {r["date"]: r["volume"] for r in series if r.get("volume")}
        for i in range(1, len(series)):
            prev, cur = series[i - 1]["close"], series[i]["close"]
            change[series[i]["date"]] = round((cur - prev) / prev * 100, 2) if prev else 0.0
        args.start = args.start or (moves[0] if moves else None) or \
            hist["meta"].get("window_start") or hist["stats"]["start_date"]
        args.end = args.end or hist["stats"]["end_date"]
    if not symbol:
        ap.error("pass --symbol or --history")

    try:
        indian = symbol.upper().endswith((".NS", ".BO"))
        rows, source = nse_filings(symbol) if indian else sec_filings(symbol)
    except urllib.error.HTTPError as exc:
        sys.exit("HTTP %s from %s. NSE blocks requests without a session cookie and SEC "
                 "rejects browser user-agents; if this persists, read the filings page in a "
                 "browser instead." % (exc.code, "NSE" if symbol.upper().endswith((".NS", ".BO")) else "SEC"))
    except urllib.error.URLError as exc:
        sys.exit("Network error: %s" % exc.reason)

    if args.start:
        rows = [r for r in rows if r["date"] >= shift(args.start, -args.lookback - 2)]
    if args.end:
        rows = [r for r in rows if r["date"] <= args.end]

    close_hhmm = "15:30" if indian else "16:00"
    nextday = {}
    for r in rows:
        r["affects"], r["after_close"] = session_for(
            r["date"], r["time"], close_hhmm, trading_days)
        later = [d for d in trading_days if d > r["date"]]
        nextday[r["date"]] = later[0] if later else shift(r["date"], 1)
    rows.sort(key=lambda r: (r["date"], r["time"]), reverse=True)

    # Tie each filing to the session it could actually have moved, not the date
    # printed on it. A filing with no timestamp claims both candidate sessions.
    def hits(r, date):
        return r["affects"] == date or (
            r["after_close"] is None and nextday.get(r["date"]) == date)

    near = OrderedDict()
    for date in sorted(moves):
        near[date] = [r for r in rows if hits(r, date)]

    payload = OrderedDict(symbol=symbol, source=source, start=args.start, end=args.end,
                          count=len(rows), by_move=near, filings=rows)
    if indian and args.start and args.end:
        try:
            deals = nse_deals(symbol, args.start, args.end, volume)
            payload["deals"] = deals
            payload["deals_by_move"] = OrderedDict((d, deals.get(d, [])) for d in sorted(moves))
        except Exception as exc:  # deals are extra evidence; never fail the filings run
            print("warning: bulk/block deals unavailable: %s" % exc)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    print("%s -- %d filings from %s between %s and %s"
          % (symbol, len(rows), source, args.start or "start", args.end or "now"))
    if moves:
        print("\nFilings that could have moved each session "
              "(after-close filings are credited to the next one):")
        for date, hits in near.items():
            if not hits:
                print("  %s  nothing filed -- the blank survives a primary-source check" % date)
                continue
            for h in hits:
                when = (" after close" if h["after_close"] else
                        " (no time filed; could be either session)"
                        if h["after_close"] is None and h["date"] != date else "")
                print("  %s  [%s] filed %s %s%s  %s"
                      % (date, h["signal"], h["date"], h["time"], when, h["category"][:46]))
    for date, ds in (payload.get("deals_by_move") or {}).items():
        if ds:
            print("\n  %s  bulk/block deals:" % date)
            for d in ds[:6]:
                what = ("traded both ways (bought %s, sold %s): explains volume, not direction" % (d["bought"], d["sold"])
                        if d["churn"] else ("net bought" if d["net"] > 0 else "net sold") + " %s shares" % abs(d["net"]))
                share = " = %.0f%% of the day's volume" % (100 * d["share_of_day_volume"]) if d["share_of_day_volume"] else ""
                print("      [%s] %s %s at about %s, %s crore%s" % (d["kind"], d["client"][:48], what, d["avg_price"], d["value_cr"], share))
    high = [r for r in rows if r["signal"] == "high"]
    if high:
        print("\nHigh-signal filings, and what the session they hit actually did:")
        for r in high[:15]:
            pct = change.get(r["affects"])
            moved = ""
            if pct is not None:
                moved = "  session %+.2f%%" % pct
                if abs(pct) < 0.75:
                    moved += " (shrug)"
            print("  filed %s %-6s -> %s%s\n        %s"
                  % (r["date"], r["time"] + (" ac" if r["after_close"] else ""),
                     r["affects"], moved, r["category"][:60]))
        print("\n  A filing whose session barely moved was priced in or ignored; a big move")
        print("  on a session with no filing needs an explanation from somewhere else.")
    if args.out:
        print("\nwrote " + args.out)


if __name__ == "__main__":
    main()
