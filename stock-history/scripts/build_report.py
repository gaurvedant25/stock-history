#!/usr/bin/env python3
"""Build an interactive HTML stock report from history.json + annotations.json.

    python build_report.py history.json annotations.json --fundamentals fundamentals.json --out report.html

The output is a single self-contained page, ready to publish as an artifact.
It carries a year of price data so the reader can switch windows without a
refetch, overlays the index on demand, and expands your researched note when a
pin is clicked.

annotations.json is what you write after researching the moves:

    {
      "verdict": "One sentence naming the main cause.",
      "pins": {
        "2026-07-08": {
          "title": "Fell with the market",
          "detail": "NIFTY dropped 2.04% the same session on US-Iran tension.",
          "source": "https://example.com/article",
          "source_name": "Angel One"
        }
      },
      "months": {"2026-07": "Flat overall, but volatile."},
      "themes": [{"heading": "Crude and O2C", "body": "..."}],
      "caveats": ["Sources disagree on the profit direction."],
      "sources": [{"name": "Angel One", "url": "https://..."}],
      "no_catalyst": ["2026-07-21"],
      "financial_notes": {"quarterly": "...", "annual": "...", "shareholding": "..."},
      "insights": [{"heading": "Growth is slowing", "body": "..."}]
    }

--fundamentals takes fetch_fundamentals.py's output and fills the Quarterly
results, Profit & loss and Shareholding pattern drop-downs; "financial_notes"
puts a short reading above each table, and "insights" fills the fourth
drop-down. A drop-down with nothing behind it is left off the page.

Only "pins" is required; every other key is optional and its section is left
out when absent, so a quiet stock does not get padded with empty headings.
A date with no pin entry simply has no marker -- unresearched days are never
presented as though they were explained.

Each window on the switcher (1M/3M/6M/1Y) lists its own biggest days from
history.json's "moves_by_window", so pins are needed across all of them, not
just the analysis window. "no_catalyst" records days you searched and found
nothing for; the build lists any other unpinned day so none slips through
unresearched.
"""

import argparse
import json
import os
import re

