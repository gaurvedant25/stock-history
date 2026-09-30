#!/usr/bin/env python3
"""Resolve a stock, pull daily price history, and find the moves worth explaining.

Two modes:

  Resolve a name to candidate tickers (do this first when given a name):
      python fetch_history.py --resolve "tata motors"

  Pull history once you know the exact symbol:
      python fetch_history.py --symbol RELIANCE.NS --months 3 --out history.json

Data comes from Yahoo Finance's public chart endpoint. No API key, no
dependencies beyond the standard library.
"""

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (compatible; stock-history-skill/1.0)"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
SEARCH = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=10&newsCount=0"

# Which index to measure a stock against, so a drop that was really the whole
# market falling can be told apart from something the company itself did.
BENCHMARKS = {
    "NSI": ("^NSEI", "NIFTY 50"),
    "BSE": ("^BSESN", "SENSEX"),
    "NMS": ("^GSPC", "S&P 500"),
    "NGM": ("^GSPC", "S&P 500"),
    "NCM": ("^GSPC", "S&P 500"),
    "NYQ": ("^GSPC", "S&P 500"),
    "PCX": ("^GSPC", "S&P 500"),
    "ASE": ("^GSPC", "S&P 500"),
}


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def resolve(query):
    """Return equity candidates for a name.

    Never glue '.NS' onto a company name and hope: tickers change after
    demergers and renames (TATAMOTORS.NS is dead, it became TMPV.NS and
    TMCV.NS), and the same word often lists in several markets.
    """
    data = fetch_json(SEARCH.format(q=urllib.parse.quote(query)))
    out = []
    for q in data.get("quotes", []):
        if q.get("quoteType") != "EQUITY":
            continue
        out.append(
            OrderedDict(
                symbol=q.get("symbol"),
                name=q.get("shortname") or q.get("longname"),
                exchange=q.get("exchange"),
                exchange_name=q.get("exchDisp"),
                industry=q.get("industry"),
            )
        )
    return out


def fetch_series(symbol, months):
    rng = {1: "1mo", 3: "3mo", 6: "6mo", 12: "1y"}.get(months, str(months) + "mo")
    data = fetch_json(CHART.format(sym=urllib.parse.quote(symbol), rng=rng))
    result = data["chart"]["result"][0]
    meta = result["meta"]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    # For a day or so after a session ends, Yahoo can leave that day's close
    # blank while its volume is already in. The quote meta then holds the
    # final price (it matched NSE's official close when checked), so use it
    # for the last bar once that session is over.
    off = meta.get("gmtoffset", 0)
    day = lambda t: datetime.fromtimestamp(t + off, tz=timezone.utc).date()
    end = ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end", 0)
    now = datetime.now(timezone.utc).timestamp()
    if stamps and len(closes) == len(stamps) and closes[-1] is None and meta.get("regularMarketPrice"):
        if day(meta.get("regularMarketTime", 0)) == day(stamps[-1]) and (day(now) > day(stamps[-1]) or now >= end):
            closes = list(closes)
            closes[-1] = meta["regularMarketPrice"]
    # A session that is still trading has a live price, not a close. Leave it
    # out, so a report run mid-session never treats an intraday quote as a close.
    if stamps and day(now) == day(stamps[-1]) and now < end:
        stamps, closes, volumes = stamps[:-1], closes[:-1], volumes[:-1]

    rows = []
    for i, ts in enumerate(stamps):
        close = closes[i] if i < len(closes) else None
        if close is None:  # holidays and trading halts come back as null
            continue
        vol = volumes[i] if i < len(volumes) else None
        if not vol and rows and round(float(close), 2) == rows[-1]["close"]:
            continue  # an exchange holiday Yahoo fills with the previous close and no volume
        rows.append(
            OrderedDict(
                date=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                close=round(float(close), 2),
                volume=int(volumes[i]) if i < len(volumes) and volumes[i] else None,
            )
        )
    if not rows:
        raise SystemExit("No price data returned for " + symbol + ".")
    return meta, rows


def pct(a, b):
    return round((b - a) / a * 100, 2) if a else 0.0


def daily_changes(rows):
    return [
        (rows[i]["date"], pct(rows[i - 1]["close"], rows[i]["close"]))
        for i in range(1, len(rows))
    ]


def monthly(rows):
    buckets = OrderedDict()
    for r in rows:
        buckets.setdefault(r["date"][:7], []).append(r)
    out = []
    for month, rs in buckets.items():
        out.append(
            OrderedDict(
                month=month,
                open=rs[0]["close"],
                close=rs[-1]["close"],
                change_pct=pct(rs[0]["close"], rs[-1]["close"]),
                high=max(r["close"] for r in rs),
                low=min(r["close"] for r in rs),
            )
        )
    return out


