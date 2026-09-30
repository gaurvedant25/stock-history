---
name: stock-history
description: Pull a listed stock's recent price history and explain what actually moved it — the 3-month trend, the biggest single-day jumps and drops, the news behind each one, and whether a move was company-specific or just the whole market falling. Use this whenever the user names a listed company or ticker on Indian (NSE/BSE) or US markets, pastes a screenshot from a broker app like Zerodha, Groww, Upstox or Dhan, or asks how a share has done, why it fell, why it jumped, or what news hit it. Trigger it on casual phrasing too — "what happened to Suzlon", "why is HDFC down", "check Apple for me", "is Tata Motors still falling" — and whenever a stock or ticker is mentioned alongside words like history, performance, chart, news, results, fell, crashed, rallied, tanked, or moved.
---

# Stock history

## Runtime setup

Resolve `<skill-dir>` to the directory containing this `SKILL.md`. Replace that
placeholder in every command with the actual absolute path, quoting paths that
contain spaces. Do not assume a desktop installation path in a cloud runtime.
Use Python 3 and the bundled scripts. Web research needs browsing or search;
price and filing fetches need outbound network access. If a required capability
is unavailable, disclose the gap rather than inventing data. For SEC requests,
use the user's configured `SEC_CONTACT` environment variable. Store report files
in a writable working directory when the installed skill is read-only.

The user wants two things that usually come apart: the numbers (what the price
did) and the story (why it did that). Charts on broker apps give the first and
none of the second. News sites give the second with no way to tell which
headlines actually mattered.

The job here is to fuse them — put a numbered pin on every day the stock moved
hard, and put a sourced explanation next to each pin. Everything below serves
that.

## The shape of the answer

The deliverable is an **interactive HTML report**, plus a short summary in
chat with its link or file. Publish a hosted page only when a suitable tool is
available and the user has authorized that destination. The page carries the detail so the chat reply does not have
to; three paragraphs and a link beat a wall of tables the reader has to scroll.

Every report, one stock or fifty, is built by `scripts/build_portfolio.py` from
`scripts/portfolio_template.html`. A single stock is simply a portfolio of one:
the search bar hides itself and the page is otherwise identical. Do not use the
older `build_report.py` design (light theme, Chart.js, no day details) unless
the user asks for it; its output is inconsistent with the current design.

The page is built by a script, so your job is the research and the writing, not
the HTML. It gives the reader:

