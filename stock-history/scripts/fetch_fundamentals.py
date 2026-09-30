#!/usr/bin/env python3
"""Pull quarterly results, annual profit & loss, and shareholding for the report.

    python fetch_fundamentals.py --history history.json --out fundamentals.json
    python fetch_fundamentals.py --symbol RELIANCE.NS --out fundamentals.json

These fill the report's drop-down sections. Sources, per market:

  India (.NS/.BO)  Screener.in's company page -- the same Quarterly Results,
                   Profit & Loss and Shareholding Pattern tables Indian investors
                   read. Consolidated where the company reports it, standalone
                   otherwise. Rs crore.
  US               SEC EDGAR XBRL company facts -- the numbers as filed in 10-Qs
                   and 10-Ks. Ownership comes from Yahoo Finance, because holder
                   breakdowns are not part of the XBRL facts.

Yahoo Finance is the fallback for results in both markets (it covers foreign
filers whose SEC facts use IFRS tags, and Indian stocks if Screener is down),
but it only returns about five quarters, too few for year-on-year growth.

Every section records its source, and a failed section is reported rather than
filled in: the report drops a section it has no data for.
"""

import argparse
import html as htmllib
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import date, datetime, timezone

from fetch_filings import SEC_UA, fetch

BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SHOW_QUARTERS = 8
SHOW_YEARS = 6


def label_for(iso):
    y, m, _ = iso.split("-")
    return MON[int(m) - 1] + " " + y


def kind_of(label):
    low = label.lower()
    if "%" in label:
        return "pct"
    if "eps" in low:
        return "eps"
    if "no. of" in low or "count" in low:
        return "count"
    return "money"


def table(columns, rows, notes=None):
    return OrderedDict(columns=columns, rows=rows, notes=notes or [])


def row(label, values, kind=None):
    return OrderedDict(label=label, kind=kind or kind_of(label), values=values)


def month_index(col):
    """'Mar 2026' -> months since year 0; None for 'TTM' or anything unparsed."""
    parts = col.rstrip("*").split()
    if len(parts) == 2 and parts[0] in MON and parts[1].isdigit():
        return int(parts[1]) * 12 + MON.index(parts[0])
    return None


def gappy(columns):
    """True when consecutive annual columns are not one year apart."""
    idx = [month_index(c) for c in columns if month_index(c) is not None]
    return any(b - a != 12 for a, b in zip(idx, idx[1:]))


def growth_row(label, base, offset, columns):
    """Year-on-year change of `base`, `offset` columns back (4 for quarters).

    Only compares columns exactly twelve months apart, so a gap in the source
    table (a company that changed its year end, or restarted consolidated
    reporting) never passes off a multi-year change as one year's growth.
    """
    vals = []
    for i, v in enumerate(base["values"]):
        prev = base["values"][i - offset] if i >= offset else None
        a = month_index(columns[i])
        b = month_index(columns[i - offset]) if i >= offset else None
        if a is None or b is None or a - b != 12 or v is None or not prev or prev < 0:
            vals.append(None)
        else:
            vals.append(round((v - prev) / abs(prev) * 100, 1))
    return row(label, vals, "pct")


def find(rows, *labels):
    for want in labels:
        for r in rows:
            if r["label"].lower() == want.lower():
                return r
    return None


def add_growth(tab, offset):
    rows = tab["rows"]
    rev = find(rows, "Revenue", "Sales")
    net = find(rows, "Net income", "Net Profit")
    extra = []
    for base in (rev, net):
        if base:
            extra.append(growth_row(base["label"] + " growth YoY %", base, offset, tab["columns"]))
    tab["rows"] = rows + extra


def trim(tab, keep):
    cols = tab["columns"]
    cut = max(0, len(cols) - keep - (1 if cols and cols[-1] == "TTM" else 0))
    tab["columns"] = cols[cut:]
    for r in tab["rows"]:
        r["values"] = r["values"][cut:]
    return tab


# --- India: Screener.in ---------------------------------------------------

def _cells(fragment, tag):
    return [re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), fragment, re.S)]