def classify(change, bench):
    """Label what a single day's move was probably about.

    A stock that fell 3% on a day the index fell 3% has no company-specific
    story to find, and hunting for one invites inventing a cause.
    """
    if bench is None:
        return "unknown", None
    excess = round(change - bench, 2)
    if abs(excess) < 1.0 and abs(bench) > 0.75:
        return "market-wide", excess
    if abs(excess) >= 2.0:
        return "stock-specific", excess
    return "mixed", excess


def months_before(date_str, n):
    """Date n calendar months earlier, clamped to a real day of that month."""
    year, month, day = (int(p) for p in date_str.split("-"))
    month -= n
    while month <= 0:
        month += 12
        year -= 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    last = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return "%04d-%02d-%02d" % (year, month, min(day, last))


# The report's window switcher offers these; each gets at most this many days.
WINDOW_TOP = OrderedDict([(1, 5), (3, 8), (6, 10), (12, 12)])
VOLUME_LOOKBACK = 60
# A day is only listed when it moved at least this many times the stock's
# normal daily move. A volatile small cap that swings 3% on an ordinary day
# needs a bigger move to count, and a quiet month lists fewer days rather than
# padding the list with sessions nobody would write a news story about.
UNUSUAL_MULTIPLE = 2.5


def typical_move(rows):
    """Mean absolute daily change over the whole carried series, in percent."""
    ch = [abs(pct(rows[i - 1]["close"], rows[i]["close"])) for i in range(1, len(rows))]
    return statistics.mean(ch) if ch else 0.0


def top_moves(rows, since, top_n, floor=None):
    """Up to top_n of the biggest single-day moves on or after `since`.

    Only days at least `floor` percent (default: UNUSUAL_MULTIPLE times the
    stock's typical move) qualify. Works from the carried series alone (the
    index close sits on each row as `bench`), so an older history.json can be
    re-sliced without a refetch. Volume is compared with the trailing average
    before each day rather than a window average, so one day reads the same
    whichever window lists it.
    """
    if floor is None:
        floor = UNUSUAL_MULTIPLE * typical_move(rows)
    out = []
    for i in range(1, len(rows)):
        row, prev = rows[i], rows[i - 1]
        if row["date"] < since:
            continue
        change = pct(prev["close"], row["close"])
        if abs(change) < floor:
            continue
        bench = None
        if row.get("bench") is not None and prev.get("bench") is not None:
            bench = pct(prev["bench"], row["bench"])
        kind, excess = classify(change, bench)
        past = [r["volume"] for r in rows[max(0, i - VOLUME_LOOKBACK):i] if r["volume"]]
        avg = statistics.mean(past) if past else None
        vol = row["volume"]
        out.append(OrderedDict(
            date=row["date"],
            change_pct=change,
            close=row["close"],
            benchmark_pct=bench,
            excess_pct=excess,
            likely=kind,
            volume=vol,
            volume_vs_avg=round(vol / avg, 2) if vol and avg else None,
        ))
    out.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
    out = out[:top_n]
    out.sort(key=lambda m: m["date"])
    return out


def moves_by_window(rows, context_months, analysis_months=None, analysis_top=None):
    """Biggest days for every window the report can switch to, keyed "1"/"3"/..."""
    last = rows[-1]["date"]
    wins = OrderedDict((m, n) for m, n in WINDOW_TOP.items() if m <= context_months)
    if analysis_months:
        wins[analysis_months] = analysis_top or wins.get(analysis_months, 8)
    floor = UNUSUAL_MULTIPLE * typical_move(rows)
    return OrderedDict(
        (str(m), top_moves(rows, months_before(last, m), n, floor)) for m, n in sorted(wins.items())
    )