TEMPLATE = r"""<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&display=swap">
<style>
:root{
  --ground:#f6f7f9; --surface:#fff; --raised:#fbfcfd;
  --ink:#151b23; --muted:#5c6875; --faint:#8a95a1;
  --line:#e3e7ec; --line-strong:#cfd6de;
  --accent:#2f5d8a; --accent-soft:#eaf0f7;
  --up:#0f7a4d; --down:#c03a2f;
  --up-soft:#e7f4ed; --down-soft:#fbecea;
  --serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#0d1117; --surface:#151b23; --raised:#1a212b;
  --ink:#e6eaef; --muted:#93a0ae; --faint:#6e7b8a;
  --line:#252d38; --line-strong:#333d4a;
  --accent:#7aaede; --accent-soft:#18242f;
  --up:#3fbf83; --down:#ff6f61;
  --up-soft:#12271e; --down-soft:#2a1715;
}}
:root[data-theme="dark"]{
  --ground:#0d1117; --surface:#151b23; --raised:#1a212b;
  --ink:#e6eaef; --muted:#93a0ae; --faint:#6e7b8a;
  --line:#252d38; --line-strong:#333d4a;
  --accent:#7aaede; --accent-soft:#18242f;
  --up:#3fbf83; --down:#ff6f61;
  --up-soft:#12271e; --down-soft:#2a1715;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding-block:28px 56px;padding-left:16px;padding-right:16px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}

.eyebrow{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--faint);font-weight:500}
h1{font-family:var(--serif);font-weight:600;font-size:30px;line-height:1.15;
  margin:6px 0 0;letter-spacing:-.01em;text-wrap:balance}
h2{font-family:var(--serif);font-weight:600;font-size:19px;margin:0 0 14px;letter-spacing:-.005em}
h3{font-size:14px;font-weight:600;margin:0 0 4px}

.head{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:26px}
.price-row{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-top:12px}
.price{font-family:var(--mono);font-size:34px;font-weight:500;letter-spacing:-.02em}
.delta{font-family:var(--mono);font-size:17px;font-weight:500}
.verdict{margin:14px 0 0;font-size:16px;color:var(--ink);max-width:62ch}
.facts{display:flex;gap:22px;flex-wrap:wrap;margin-top:16px}
.fact{min-width:88px}
.fact .k{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--faint)}
.fact .v{font-family:var(--mono);font-size:14px;font-variant-numeric:tabular-nums;margin-top:2px}

.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:16px;margin-bottom:26px}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.chips{display:flex;gap:4px;background:var(--ground);border:1px solid var(--line);
  border-radius:7px;padding:3px}
.chip{font:inherit;font-size:12px;font-weight:500;color:var(--muted);background:none;
  border:0;border-radius:5px;padding:5px 11px;cursor:pointer;font-family:var(--mono)}
.chip:hover{color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--surface);color:var(--ink);
  box-shadow:0 1px 2px rgba(0,0,0,.07)}
.toggle{font:inherit;font-size:12px;font-weight:500;color:var(--muted);
  background:var(--ground);border:1px solid var(--line);border-radius:7px;
  padding:7px 12px;cursor:pointer;margin-left:auto}
.toggle:hover{color:var(--ink);border-color:var(--line-strong)}
.toggle[aria-pressed="true"]{background:var(--accent-soft);color:var(--accent);
  border-color:var(--accent)}
.chip:focus-visible,.toggle:focus-visible,.move:focus-visible{outline:2px solid var(--accent);
  outline-offset:2px}
.chart-box{position:relative;height:320px}
@media (max-width:560px){.chart-box{height:250px}}
.axis-note{font-size:11px;color:var(--faint);margin-top:8px;font-family:var(--mono)}

.moves{display:flex;flex-direction:column;gap:0}
.move{display:grid;grid-template-columns:26px 74px 62px 1fr;gap:12px;align-items:center;
  width:100%;text-align:left;font:inherit;color:inherit;background:none;cursor:pointer;
  border:0;border-top:1px solid var(--line);padding:11px 4px}
.moves .move:first-child{border-top:0}
.move:hover{background:var(--raised)}
.move[aria-expanded="true"]{background:var(--raised)}
.badge{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;
  font-family:var(--mono);font-size:11px;font-weight:500;color:#fff}
.badge.up{background:var(--up)} .badge.down{background:var(--down)}
.m-date{font-family:var(--mono);font-size:13px;color:var(--muted)}
.m-pct{font-family:var(--mono);font-size:13px;font-weight:500;font-variant-numeric:tabular-nums}
.m-title{font-size:14px}
.m-title .unex{color:var(--faint);font-style:italic}
.detail{padding:0 4px 14px 54px;font-size:14px;color:var(--muted);max-width:66ch}
.detail p{margin:0 0 8px}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.tag{font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;
  padding:3px 7px;border-radius:4px;background:var(--ground);color:var(--muted);
  border:1px solid var(--line)}
.tag.spec{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
a{color:var(--accent)}
a:hover{text-decoration-thickness:2px}

.months{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.mo{background:var(--surface);padding:12px 14px}
.mo .k{font-family:var(--mono);font-size:11px;color:var(--faint);letter-spacing:.05em}
.mo .v{font-family:var(--mono);font-size:16px;font-weight:500;margin:2px 0 6px}
.mo .n{font-size:13px;color:var(--muted);line-height:1.5}

.theme-block{margin-bottom:18px;max-width:68ch}
.theme-block p{margin:0;color:var(--muted)}
.caveats{border-left:2px solid var(--line-strong);padding-left:14px;margin:0;
  list-style:none;max-width:68ch}
.caveats li{color:var(--muted);font-size:14px;margin-bottom:8px}
.foot{border-top:1px solid var(--line);margin-top:34px;padding-top:16px;
  font-size:12.5px;color:var(--faint);max-width:68ch}
.foot .src{margin-bottom:8px}
.disc{font-style:italic}

.dd{border-top:1px solid var(--line)}
.dd:first-of-type{border-top:0}
.dd summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:10px;
  padding:13px 4px;font-size:15px;font-weight:600;border-radius:6px}
.dd summary::-webkit-details-marker{display:none}
.dd summary::after{content:"";margin-left:auto;align-self:center;flex:none;width:7px;height:7px;
  border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);
  transform:translateY(-2px) rotate(45deg);transition:transform .15s ease-out}
.dd[open] summary::after{transform:translateY(2px) rotate(-135deg)}
.dd summary:hover{background:var(--raised)}
.dd summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.dd-s{font-family:var(--mono);font-size:11.5px;font-weight:400;color:var(--faint)}
@media (max-width:460px){.dd-s{display:none}}
.dd-b{padding:2px 4px 18px}
.freading{font-size:14px;color:var(--muted);margin:0 0 12px;max-width:68ch}
.tscroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;
  -webkit-overflow-scrolling:touch}
.ftable{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
.ftable th,.ftable td{padding:7px 12px;text-align:right;white-space:nowrap;
  border-bottom:1px solid var(--line)}
.ftable tbody tr:last-child td{border-bottom:0}
.ftable thead th{font-family:var(--mono);font-size:11px;font-weight:500;color:var(--faint);
  background:var(--raised)}
.ftable td{font-family:var(--mono);color:var(--ink)}
.ftable th:first-child,.ftable td:first-child{text-align:left;position:sticky;left:0;z-index:1;
  font-family:var(--sans);background:var(--surface);border-right:1px solid var(--line)}
.ftable thead th:first-child{background:var(--raised)}
.ftable tr.pct td{color:var(--muted)}
.ftable tr.pct td.up{color:var(--up)} .ftable tr.pct td.down{color:var(--down)}
.fnote{font-size:12px;color:var(--faint);margin:8px 0 0;max-width:68ch}
.subh{margin:18px 0 8px;font-size:13px}
.insights{margin:0;padding-left:18px;max-width:68ch}
.insights li{color:var(--muted);font-size:14px;margin-bottom:10px}
.insights strong{color:var(--ink);font-weight:600}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>

<div class="wrap">
  <header class="head">
    <div class="eyebrow" id="exch"></div>
    <h1 id="name"></h1>
    <div class="price-row">
      <span class="price" id="price"></span>
      <span class="delta" id="delta"></span>
      <span class="eyebrow" id="winlabel"></span>
    </div>
    <p class="verdict" id="verdict"></p>
    <div class="facts" id="facts"></div>
  </header>

  <section class="panel">
    <div class="bar">
      <div class="chips" role="group" aria-label="Time window">
        <button class="chip" data-win="1" aria-pressed="false">1M</button>
        <button class="chip" data-win="3" aria-pressed="true">3M</button>
        <button class="chip" data-win="6" aria-pressed="false">6M</button>
        <button class="chip" data-win="12" aria-pressed="false">1Y</button>
      </div>
      <button class="toggle" id="ovl" aria-pressed="false"></button>
    </div>
    <div class="chart-box"><canvas id="c" role="img" aria-label="Price history chart. The moves table below lists the same data as text."></canvas></div>
    <div class="axis-note" id="axisnote"></div>
  </section>

  <section class="panel">
    <h2>What moved it</h2>
    <div class="moves" id="moves"></div>
    <p class="axis-note" id="pinnote"></p>
  </section>

  <section class="panel" id="finwrap" hidden>
    <h2>Company financials</h2>
    <details class="dd" id="dd-quarterly" hidden><summary><span>Quarterly results</span><span class="dd-s"></span></summary><div class="dd-b"></div></details>
    <details class="dd" id="dd-annual" hidden><summary><span>Profit &amp; loss</span><span class="dd-s"></span></summary><div class="dd-b"></div></details>
    <details class="dd" id="dd-holding" hidden><summary><span>Shareholding pattern</span><span class="dd-s"></span></summary><div class="dd-b"></div></details>
    <details class="dd" id="dd-insights" hidden><summary><span>Insights</span><span class="dd-s"></span></summary><div class="dd-b"></div></details>
  </section>

  <section id="monthwrap" hidden>
    <h2>Month by month</h2>
    <div class="months" id="months"></div>
  </section>

  <section id="themewrap" hidden style="margin-top:26px">
    <h2>Why it moved</h2>
    <div id="themes"></div>
  </section>

  <section id="cavwrap" hidden style="margin-top:26px">
    <h2>Worth knowing</h2>
    <ul class="caveats" id="caveats"></ul>
  </section>

  <footer class="foot">
    <div class="src" id="sources"></div>
    <div id="stamp"></div>
    <div class="disc">Past prices and reported reasons only. Not investment advice.</div>
  </footer>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const DATA = __DATA__;
const NOTE = __ANNOTATIONS__;
const FUND = __FUNDAMENTALS__ || {};

const S = DATA.series, META = DATA.meta, ST = DATA.stats, BM = DATA.benchmark || {};
const CUR = {INR:"₹", USD:"$", EUR:"€", GBP:"£", JPY:"¥"}[META.currency] || "";
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
let win = META.months || 3, overlay = false, open = null, chart = null;

const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const money = v => CUR + v.toLocaleString(undefined,
  {minimumFractionDigits: v >= 1000 ? 0 : 2, maximumFractionDigits: v >= 1000 ? 0 : 2});
const sign = v => (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const fdate = d => { const p = d.split("-"); return +p[2] + " " + MON[+p[1]-1] + " " + p[0]; };
const short = d => { const p = d.split("-"); return +p[2] + " " + MON[+p[1]-1]; };

function cutoff(months){
  const last = S[S.length-1].date.split("-").map(Number);
  let y = last[0], m = last[1] - months, d = last[2];
  while (m <= 0) { m += 12; y -= 1; }
  // Clamp to a real day: an unclamped "2026-02-31" sorts above every February
  // date and would silently crop a month off the window.
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  d = Math.min(d, [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m-1]);
  return y + "-" + String(m).padStart(2,"0") + "-" + String(d).padStart(2,"0");
}
const slice = () => { const c = cutoff(win); return S.filter(r => r.date >= c); };
// Each window carries its own biggest days, so 1Y shows the year's big moves
// rather than the analysis window's list filtered down to nothing new.
const BYWIN = DATA.moves_by_window || {};
const listFor = () => BYWIN[String(win)] || (DATA.moves || []).filter(m => m.date >= cutoff(win));
const pinnedIn = list => new Set(list.filter(m => NOTE.pins && NOTE.pins[m.date]).map(m => m.date));
const span = () => win === 12 ? "the past year" : win === 1 ? "the past month" : "the past " + win + " months";

function compute(rows){
  const cl = rows.map(r => r.close);
  const hi = Math.max(...cl), lo = Math.min(...cl);
  const chg = (cl[cl.length-1] - cl[0]) / cl[0] * 100;
  const b = rows.filter(r => r.bench != null).map(r => r.bench);
  const bchg = b.length > 1 ? (b[b.length-1] - b[0]) / b[0] * 100 : null;
  return {
    chg, abs: cl[cl.length-1] - cl[0], hi, lo, bchg,
    hiD: rows[cl.indexOf(hi)].date, loD: rows[cl.indexOf(lo)].date
  };
}

function header(){
  const rows = slice(), k = compute(rows);
  document.getElementById("exch").textContent = META.symbol + " · " + (META.exchange || "");
  document.getElementById("name").textContent = META.name || META.symbol;
  document.getElementById("price").textContent = money(S[S.length-1].close);
  const d = document.getElementById("delta");
  d.textContent = sign(k.chg) + "  (" + (k.abs >= 0 ? "+" : "−") + money(Math.abs(k.abs)) + ")";
  d.className = "delta " + (k.chg >= 0 ? "up" : "down");
  document.getElementById("winlabel").textContent = "over " + (win === 12 ? "1 year" : win === 1 ? "1 month" : win + " months");

  const f = [
    ["High", money(k.hi) + "  " + short(k.hiD)],
    ["Low", money(k.lo) + "  " + short(k.loD)]
  ];
  if (k.bchg != null) {
    f.push([BM.name || "Index", sign(k.bchg)]);
    f.push(["Vs index", sign(k.chg - k.bchg)]);
  }
  document.getElementById("facts").innerHTML = f.map(([a,b]) =>
    '<div class="fact"><div class="k">' + a + '</div><div class="v">' + b + "</div></div>").join("");
}

function draw(){
  const rows = slice();
  const base = rows[0].close, bbase = (rows.find(r => r.bench != null) || {}).bench;
  const up = compute(rows).chg >= 0;
  const cUp = tok("--up"), cDown = tok("--down"), cAcc = tok("--accent");
  const cLine = tok("--line"), cMute = tok("--muted"), cSurf = tok("--surface"), cInk = tok("--ink");
  const tone = up ? cUp : cDown;

  const priceVals = rows.map(r => overlay ? r.close / base * 100 : r.close);
  // Mark only this window's listed days, so every marker opens a row below.
  const listed = pinnedIn(listFor());
  const pins = rows.map(r => listed.has(r.date) ? 6 : 0);
  const pinCol = rows.map((r,i) => {
    if (!pins[i]) return "transparent";
    const prev = rows[i-1];
    return prev && r.close < prev.close ? cDown : cUp;
  });

  const ds = [{
    label: META.symbol, data: priceVals, borderColor: tone, borderWidth: 2,
    tension: .15, pointRadius: pins, pointHoverRadius: rows.map(p => 6),
    pointBackgroundColor: pinCol, pointBorderColor: cSurf, pointBorderWidth: 2,
    fill: { target: "origin", above: (up ? cUp : cDown) + "14" }, order: 1
  }];
  if (overlay && bbase) {
    ds.push({
      label: BM.name || "Index", data: rows.map(r => r.bench != null ? r.bench / bbase * 100 : null),
      borderColor: cAcc, borderWidth: 1.5, borderDash: [5,4], tension: .15,
      pointRadius: 0, pointHoverRadius: 0, fill: false, spanGaps: true, order: 2
    });
  }

  if (chart) chart.destroy();
  chart = new Chart(document.getElementById("c"), {
    type: "line",
    data: { labels: rows.map(r => r.date), datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      onClick: (e, els) => { if (els.length) { const r = rows[els[0].index];
        if (listed.has(r.date)) expand(r.date, true); } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cSurf, titleColor: cInk, bodyColor: cMute,
          borderColor: cLine, borderWidth: 1, padding: 10, displayColors: false,
          titleFont: { family: "IBM Plex Mono", size: 12, weight: "500" },
          bodyFont: { family: "IBM Plex Mono", size: 12 },
          callbacks: {
            title: it => fdate(it[0].label),
            label: it => {
              const i = it.dataIndex, r = rows[i];
              if (it.datasetIndex === 1) return (BM.name || "Index") + "  " + it.raw.toFixed(1);
              const out = [money(r.close)];
              if (i > 0) out.push(sign((r.close - rows[i-1].close) / rows[i-1].close * 100));
              if (r.volume) out.push((r.volume/1e6).toFixed(1) + "M shares");
              const n = NOTE.pins && NOTE.pins[r.date];
              if (n) out.push("• " + n.title);
              return out.join("   ");
            }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, border: { color: cLine },
          ticks: { color: cMute, font: { family: "IBM Plex Mono", size: 11 },
            maxRotation: 0, autoSkipPadding: 28, callback: function(v){ return short(this.getLabelForValue(v)); } } },
        y: { grid: { color: cLine }, border: { display: false },
          ticks: { color: cMute, font: { family: "IBM Plex Mono", size: 11 }, maxTicksLimit: 6,
            callback: v => overlay ? v.toFixed(0) : money(v) } }
      }
    }
  });

  document.getElementById("axisnote").textContent = overlay
    ? "Both lines indexed to 100 at the start of the window, so the gap between them is the lag or lead."
    : "Closing price. Markers are days with a researched explanation — click one.";
}

function expand(date, scroll){
  open = open === date ? null : date;
  moves();
  if (scroll && open) {
    const el = document.querySelector('[data-d="' + open + '"]');
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function moves(){
  const list = listFor();
  const host = document.getElementById("moves");
  const explained = pinnedIn(list).size;
  document.getElementById("pinnote").textContent = list.length
    ? "The " + list.length + " biggest single-day moves over " + span() + ", " + explained +
      " with a researched explanation. Days are flagged when they diverge from " +
      (BM.name || "the index") + " or move on unusual volume; a day that moved with the whole " +
      "market has no company story to find."
    : "";
  if (!list.length) {
    host.innerHTML = '<p class="axis-note">No big moves fall inside this window.</p>';
    return;
  }
  host.innerHTML = list.map((m, i) => {
    const n = (NOTE.pins || {})[m.date];
    const isOpen = open === m.date;
    const dir = m.change_pct >= 0 ? "up" : "down";
    let h = '<button class="move" data-d="' + m.date + '" aria-expanded="' + isOpen + '">' +
      '<span class="badge ' + dir + '">' + (i+1) + "</span>" +
      '<span class="m-date">' + short(m.date) + "</span>" +
      '<span class="m-pct ' + dir + '">' + sign(m.change_pct) + "</span>" +
      '<span class="m-title">' + (n ? n.title :
        '<span class="unex">No clear catalyst found</span>') + "</span></button>";
    if (isOpen) {
      let d = '<div class="detail">';
      d += "<p>" + (n && n.detail ? n.detail :
        "Checked against both the news record and the exchange's own filings for this session — neither explains the move. Markets do move on days when nothing is disclosed, and an honest blank beats an invented cause.") + "</p>";
      const tags = [];
      if (m.likely === "market-wide") tags.push('<span class="tag">Moved with the market</span>');
      if (m.likely === "stock-specific") tags.push('<span class="tag spec">Company-specific</span>');
      if (m.likely === "mixed") tags.push('<span class="tag">Partly sector</span>');
      if (m.benchmark_pct != null) tags.push('<span class="tag">' + (BM.name || "Index") +
        " " + sign(m.benchmark_pct) + "</span>");
      if (m.volume_vs_avg) tags.push('<span class="tag">' + m.volume_vs_avg + "× avg volume</span>");
      if (n && n.source) tags.push('<a class="tag" href="' + n.source + '" target="_blank" rel="noopener">' +
        (n.source_name || "Source") + " ↗</a>");
      d += '<div class="tags">' + tags.join("") + "</div></div>";
      h += d;
    }
    return h;
  }).join("");
  host.querySelectorAll(".move").forEach(b =>
    b.addEventListener("click", () => expand(b.dataset.d, false)));
}

function statics(){
  document.getElementById("verdict").textContent = NOTE.verdict || "";
  const mo = DATA.monthly || [], notes = NOTE.months || {};
  if (mo.length) {
    document.getElementById("monthwrap").hidden = false;
    document.getElementById("months").innerHTML = mo.map(m => {
      const p = m.month.split("-");
      return '<div class="mo"><div class="k">' + MON[+p[1]-1] + " " + p[0] + "</div>" +
        '<div class="v ' + (m.change_pct >= 0 ? "up" : "down") + '">' + sign(m.change_pct) + "</div>" +
        (notes[m.month] ? '<div class="n">' + notes[m.month] + "</div>" : "") + "</div>";
    }).join("");
  }
  if ((NOTE.themes || []).length) {
    document.getElementById("themewrap").hidden = false;
    document.getElementById("themes").innerHTML = NOTE.themes.map(t =>
      '<div class="theme-block"><h3>' + t.heading + "</h3><p>" + t.body + "</p></div>").join("");
  }
  if ((NOTE.caveats || []).length) {
    document.getElementById("cavwrap").hidden = false;
    document.getElementById("caveats").innerHTML = NOTE.caveats.map(c => "<li>" + c + "</li>").join("");
  }
  const srcs = NOTE.sources || [];
  document.getElementById("sources").innerHTML = srcs.length
    ? "News sources: " + srcs.map(s => '<a href="' + s.url + '" target="_blank" rel="noopener">' +
        s.name + "</a>").join(" · ") : "";
  document.getElementById("stamp").textContent =
    "Prices from " + (META.source || "Yahoo Finance") + ", fetched " + META.fetched_at +
    ". Volume is compared with the average of the 60 sessions before each day.";
}

// Company financials: fetched tables (FUND) plus your written reading (NOTE).
// Table labels and holder names come from third-party pages, so escape them.
const LOC = META.currency === "INR" ? "en-IN" : "en-US";
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function fmt(v, kind, dec){
  if (v == null) return "—";
  if (kind === "text") return esc(v);
  if (kind === "pct") return v.toLocaleString(LOC, {minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0}) + "%";
  if (kind === "eps") return v.toLocaleString(LOC, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (kind === "count") return Math.round(v).toLocaleString(LOC);
  return v.toLocaleString(LOC, {minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0});
}
function ftable(t){
  // One decimal across the table if any figure needs it (USD millions), none
  // for whole numbers (Screener's crore), so a column never mixes the two.
  const dec = t.rows.some(r => r.kind === "money" && r.values.some(v => v != null && v % 1)) ? 1 : 0;
  // Percent rows keep as many decimals as their most precise cell (max 2).
  const places = v => { const s = String(v), i = s.indexOf("."); return i < 0 ? 0 : Math.min(2, s.length - i - 1); };
  const head = "<tr><th></th>" + t.columns.map(c => "<th>" + esc(c) + "</th>").join("") + "</tr>";
  const body = t.rows.map(r => {
    const growth = /growth/i.test(r.label);
    const d = r.kind === "pct" ? Math.max(0, ...r.values.filter(v => v != null).map(places)) : dec;
    return '<tr class="' + r.kind + '"><td>' + esc(r.label) + "</td>" + r.values.map(v =>
      "<td" + (growth && v != null ? ' class="' + (v >= 0 ? "up" : "down") + '"' : "") + ">" +
      fmt(v, r.kind, d) + "</td>").join("") + "</tr>";
  }).join("");
  return '<div class="tscroll"><table class="ftable"><thead>' + head + "</thead><tbody>" +
    body + "</tbody></table></div>";
}
function financials(){
  const FN = NOTE.financial_notes || {}, src = FUND.sources || {};
  let any = false;
  const fill = (id, sub, html) => {
    const d = document.getElementById(id);
    d.hidden = false; any = true;
    d.querySelector(".dd-s").textContent = sub;
    d.querySelector(".dd-b").innerHTML = html;
    // Newest figures sit on the right; show them first on a narrow screen.
    d.addEventListener("toggle", () => d.querySelectorAll(".tscroll").forEach(s => s.scrollLeft = s.scrollWidth));
  };
  const block = (key, t, source, extra) =>
    (FN[key] ? '<p class="freading">' + FN[key] + "</p>" : "") + ftable(t) + (extra || "") +
    t.notes.map(n => '<p class="fnote">' + esc(n) + "</p>").join("") +
    (source ? '<p class="fnote">Source: <a href="' + esc(source.url) + '" target="_blank" rel="noopener">' +
      esc(source.name) + " ↗</a></p>" : "");
  const unit = FUND.unit ? " · " + FUND.unit : "";
  if (FUND.quarterly) fill("dd-quarterly", "Last " + FUND.quarterly.columns.length + " quarters" + unit,
    block("quarterly", FUND.quarterly, src.results));
  if (FUND.annual) fill("dd-annual", FUND.annual.columns.length + " years" + unit,
    block("annual", FUND.annual, src.results));
  const sh = FUND.shareholding;
  if (sh) {
    const hs = sh.holders || [];
    const top = hs.length ? '<h3 class="subh">Largest institutional holders</h3>' + ftable({
      columns: ["% held", "As of"],
      rows: hs.map(h => ({label: h.name, kind: "text", values: [fmt(h.pct, "pct", 2), h.as_of]}))
    }) : "";
    fill("dd-holding", sh.columns.length > 1 ? sh.columns.length + " quarters" : "Latest filings",
      block("shareholding", sh, src.shareholding, top));
  }
  const ins = NOTE.insights || [];
  if (ins.length) fill("dd-insights", ins.length + " observations", '<ul class="insights">' +
    ins.map(i => "<li>" + (typeof i === "string" ? i : "<strong>" + i.heading + ".</strong> " + i.body) +
      "</li>").join("") + "</ul>");
  document.getElementById("finwrap").hidden = !any;
}

function setOverlay(){
  const b = document.getElementById("ovl");
  b.textContent = (overlay ? "Hide " : "Compare ") + (BM.name || "index");
  b.setAttribute("aria-pressed", String(overlay));
  // Hide the control outright when there is no index series behind it, rather
  // than leaving a button that looks live and does nothing.
  b.hidden = !BM.name || !S.some(r => r.bench != null);
}

document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => {
  win = +b.dataset.win; open = null;
  document.querySelectorAll(".chip").forEach(x =>
    x.setAttribute("aria-pressed", String(x === b)));
  header(); draw(); moves();
}));
document.getElementById("ovl").addEventListener("click", () => {
  overlay = !overlay; setOverlay(); draw();
});

const repaint = () => { if (chart) draw(); };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", repaint);
new MutationObserver(repaint).observe(document.documentElement,
  { attributes: true, attributeFilter: ["data-theme"] });

document.querySelectorAll(".chip").forEach(b =>
  b.setAttribute("aria-pressed", String(+b.dataset.win === win)));
statics(); setOverlay(); header(); draw(); moves(); financials();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("history", help="JSON from fetch_history.py")
    ap.add_argument("annotations", nargs="?", help="your researched notes (optional)")
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--title", help="page title; defaults to the company name")
    ap.add_argument("--fundamentals", help="JSON from fetch_fundamentals.py (fills the "
                                           "results, P&L and shareholding drop-downs)")
    args = ap.parse_args()

    with open(args.history, encoding="utf-8") as fh:
        data = json.load(fh)
    note = {}
    if args.annotations and os.path.exists(args.annotations):
        with open(args.annotations, encoding="utf-8") as fh:
            note = json.load(fh)
    fund = None
    if args.fundamentals:
        with open(args.fundamentals, encoding="utf-8") as fh:
            fund = json.load(fh)

    meta = data["meta"]
    if data.get("series"):
        # Derive the lists from the carried series rather than trusting stored
        # ones, so older files and changes to which days count as big both
        # apply at build time without a refetch.
        from fetch_history import moves_by_window
        months = meta.get("months", 3)
        data["moves_by_window"] = moves_by_window(data["series"], meta.get("context_months", 12), months)
        data["moves"] = data["moves_by_window"][str(months)]

    name = meta.get("name") or meta.get("symbol") or "Stock"
    # Strip the corporate suffix so the gallery shows "Alphabet Share History"
    # rather than "Alphabet Inc. Share History".
    name = re.sub(r"[\s,]+(Limited|Ltd\.?|Inc\.?|Corporation|Corp\.?|Company|Co\.?|"
                  r"plc|PLC|S\.A\.|N\.V\.|AG|SE|Holdings?|Group)$", "", name).strip(" ,.")
    title = args.title or (name + " Share History")

    # Scraped labels land inside a <script>; "</" there would end it early.
    def embed(obj):
        return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")

    html = (TEMPLATE
            .replace("__TITLE__", title)
            .replace("__DATA__", embed(data))
            .replace("__ANNOTATIONS__", embed(note))
            .replace("__FUNDAMENTALS__", embed(fund)))

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)

    pins = note.get("pins", {})
    searched = set(note.get("no_catalyst", []))
    by_window = data.get("moves_by_window") or {str(meta.get("months", 3)): data.get("moves", [])}
    print("wrote " + args.out + "  (" + str(len(html) // 1024) + " KB)")
    print("title: " + title)
    blank = set()
    for w, ms in by_window.items():
        dates = [m["date"] for m in ms]
        missing = [d for d in dates if d not in pins]
        blank.update(d for d in missing if d not in searched)
        label = "1Y" if w == "12" else w + "M"
        print("  %-3s %d of %d moves annotated" % (label, len(dates) - len(missing), len(dates)))
    if blank:
        # The page renders every unpinned listed day as "no clear catalyst
        # found", which is only true once the day has actually been searched.
        print("\nShown as 'no clear catalyst found' but not yet researched -- search each one, "
              "then pin it or list it under \"no_catalyst\" in annotations.json:")
        print("  " + ", ".join(sorted(blank)))

    sections = [("Quarterly results", fund and fund.get("quarterly")),
                ("Profit & loss", fund and fund.get("annual")),
                ("Shareholding pattern", fund and fund.get("shareholding")),
                ("Insights", note.get("insights"))]
    print("\ndrop-downs: " + ", ".join(n + (" yes" if ok else " --") for n, ok in sections))
    if fund and not note.get("insights"):
        print("  Insights is empty -- write them into annotations.json (see SKILL.md, step 5b).")


if __name__ == "__main__":
    main()