- a **header**: ticker, exchange, last price, change over the window, and one
  plain sentence (the window's **verdict**) naming the main cause;
- a **window switcher** (1M/3M/6M/1Y, or "All" for a recent listing) that
  reslices a year of data already in the page; each window has its own verdict,
  stats and biggest days;
- a **chart** with a short key above it:
  - **numbered pins** on unusually big days that have a researched reason;
  - **shaded bands** on multi-day swings that have a researched reason, each
    with a "+x% · Why?" label;
  - a compare-with-index toggle;
- **details for every day**: hover shows a tooltip, and a click, tap or the
  arrow keys fill a panel under the chart. The panel shows close, change
  against the index, volume against a typical day, the researched reason,
  market news, company filings and bulk or block deals. Clicking a pin or a
  band's "Why?" shows its reason in that same panel;
- **tabs**: Key points (three things to know, biggest moves), All moves (with
  the swings), Financials (quarterly, annual, ownership, with notes), and
  Notes & sources.

A marker appears only when its reason is known. A big day or swing with no
cause found gets no pin and no band; it stays reachable through the day
details, and a computed caveat under Notes & sources names the largest ones.
The user asked for this explicitly: an unexplained marker tells them nothing.

Because the window switcher exists, mention in chat when a longer window tells a
different story — a stock down 6% over three months but 12% over six is a
different situation, and the reader will not find that unless told to look.

## Step 1 — Pin down exactly which stock

Getting this wrong makes every number downstream wrong, and it fails silently:
a confident report about the wrong company looks identical to a right one. So
resolve the ticker rather than assuming it.

```bash
python <skill-dir>/scripts/fetch_history.py --resolve "tata motors"
```

Never build a ticker by gluing `.NS` onto a company name. Tickers die and
change — `TATAMOTORS.NS` returns a 404 because the company demerged into
`TMPV.NS` and `TMCV.NS`, and a name like "reliance" matches both Reliance
Industries in Mumbai and a US-listed Reliance Inc.

Pick the match yourself when it is genuinely obvious (exact name match, one
candidate, right market). When two candidates are both plausible — the demerged
halves of one company, the same name listed in India and the US, or an NSE and
BSE listing where the user's market is unclear — ask which one, showing the
candidates. One short question beats a detailed report about the wrong company.

**From a screenshot:** read the company name, ticker, and exchange off the
image, then resolve that name through the script the same way. Broker apps
often show a shortened display name, so trust the resolution over the
screenshot's spelling. If the screenshot also shows the user's holding or
profit and loss, you may acknowledge it, but the report is still about the
stock's history — do not turn it into advice about their position.

## Step 2 — Pull the price data

```bash
python <skill-dir>/scripts/fetch_history.py \
  --symbol RELIANCE.NS --months 3 --out history.json
```

This analyses three months but carries twelve, which is what lets the published
report switch windows without another fetch. The index series comes down
alongside it for the same reason.

Every price, percentage, high, low, and date in the report comes from this
file. Do not type a price from memory or from a half-remembered headline — model
recall of specific prices is unreliable in a way that is invisible to the
reader, and a wrong number discredits the whole report. If the fetch fails,
say so and fall back to a live source (see `references/data-sources.md`) rather
than filling the gap from memory.

The JSON gives you `stats` (period change, high and low with dates, up and down
day counts, average daily swing), `monthly`, `moves` (the biggest single days
in the analysis window), `moves_by_window` (the biggest days in each switcher
window: at most 5 for 1M, 8 for 3M, 10 for 6M, 12 for 1Y), `benchmark`, and
the full daily `series`.

A day only makes these lists if it moved at least 2.5 times the stock's normal
daily move (`UNUSUAL_MULTIPLE` in fetch_history.py). A volatile small cap that
swings 3% on an ordinary day needs a bigger move to count, and a quiet month
lists fewer days instead of padding the list with sessions nobody reported on.
Before this rule, about half the unexplained days in a 33-stock portfolio were
ordinary volatility. The builders recompute the lists from the saved series, so
changing the rule applies to existing reports without a refetch.

**Every date in `moves_by_window` is a date to research**, not only the
three-month ones — the reader who clicks 1Y should get the year's story, not a
list of blanks. The windows overlap heavily, so the distinct dates are usually
around twenty; the script prints the count. Research depth can taper with age
(a line and a source for a move nine months back is enough), but each one needs
either a pin or an honest blank.

## Step 2b — Pull the financials

```bash
python <skill-dir>/scripts/fetch_fundamentals.py \
  --history history.json --out fundamentals.json
```

This fills the three data drop-downs. Indian tickers come from Screener.in —
the Quarterly Results, Profit & Loss and Shareholding Pattern tables (promoters,
FIIs, DIIs, public) that Indian investors already read, consolidated where the
company reports it. US tickers come from SEC EDGAR's XBRL facts, the figures as
filed in 10-Qs and 10-Ks, with ownership (insiders, institutions, largest
holders) from Yahoo Finance. The script adds year-on-year growth rows and falls
back to Yahoo for results if the primary source fails. It prints what it got;
a missing section is dropped from the page rather than faked.

Two things to know when reading US numbers. 10-Ks rarely report the fourth
quarter on its own, so Q4 is derived as the full year minus the first nine
months and marked `*`; derived EPS is approximate. And US filings have no
promoter category — for dual-class companies, look up the share classes and
voting split in the latest proxy (DEF 14A) and say so in the shareholding note,
because a 1% insider stake can still mean founder control.

Read the tables before researching the moves: results dates, a guidance cut or
a one-off tax item often explain two or three pins at once.

## Step 3 — Separate the market from the company

Each entry in `moves` and `moves_by_window` carries a `likely` label, computed
by comparing the day's move against its index (NIFTY 50, SENSEX, or S&P 500):

- `market-wide` — the index moved nearly as much in the same direction. There
  is usually no company story to find here, and hunting for one is how invented
  causes creep in. Say "fell with the broader market" and move on.
- `stock-specific` — the stock diverged from its index by two points or more.
  Something happened to this company. These are the days worth researching.
- `mixed` — sector or partial. Worth a look, lower priority.

`volume_vs_avg` (against the 60 sessions before that day, so a date reads the
same in every window) sharpens this further. A big move on twice the usual volume is
conviction — real money repricing the stock on real information. The same move
on thin volume is often noise, and worth flagging as such.

Lead your research with the `stock-specific` days. That is where the answer to
"why did it fall" actually lives.

## Step 4 — Find out what happened on those days

Search for each notable date. Timing matters more than people expect:

- A move on day D is usually caused by news from **D, or the evening of D-1**.
  Indian results and regulatory filings often land after the 3:30pm close and
  hit the price the next morning. US earnings land after the 4pm close and gap
  the stock overnight. Always search both days.
- **Check the earnings dates first.** Quarterly results are the single largest
  driver of single-day moves. One search for the results calendar often
  explains two or three pins at once.
- For `market-wide` days, search what the index did instead of what the company
  did — foreign investor flows, a Fed or RBI decision, a tariff headline, a
  global selloff.

`references/data-sources.md` lists which outlets to trust per market and the
search phrasings that work.

## Step 4b — Check the exchange filings

Run this on every report, and always before calling a day unexplained:

```bash
python <skill-dir>/scripts/fetch_filings.py \
  --history history.json --from <first date in history.json> --out filings.json
```

It pulls NSE corporate announcements for Indian tickers and SEC EDGAR for US
ones, then tells you, for each filing, **which trading session it could have
moved** and what that session actually did. It checks every date in
`moves_by_window`. Pass `--from` with the first date of the series so the
page's day details have filings for every day of the year, not only near the
big moves.

For Indian tickers it also pulls NSE's **bulk and block deals** and prints them
under each big day, grouped by client. A client that bought and sold about the
same amount is marked as trading (`churn`): high-frequency and arbitrage desks
such as Microcurves, NK Securities, Graviton and QE Securities show up on most
heavy-volume days. That accounts for volume, not direction, so never pin a move
on it. A one-sided deal (a fund buying 1% of a company, a promoter selling a
block) is real evidence. The portfolio page shows a one-line summary of each
day's deals under the move's detail.

**Heavy volume with no news is sometimes index rebalancing.** In a 33-stock
second pass it explained 3 of 47 heavy-volume days, and a single NSE Indices
press release (niftyindices.com, "Press_Release") named several portfolio stocks
at once, so check it early. Index funds trade at
the close on the day before an MSCI, FTSE, NSE or BSE index change takes effect
(MSCI: the last business day of Feb, May, Aug and Nov; FTSE: the third Friday of
Mar, Jun, Sep and Dec; NSE reviews: the end of March and September). When a
high-volume day falls on one of these, search for the stock's inclusion,
exclusion or weight change before calling it unexplained, and pin it only with a
dated source that names the stock.

Two reasons this earns its place on every run:

**Filings catch what the news misses.** A block deal, a promoter stake change,
a rating action, a plant shutdown or a court order is disclosed to the exchange
and frequently never reaches a news site. A day that looks blank in search is
often fully explained here.

**Filing times fix cause and effect.** Indian results routinely land around
19:00, hours after the 15:30 close. If a stock rallied on the filing date, it
rallied *before the news existed* — that was anticipation, and the real
reaction is the next session. Attributing the earlier move to the results
reverses cause and effect while reading perfectly plausibly, which is why it
survives review. The script credits after-close filings to the next session and
prints that session's move, so a results day that was really a shrug shows up
as one. Say so when it happens: "ran up 2.4% into the results, which landed
after the close; the reaction session was flat" is more accurate and more
interesting than "rose on results."

Be as sceptical of filings as of headlines. Large companies file constantly,
and most of it is housekeeping — dissolving a dormant subsidiary, a newspaper
publication, a trading-window notice. The script marks these `routine`, but
judge the content: a filing sitting near a big move is not the cause unless it
is big enough to be. Nothing produces a confident wrong answer faster than a
routine filing that happened to land on an interesting day.

**When you still cannot find a cause, say so.** Write "no clear catalyst —
moved with sector" or "no specific news or filing found for this date." This is
the single most important discipline in the skill. A plausible-sounding invented
reason is far worse than an admitted gap, because the reader cannot tell the
difference and will act on it. Real markets move on days when nothing is
reported, and an honest blank is a true statement about the market — one you can
now make on the strength of the primary record, not just a search that came up
short.

Attribute a cause only when a dated source ties that news to that day. If a
source is suggestive but not conclusive, hedge visibly — "reportedly", "analysts
linked the drop to" — rather than stating it flat.

## Step 5 — Write up what you found in research.json

Every report keeps its files in one folder per stock, inside a report folder:

```
<report>/portfolio.json        {"title": "...", "stocks": [{"dir": "NVDA", "name": "NVIDIA", "isin": "", "weight": null}]}
<report>/market_calendar.md    optional: "- YYYY-MM-DD: NIFTY -1.2%. Cause. (URL)" lines, shared by all stocks
<report>/NVDA/                 history.json, fundamentals.json, filings.json, research.json, swing_research.json
```

Use `<skill-dir>/portfolios/<ticker-lowercase>/` when that
folder is writable (then a later session can refresh the same report), and a
folder in the working directory otherwise. For one stock, `title` is the
company name plus "Share History". Fetch Steps 2, 2b and 4b into the stock's
folder.

Your research goes into `research.json`. This is the writing task; the script
handles presentation.

```json
{
  "pins": {
    "2026-07-08": {
      "title": "Fell with the market on US-Iran tension",
      "detail": "NIFTY dropped 2.04% the same session, its worst day since March.",
      "source": "https://example.com/article",
      "source_name": "Angel One"
    }
  },
  "no_catalyst": ["2026-07-21"],
  "verdicts": {"1": "...", "3": "...", "6": "...", "12": "..."},
  "things": [{"heading": "A short sentence ending in a period.", "body": "One sentence with numbers."}],
  "financial_points": {"quarterly": ["..."], "annual": ["..."], "shareholding": ["..."]},
  "caveats": ["Sources disagree on the direction of Q1 profit."],
  "sources": [{"name": "Angel One", "url": "https://..."}]
}
```

- `pins`: one per researched big day, keyed by date, so one pin serves every
  window that lists that day. Title under 60 characters; detail 1 to 3
  sentences with numbers; a real source link.
- **Leave a day out of `pins` when you found nothing, and list it under
  `no_catalyst`.** A vague sentence implies more certainty than you have.
  `no_catalyst` tells the build you actually searched; any listed day that is
  in neither gets printed by the build as unresearched, and publishing with that
  list non-empty would present unsearched days as searched.
- `verdicts`: one sentence per window, keyed "1", "3", "6", "12": the rounded %
  change, the index comparison and the main cause, for example "Up 11% in three
  months while the NIFTY fell 4%, as two analyst upgrades outweighed a soft Q2
  outlook." Take the numbers from the page's own stats (build once, or compute
  from history.json over the same window).