def _num(text):
    t = text.replace(",", "").replace("%", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _screener_table(block):
    head = re.search(r"<thead>(.*?)</thead>", block, re.S)
    body = re.search(r"<tbody>(.*?)</tbody>", block, re.S)
    if not head or not body:
        return None
    columns = _cells(head.group(1), "th")[1:]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        cells = _cells(tr, "td")
        if len(cells) < 2:
            continue
        label = cells[0].replace("+", "").strip()
        values = [_num(c) for c in cells[1:]]
        if not label or all(v is None for v in values):
            continue  # the "Raw PDF" link row and similar
        # Shareholding cells carry their own "%" while the row label does not.
        pct = any(c.endswith("%") for c in cells[1:])
        rows.append(row(label, values, "pct" if pct else None))
    return table(columns, rows) if rows else None


def screener(symbol):
    code = symbol.rsplit(".", 1)[0]
    last_err = None
    for variant in ("consolidated/", ""):
        url = "https://www.screener.in/company/%s/%s" % (urllib.parse.quote(code), variant)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER})
            page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last_err = "Screener HTTP %s" % exc.code
            continue
        out = OrderedDict()
        for key, sid in (("quarterly", "quarters"), ("annual", "profit-loss")):
            m = re.search(r'<section id="%s".*?</section>' % sid, page, re.S)
            out[key] = _screener_table(m.group(0)) if m else None
        m = re.search(r'<div id="quarterly-shp">.*?</table>', page, re.S)
        out["shareholding"] = _screener_table(m.group(0)) if m else None
        if not out["quarterly"]:
            last_err = "Screener has no results table at " + url
            continue  # no consolidated accounts; try standalone
        if variant and out["annual"] and gappy([c for c in out["annual"]["columns"] if c != "TTM"][-SHOW_YEARS:]):
            last_err = "consolidated annual table has gaps at " + url
            continue  # consolidated reporting restarted recently; standalone has the full run
        basis = "Consolidated" if variant else "Standalone"
        for key in ("quarterly", "annual"):
            if out[key]:
                out[key]["notes"].append(basis + " figures in Rs crore, from Screener.in.")
        if out["quarterly"]:
            add_growth(out["quarterly"], 4)
            trim(out["quarterly"], SHOW_QUARTERS)
        if out["annual"]:
            add_growth(out["annual"], 1)
            trim(out["annual"], SHOW_YEARS)
        if out["shareholding"]:
            out["shareholding"]["notes"].append(
                "Percent of shares held at each quarter end, from exchange filings via Screener.in.")
        return out, url, "₹ crore"
    raise RuntimeError(last_err or "Screener lookup failed")


# --- US: SEC EDGAR XBRL company facts ---------------------------------------