def build(symbol, months, top_n, context_months=12):
    """Analyse `months` of history, but carry `context_months` of series.

    The extra series is what lets a published report switch between 1M, 3M, 6M
    and 1Y views without another fetch; the stats and moves below still describe
    the analysis window, which is what the written report covers.
    """
    meta, all_rows = fetch_series(symbol, context_months)
    exch = meta.get("exchangeName") or meta.get("fullExchangeName") or ""

    bench_sym, bench_name = BENCHMARKS.get(exch, (None, None))
    bench_close = {}
    bench_summary = None
    if bench_sym:
        try:
            _, brows = fetch_series(bench_sym, context_months)
            bench_close = {r["date"]: r["close"] for r in brows}
        except Exception as exc:  # a missing benchmark must not sink the report
            bench_summary = OrderedDict(symbol=bench_sym, name=bench_name, error=str(exc))

    for row in all_rows:
        row["bench"] = bench_close.get(row["date"])

    cutoff = months_before(all_rows[-1]["date"], months)
    rows = [r for r in all_rows if r["date"] >= cutoff] or all_rows

    if bench_sym and bench_close and bench_summary is None:
        in_window = [r["bench"] for r in rows if r["bench"] is not None]
        bench_summary = OrderedDict(
            symbol=bench_sym,
            name=bench_name,
            change_pct=pct(in_window[0], in_window[-1]) if len(in_window) > 1 else 0.0,
        )

    changes = daily_changes(rows)
    vols = [r["volume"] for r in rows if r["volume"]]
    avg_vol = statistics.mean(vols) if vols else None

    by_window = moves_by_window(all_rows, context_months, months, top_n)
    moves = by_window[str(months)]

    abs_daily = [abs(c) for _, c in changes]
    high = max(rows, key=lambda r: r["close"])
    low = min(rows, key=lambda r: r["close"])
    total = pct(rows[0]["close"], rows[-1]["close"])

    stats = OrderedDict(
        start_date=rows[0]["date"],
        end_date=rows[-1]["date"],
        start_close=rows[0]["close"],
        end_close=rows[-1]["close"],
        change_pct=total,
        change_abs=round(rows[-1]["close"] - rows[0]["close"], 2),
        high=OrderedDict(close=high["close"], date=high["date"]),
        low=OrderedDict(close=low["close"], date=low["date"]),
        avg_abs_daily_move_pct=round(statistics.mean(abs_daily), 2) if abs_daily else None,
        up_days=sum(1 for _, c in changes if c > 0),
        down_days=sum(1 for _, c in changes if c < 0),
        avg_volume=int(avg_vol) if avg_vol else None,
    )
    if bench_summary and "change_pct" in bench_summary:
        stats["vs_benchmark_pct"] = round(total - bench_summary["change_pct"], 2)

    return OrderedDict(
        meta=OrderedDict(
            symbol=meta.get("symbol", symbol),
            name=meta.get("longName") or meta.get("shortName"),
            exchange=exch,
            currency=meta.get("currency"),
            last_price=meta.get("regularMarketPrice"),
            # Lets a report tell a recent listing from a data range that merely
            # starts a day after the one-year mark.
            first_trade_date=datetime.fromtimestamp(meta["firstTradeDate"] + meta.get("gmtoffset", 0), tz=timezone.utc)
            .strftime("%Y-%m-%d") if meta.get("firstTradeDate") else None,
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            source="Yahoo Finance chart API",
            months=months,
            context_months=context_months,
            window_start=rows[0]["date"],
        ),
        stats=stats,
        benchmark=bench_summary,
        monthly=monthly(rows),
        moves=moves,
        moves_by_window=by_window,
        series=all_rows,
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--resolve", metavar="NAME", help="look up candidate tickers for a name")
    ap.add_argument("--symbol", help="exact ticker, e.g. RELIANCE.NS, TMCV.NS, AAPL")
    ap.add_argument("--months", type=int, default=3, help="window the report analyses")
    ap.add_argument("--context-months", type=int, default=12,
                    help="series carried for the report's window switcher")
    ap.add_argument("--top-moves", type=int, default=8)
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args()

    try:
        if args.resolve:
            payload = OrderedDict(query=args.resolve, candidates=resolve(args.resolve))
        elif args.symbol:
            payload = build(args.symbol, args.months, args.top_moves, args.context_months)
        else:
            ap.error("pass --resolve NAME or --symbol TICKER")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            sys.exit(
                "'" + str(args.symbol) + "' is not a live ticker (HTTP 404). Tickers "
                "change after demergers and renames -- re-run with --resolve on the "
                "company name to find the current one."
            )
        sys.exit("HTTP " + str(exc.code) + " fetching data: " + str(exc.reason))
    except urllib.error.URLError as exc:
        sys.exit("Network error: " + str(exc.reason))

    text = json.dumps(payload, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        m = payload.get("meta")
        if m:
            s = payload["stats"]
            print(m["symbol"] + " (" + str(m["name"]) + ") " + str(m["currency"]) + " " + str(m["last_price"]))
            print(s["start_date"] + " -> " + s["end_date"] + ": " + format(s["change_pct"], "+") + "%"
                  + "  high " + str(s["high"]["close"]) + " / low " + str(s["low"]["close"]))
            wins = payload.get("moves_by_window", {})
            dates = {mv["date"] for ms in wins.values() for mv in ms}
            print(", ".join(("1Y" if w == "12" else w + "M") + ": " + str(len(ms))
                            for w, ms in wins.items())
                  + " biggest days per window (" + str(len(dates)) + " distinct dates to research)")
            print("wrote " + args.out)
        else:
            print(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