- `things`: exactly three things to know about the year.
- `financial_points`: about three plain observations per table.
- `caveats`: anything that did not reconcile — conflicting figures, a source
  whose numbers disagree with the price series, a claim you could not confirm.

## Step 5b — What the financial notes should say

`things` and `financial_points` connect the numbers to each other and to the
price: growth accelerating or fading, margins under pressure and why, one-off
items distorting profit (a tax benefit, an asset sale, gains on equity stakes),
promoter stake changes or pledging, FII/DII rotation, cash and buybacks, and
which results days drove the biggest moves. Every figure must come from
`fundamentals.json`, `history.json`, a filing, or a dated source; compute the
ratios yourself rather than recalling them.

They describe; they do not advise. No "undervalued", no "good entry point", no
price targets of your own, no forecast of next quarter beyond what management
guided. "The stock has traded on guidance rather than results" is an
observation; "the next results could be a catalyst" is a prediction — leave it
out.

## Step 5c — Explain the multi-day swings

```bash
python <skill-dir>/scripts/make_swing_brief.py <report>
```

This writes `swing_brief.md` in each stock folder: every multi-day swing the
page would shade, with each day's move, volume, filings, pinned days and market
notes inside it. Research them following `references/swing_research.md` and
write `swing_research.json` in the stock folder. A single stock usually has 4
to 8 swings; do them yourself. Only swings with a found reason are drawn.