CONCEPTS = [
    ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    ("Gross profit", ["GrossProfit"]),
    ("Operating expenses", ["OperatingExpenses"]),
    ("Operating income", ["OperatingIncomeLoss"]),
    ("Pre-tax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("Income tax", ["IncomeTaxExpenseBenefit"]),
    ("Net income", ["NetIncomeLoss", "ProfitLoss"]),
    ("Diluted EPS", ["EarningsPerShareDiluted"]),
]


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _periods(facts):
    """Split filed facts into quarters, nine-month YTDs and full years by end date."""
    q, ytd9, fy = {}, {}, {}
    for f in facts:
        if not f.get("start") or not str(f.get("form", "")).startswith(("10-Q", "10-K")):
            continue
        span = _days(f["start"], f["end"])
        bucket = q if 80 <= span <= 100 else ytd9 if 260 <= span <= 285 else fy if 350 <= span <= 380 else None
        if bucket is None:
            continue
        key = (f["start"], f["end"]) if bucket is ytd9 else f["end"]
        if key not in bucket or f.get("filed", "") > bucket[key][1]:
            bucket[key] = (f["val"], f.get("filed", ""), f["start"])
    return q, ytd9, fy


def sec_results(symbol):
    head = {"User-Agent": SEC_UA, "Accept": "application/json,*/*", "Accept-Encoding": "gzip, deflate"}
    tickers = fetch("https://www.sec.gov/files/company_tickers.json", head)
    cik = next((str(v["cik_str"]).zfill(10) for v in tickers.values()
                if v.get("ticker", "").upper() == symbol.upper()), None)
    if not cik:
        raise RuntimeError("ticker not in SEC's list")
    facts = fetch("https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % cik, head)
    gaap = facts.get("facts", {}).get("us-gaap", {})

    series = OrderedDict()
    derived_q4 = set()
    for label, tags in CONCEPTS:
        best = None
        for tag in tags:
            units = gaap.get(tag, {}).get("units", {})
            vals = units.get("USD") or units.get("USD/shares")
            if vals:
                latest = max(f["end"] for f in vals)
                if not best or latest > best[0]:
                    best = (latest, vals)
        if not best:
            continue
        q, ytd9, fy = _periods(best[1])
        quarters = {end: v[0] for end, v in q.items()}
        # 10-Ks rarely break out the fourth quarter, so derive it: full year
        # minus the nine-month year-to-date with the same fiscal start.
        for end, (val, _, start) in fy.items():
            if end in quarters:
                continue
            nine = next((v for (s, e), v in ytd9.items() if s == start), None)
            if nine is not None:
                quarters[end] = val - nine[0]
                derived_q4.add(end)
        series[label] = (quarters, {end: v[0] for end, v in fy.items()})
    if "Revenue" not in series and "Net income" not in series:
        raise RuntimeError("no us-gaap income-statement facts (IFRS filer?)")

    def assemble(idx, keep):
        ends = sorted({e for qs in series.values() for e in qs[idx]})[-(keep + 4):]
        cols = [label_for(e) + ("*" if idx == 0 and e in derived_q4 else "") for e in ends]
        rows = []
        for label, pair in series.items():
            vals = []
            for e in ends:
                v = pair[idx].get(e)
                vals.append(None if v is None else round(v, 2) if label == "Diluted EPS" else round(v / 1e6, 1))
            rows.append(row(label, vals, "eps" if label == "Diluted EPS" else "money"))
        tab = table(cols, rows)
        add_growth(tab, 4 if idx == 0 else 1)
        return trim(tab, keep)

    quarterly, annual = assemble(0, SHOW_QUARTERS), assemble(1, SHOW_YEARS)
    quarterly["notes"].append("USD millions except EPS, as filed with the SEC (10-Q and 10-K).")
    if any(c.endswith("*") for c in quarterly["columns"]):
        quarterly["notes"].append("* Fourth quarter derived as the full year minus the first nine months; "
                                  "EPS for those quarters is approximate.")
    annual["notes"].append("USD millions except EPS, from 10-K filings. Columns are fiscal year ends.")
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=" + cik
    return OrderedDict(quarterly=quarterly, annual=annual), url


# --- Yahoo Finance: results fallback and US ownership ----------------------

YAHOO_TYPES = [("Revenue", "TotalRevenue"), ("Gross profit", "GrossProfit"),
               ("Operating income", "OperatingIncome"), ("Pre-tax income", "PretaxIncome"),
               ("Income tax", "TaxProvision"), ("Net income", "NetIncome"), ("Diluted EPS", "DilutedEPS")]


def _yahoo_opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", BROWSER)]
    return op


def yahoo_results(symbol, currency):
    op = _yahoo_opener()
    scale, unit = (1e7, "₹ crore") if currency == "INR" else (1e6, (currency or "") + " millions")
    out = OrderedDict()
    for key, prefix, keep in (("quarterly", "quarterly", SHOW_QUARTERS), ("annual", "annual", SHOW_YEARS)):
        types = ",".join(prefix + t for _, t in YAHOO_TYPES)
        url = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
               "?symbol=%s&type=%s&period1=1262304000&period2=%d"
               % (urllib.parse.quote(symbol), urllib.parse.quote(symbol), types, int(time.time())))
        data = json.load(op.open(url, timeout=30))
        got = {}
        for res in data.get("timeseries", {}).get("result", []):
            t = res["meta"]["type"][0]
            got[t[len(prefix):]] = {p["asOfDate"]: p["reportedValue"]["raw"]
                                    for p in (res.get(t) or []) if p}
        ends = sorted({e for v in got.values() for e in v})
        if not ends:
            continue
        rows = []
        for label, t in YAHOO_TYPES:
            vals = got.get(t, {})
            if not vals:
                continue
            eps = label == "Diluted EPS"
            rows.append(row(label, [None if vals.get(e) is None else
                                    round(vals[e], 2) if eps else round(vals[e] / scale, 1)
                                    for e in ends], "eps" if eps else "money"))
        tab = table([label_for(e) for e in ends], rows,
                    ["%s except EPS, from Yahoo Finance." % unit])
        add_growth(tab, 4 if key == "quarterly" else 1)
        out[key] = trim(tab, keep)
    if not out:
        raise RuntimeError("Yahoo returned no statements")
    return out, "https://finance.yahoo.com/quote/%s/financials" % urllib.parse.quote(symbol), unit


def yahoo_holders(symbol):
    op = _yahoo_opener()
    try:
        op.open("https://fc.yahoo.com", timeout=20)
    except Exception:
        pass  # this only sets the cookie; it answers 404 by design
    crumb = op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20).read().decode()
    url = ("https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=majorHoldersBreakdown,"
           "institutionOwnership&crumb=%s" % (urllib.parse.quote(symbol), urllib.parse.quote(crumb)))
    res = json.load(op.open(url, timeout=30))["quoteSummary"]["result"][0]
    mh = res.get("majorHoldersBreakdown", {})

    def pct(k):
        v = (mh.get(k) or {}).get("raw")
        return None if v is None else round(v * 100, 2)

    rows = [row("Insiders %", [pct("insidersPercentHeld")]),
            row("Institutions %", [pct("institutionsPercentHeld")]),
            row("Institutions (count)", [(mh.get("institutionsCount") or {}).get("raw")], "count")]
    holders = []
    for o in (res.get("institutionOwnership") or {}).get("ownershipList", [])[:10]:
        holders.append(OrderedDict(
            name=o.get("organization"),
            pct=round((o.get("pctHeld") or {}).get("raw", 0) * 100, 2),
            as_of=(o.get("reportDate") or {}).get("fmt")))
    tab = table(["Latest"], [r for r in rows if r["values"][0] is not None],
                ["Percent of shares outstanding, from Yahoo Finance's 13F-based holder data. "
                 "Institutional totals lag by up to a quarter and can exceed 100% of the float "
                 "when shares are lent out and sold short."])
    tab["holders"] = holders
    return tab, "https://finance.yahoo.com/quote/%s/holders" % urllib.parse.quote(symbol)