## Step 6 — Build the page and publish it

```bash
python <skill-dir>/scripts/build_portfolio.py <report> --out report.html
```

The build prints anything still missing (unresearched days, verdicts, things to
know). Clear that list before publishing.

If authorized and supported, publish `report.html` using the available artifact
or hosting tool and give the user the link. Otherwise provide the generated
HTML file for download; do not assume that every runtime has an artifact API.

**Re-running the same stock later updates the same page.** Pass its URL as
`url` so the reader keeps one link per stock instead of collecting near-
duplicates — check with the artifact list if you do not have the URL to hand.
Never republish over a page the user asked you to leave alone.

When an HTML attachment is unavailable or the user wants an inline chart, use
`scripts/render_chart.py`, which produces a self-contained SVG with the same
numbered pins, and write the report as Markdown in chat.

## Step 7 — Summarise in chat

Three or four paragraphs and the link. Lead with the answer to what they
actually asked, name the one or two things that mattered most, say plainly
where you came up empty, and point out anything on the page they would not
think to look for — a longer window that tells a different story, a caveat that
changes how a number should be read.

## Several stocks on one page

When the user gives a list (a portfolio sheet, a watchlist, a screenshot of
holdings) and wants them together, build one searchable page instead of one
artifact per stock. It is the same page as a single-stock report, plus a
search bar (name, ticker or ISIN) that switches between stocks; each stock
keeps its own windows, pins, swings, financials and notes.

Each portfolio lives in `portfolios/<name>/` inside this skill folder, so it can
be extended in a later session:

```
portfolios/pms/portfolio.json     title + every stock: {"dir", "isin", "name", "weight"}
portfolios/pms/market_calendar.md market-wide causes already researched, shared by all stocks
portfolios/pms/NYKAA/             history.json, fundamentals.json, filings.json, brief.md, research.json
```

1. Resolve each stock (Step 1), then per stock run `fetch_history.py`,
   `fetch_fundamentals.py` and `fetch_filings.py` into its folder.
2. Research every listed date as usual and write each stock's `research.json`
   (Step 5) and `swing_research.json` (Step 5c). Market-wide days recur across stocks, so add each
   researched market cause to `market_calendar.md` and reuse it; spend the
   searches on company news. With several stocks, one research agent per stock
   working from a brief of its dates, filings and financials works well.
3. Build and publish:

```bash
python <skill-dir>/scripts/build_portfolio.py \
  <skill-dir>/portfolios/pms --out portfolio.html
```

The build picks the financial key rows and ownership bars from
`fundamentals.json` itself, and prints anything still missing per stock
(unresearched dates, verdicts, things to know). An entry in `portfolio.json`
with an empty `dir` is listed for later and left off the page. To add stocks
later, fill in their folders and `dir`, rebuild, and republish to the same
artifact URL so the link stays the same.