def build(symbol, currency=None):
    out = OrderedDict(symbol=symbol, fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                      sources=OrderedDict(), errors=[])
    indian = symbol.upper().endswith((".NS", ".BO"))
    results = None
    try:
        if indian:
            results, url, out["unit"] = screener(symbol)
            out["sources"]["results"] = OrderedDict(name="Screener.in", url=url)
            if results.get("shareholding"):
                out["sources"]["shareholding"] = OrderedDict(name="Screener.in", url=url + "#shareholding")
        else:
            results, url = sec_results(symbol)
            out["unit"] = "USD millions"
            out["sources"]["results"] = OrderedDict(name="SEC EDGAR (XBRL)", url=url)
    except Exception as exc:
        out["errors"].append("%s results: %s" % ("Screener" if indian else "SEC", exc))
    if not results or not results.get("quarterly"):
        try:
            yres, url, out["unit"] = yahoo_results(symbol, currency or ("INR" if indian else "USD"))
            results = OrderedDict(results or {}, **yres)
            out["sources"]["results"] = OrderedDict(name="Yahoo Finance", url=url)
        except Exception as exc:
            out["errors"].append("Yahoo results: %s" % exc)
    for key in ("quarterly", "annual", "shareholding"):
        out[key] = (results or {}).get(key)
    if not out["shareholding"]:
        try:
            out["shareholding"], url = yahoo_holders(symbol)
            out["sources"]["shareholding"] = OrderedDict(name="Yahoo Finance", url=url)
        except Exception as exc:
            out["errors"].append("Yahoo holders: %s" % exc)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="ticker, e.g. RELIANCE.NS or AAPL")
    ap.add_argument("--history", help="history.json -- takes the symbol and currency from it")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args()

    symbol, currency = args.symbol, None
    if args.history:
        with open(args.history, encoding="utf-8") as fh:
            meta = json.load(fh)["meta"]
        symbol, currency = symbol or meta["symbol"], meta.get("currency")
    if not symbol:
        ap.error("pass --symbol or --history")

    payload = build(symbol, currency)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if not args.out:
        print(text)
        return
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    for key in ("quarterly", "annual", "shareholding"):
        tab = payload.get(key)
        if tab:
            print("%-12s %d rows x %d columns (%s ... %s)" % (
                key, len(tab["rows"]), len(tab["columns"]), tab["columns"][0], tab["columns"][-1]))
        else:
            print("%-12s missing -- the report will leave this section out" % key)
    for src, s in payload["sources"].items():
        print("source %-12s %s" % (src, s["name"]))
    for e in payload["errors"]:
        print("warning: " + e)
    print("wrote " + args.out)


if __name__ == "__main__":
    main()