Every day on the chart has details, not only the pinned ones. Hovering shows
a tooltip, and a click, tap, drag or the arrow keys pick a day for the panel
under the chart. The panel shows the close, the change against the index,
volume against a typical day (the median of the previous 60 sessions, since
a mean is inflated by a few heavy days), any researched cause, the market
cause from `market_calendar.md`, company filings, and bulk or block deals.
The filings are grouped by the session they could move, so an after-close
filing appears on the next day. When a day has none of these, the panel says
so. For this to work, `fetch_filings.py` has to cover the whole series, so
run it with `--from <first date in history.json>` and not only the analysis
window.

The build also marks multi-day swings (`find_swings` in build_portfolio.py).
These are stretches of 3 to 20 sessions that moved at least 5 normal days'
worth, and at least 5%, without one day doing 60% or more of the move, and
without a bounce against the direction bigger than 35% of the move (so a
fall, rebound and second fall is not drawn as one slide). They
cover gradual slides and rallies that no single-day pin catches. They are
shaded on the chart and listed under All moves. Their text is computed: the
index over the same span, the biggest day inside, any results inside, and a
researched day inside that moved the same way, plus the company's news-like
filings from the start day through the end (paperwork such as con-call
notices and AGM papers is left out). A cause for the stretch as a whole comes
only from research: `scripts/make_swing_brief.py portfolios/<name>` writes
each stock's `swing_brief.md`, research agents (a few stocks each) follow
`references/swing_research.md` and write `swing_research.json` ({"swings": {"start/end": pin-like note},
"no_cause": [...]}), and the build attaches those notes by key. Swings found
after a data refresh have new keys, so rerun the brief and research those.

Like pins, a band is drawn only once research has found its reason; searched
and not-yet-researched swings stay off the chart. Each band carries a
"+x% · Why?" label: clicking it (or a pin) shows the reason in the panel
right under the chart, not in another tab, because the user could not find
the explanation when it lived only in the day details and the All moves tab.
A short key above the chart says what a pin and a band mean, with the pin
threshold for that stock.

To bring a portfolio up to date: back up each folder's history.json and
filings.json, rerun `fetch_history.py` (same symbol, `--months 3
--context-months 12`) and `fetch_filings.py --from <first date>` per stock,
then check the newest close against NSE's own record
(`/api/historicalOR/generateSecurityWiseHistoricalData`). Yahoo sometimes
leaves the latest day's close blank for a while; `fetch_series` fills it from
the quote's final price once that session is over. After a refresh:
- research any newly listed big days (the build's unresearched list);
- rerun `make_swing_brief.py`, which briefs only swings not yet researched
  (researched spans keep their place, so earlier research survives);
- rewrite the `verdicts`, because their % figures go stale as the windows move.
  Give an agent each old sentence with the old and new window numbers.

## Going deeper

If the user asks about one specific move, drop the report format and go
narrow: that day and the days around it, the full news trail, what management
said, how peers reacted. The report structure exists to answer an open
question, not to constrain a focused one.

To research a longer window rather than just view one, pass `--months 6` or
`--months 12`; `--context-months` controls how much history the page carries
behind the switcher.

## Boundaries

Explain what happened and why it was reported to have happened. Do not tell the
user whether to buy, sell, or hold, do not forecast a target price, and do not
assess whether the stock is cheap — that is licensed financial advice, and the
honest version of this skill is a history, not a recommendation. If asked
directly, say plainly that you can lay out what happened and what analysts have
publicly said, but the call is theirs.

Close every report with a one-line note that it covers past prices and reported
reasons only and is not investment advice.
