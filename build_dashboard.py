#!/usr/bin/env python3
"""Generate the internal Ops cost dashboard as a self-contained static HTML page.

Reads the live Ops base (Spend_Variable + Processes), attributes every cost to a brand,
and bakes ONE page with a brand toggle (All + one chip per brand, abbreviated).
Per brand it computes monthly spend, a PLAIN 14-day baseline with a >=2x balloon flag, a
trend chart, coverage, and a freshness stamp. All views are pre-rendered server-side (so the
balloon math lives in Python); the toggle just shows/hides them. Stdlib only.

  python3 build_dashboard.py
"""
import os
import re
import sys
import json
import html
import datetime as dt
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def _brand_map():
    """Full-name -> abbreviation map, loaded from OUTSIDE committed source: the BRAND_MAP env
    secret in CI, or the gitignored .env locally. Keeps spelled-out brand names out of the repo.
    Brand names are abbreviated in the rendered page (paired with a noindex tag); fail CLOSED
    (main() refuses to render if this is missing, so full names can never leak)."""
    raw = os.environ.get("BRAND_MAP")
    if not raw:
        p = os.path.join(HERE, ".env")
        if os.path.exists(p):
            for line in open(p):
                if line.strip().startswith("BRAND_MAP="):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


BRAND_ABBR = _brand_map()
# preferred brand order in the toggle = the map's order (abbreviations only; no full names in source)
KNOWN_BRANDS = list(dict.fromkeys(BRAND_ABBR.values()))
# fallback attribution for any row/process that somehow lacks a Brand (masked keys, abbreviations)
PROC_BRAND = {
    "lv-carousel": "LV", "lv-crosspost": "LV",
    "brand-voice-generator": "LV", "content-ledger": "LV",
    "slot-watch": "LV", "tiktok-trends": "LV", "trends-tiktok": "LV",
    "fan-carousel": "Fan",
}


def _mask_brand(b):
    return BRAND_ABBR.get(b, b)


def _mask_proc(name):
    """Strip spelled-out brand names (from the loaded map) out of a process name."""
    s = name or ""
    for full, ab in BRAND_ABBR.items():
        words = [re.escape(w) for w in re.split(r"[ _-]+", full) if w]
        if words:
            s = re.sub("(?i)" + r"[-_ ]?".join(words), ab.lower(), s)
    return s


def _load_ops():
    """Ops Airtable creds. Local: the .env file. Cloud (GitHub Actions): env-var secrets."""
    d = {}
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"')
    for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"):
        if not d.get(k) and os.environ.get(k):
            d[k] = os.environ[k]
    return d


def _fetch(table, key, base):
    rows, off = [], None
    while True:
        u = f"https://api.airtable.com/v0/{base}/{table}?pageSize=100" + (f"&offset={off}" if off else "")
        r = urllib.request.Request(u, headers={"Authorization": f"Bearer {key}"})
        d = json.loads(urllib.request.urlopen(r).read())
        rows += [x["fields"] for x in d["records"]]
        off = d.get("offset")
        if not off:
            return rows


def _money(x):
    if not x:
        return "$0"
    if x < 0.01:
        return f"${x:.4f}"
    if x < 1:
        return f"${x:.2f}"
    return f"${x:,.2f}"


def _brand_of(f):
    return f.get("Brand") or PROC_BRAND.get(f.get("Process")) or "Unattributed"


def compute(sv, processes, today):
    """Per-process spend + 14-day balloon flags over the given (already brand-filtered) rows."""
    month = today.strftime("%Y-%m")
    proc_month = defaultdict(float)
    proc_daily = defaultdict(lambda: defaultdict(float))
    proc_step = defaultdict(lambda: defaultdict(float))  # {process: {step: this-month $}}
    for f in sv:
        d = (f.get("Date") or "")[:10]
        c = f.get("Cost_USD", 0) or 0
        if not d:
            continue
        proc_daily[f.get("Process")][d] += c
        if d.startswith(month):
            proc_month[f.get("Process")] += c
            step = f.get("Step")
            if step:
                proc_step[f.get("Process")][step] += c

    window = {(today - dt.timedelta(days=i)).isoformat() for i in range(14)}
    flags = {}
    for proc, daily in proc_daily.items():
        active = [v for d, v in daily.items() if d in window and v > 0]
        baseline = sum(active) / len(active) if active else 0.0
        last_date = max(daily)
        latest = daily[last_date]
        flags[proc] = {"baseline": baseline, "latest": latest, "last_date": last_date,
                       "ballooned": baseline > 0 and latest >= 2 * baseline,
                       "ratio": (latest / baseline) if baseline > 0 else 0}

    chart_proc, chart = None, []
    if proc_daily:
        chart_proc = max(proc_daily, key=lambda p: len(proc_daily[p]))
        days = sorted(proc_daily[chart_proc])[-10:]
        chart = [(d, proc_daily[chart_proc][d]) for d in days]

    return {
        "month": month, "month_total": sum(proc_month.values()),
        "proc_month": proc_month, "flags": flags, "chart_proc": chart_proc, "chart": chart,
        "wired": sum(1 for p in processes if p.get("Wired")), "n_proc": len(processes),
        "processes": processes, "with_spend": sum(1 for v in proc_month.values() if v > 0),
        "flagged": [p for p, fl in flags.items() if fl["ballooned"]], "proc_step": proc_step,
    }


def _cost_body(ctx, brand, generated):
    """Inner HTML of the Cost panel for one brand view."""
    e = html.escape
    if not ctx["processes"] and ctx["month_total"] == 0:
        return (f'<div class="stub"><b>Nothing attributed to {e(brand)} yet.</b><br>'
                'As soon as a process tagged to this brand records cost, it shows up here.</div>')

    if not ctx["flagged"]:
        banner = ('<div class="banner ok"><span class="k">clear</span><p>Nothing ballooning: '
                  'every process is within ~2x of its own 14-day norm.</p></div>')
    else:
        banner = ('<div class="banner alert"><span class="k">balloon</span><p>Above 2x their 14-day norm: <b>'
                  + ", ".join(e(p) for p in ctx["flagged"])
                  + "</b>. Dig into the process + model to see what drove it.</p></div>")

    if ctx["chart"]:
        mx = max(v for _, v in ctx["chart"]) or 1
        bars = "".join(
            f'<div class="col"><span class="v">{_money(v)}</span>'
            f'<div class="bar" style="height:{max(3, round(v / mx * 100))}%"></div></div>'
            for _, v in ctx["chart"])
        xlab = "".join(f"<div>{d[5:]}</div>" for d, _ in ctx["chart"])
        chart_html = (f'<div class="card"><h2>{e(ctx["chart_proc"])} · daily cost</h2>'
                      '<p class="cap">The most-active process in this view. A spike shows up as a '
                      'taller bar.</p>'
                      f'<div class="chart">{bars}</div><div class="xlabels">{xlab}</div></div>')
    else:
        chart_html = ""

    rank = sorted(ctx["processes"], key=lambda p: (-(ctx["proc_month"].get(p.get("Process"), 0)),
                                                    -(p.get("Monthly_USD") or 0), not p.get("Wired")))
    rows = ""
    for p in rank:
        name = p.get("Process", "?")
        wired = bool(p.get("Wired"))
        mtd = ctx["proc_month"].get(name, 0.0)
        fl = ctx["flags"].get(name, {})
        if fl.get("ballooned"):
            pill = f'<span class="st bal"><span class="dot"></span>{fl["ratio"]:.1f}x</span>'
        elif wired and (p.get("Monthly_USD") or 0) == 0 and mtd == 0:
            pill = '<span class="st zero"><span class="dot"></span>$0</span>'
        elif wired:
            pill = '<span class="st good"><span class="dot"></span>wired</span>'
        else:
            pill = '<span class="st warn"><span class="dot"></span>placeholder</span>'
        amt = _money(mtd) if (mtd or wired) else "-"
        steps = ctx["proc_step"].get(name, {})
        caret = '<span class="caret">&#9656;</span> ' if steps else ""
        rows += (f'<tr{" class=has-steps onclick=st(this)" if steps else ""}>'
                 f'<td class="proc">{caret}{e(name)}</td><td class="src bt">{e(p.get("Brand", ""))}</td>'
                 f'<td class="src">{e(p.get("Providers", ""))}</td>'
                 f'<td>{pill}</td><td class="amt r">{amt}</td></tr>')
        if steps:
            total = sum(steps.values()) or 1
            ordered = sorted(steps.items(), key=lambda kv: -kv[1])
            seg = "".join(f'<div class="seg s{i % 6}" style="width:{max(2, round(v / total * 100))}%" '
                          f'title="{e(k)} {_money(v)}"></div>' for i, (k, v) in enumerate(ordered))
            legend = "".join(f'<span class="lg"><i class="s{i % 6}"></i>{e(k)} '
                             f'<b>{_money(v)}</b> <em>{round(v / total * 100)}%</em></span>'
                             for i, (k, v) in enumerate(ordered))
            rows += (f'<tr class="stepbrk" hidden><td colspan="5"><div class="brkbar">{seg}</div>'
                     f'<div class="legend">{legend}</div></td></tr>')

    data_through = max((fl["last_date"] for fl in ctx["flags"].values()), default="no data yet")
    return f"""
    <div class="kpis">
      <div class="kpi"><div class="lab">This month ({ctx['month']})</div><div class="val mono">{_money(ctx['month_total'])}</div><div class="sub">metered variable spend</div></div>
      <div class="kpi"><div class="lab">Processes with spend</div><div class="val mono">{ctx['with_spend']}</div><div class="sub">recording real cost</div></div>
      <div class="kpi"><div class="lab">Coverage</div><div class="val mono">{ctx['wired']}<span style="font-size:16px;color:var(--muted)"> / {ctx['n_proc']}</span></div><div class="sub">accounted for</div></div>
    </div>
    {banner}
    {chart_html}
    <div class="card"><h2>Every process</h2><p class="cap">This month's spend per process. A balloon flag means it is above 2x its own 14-day norm.</p>
      <table><thead><tr><th>Process</th><th>Brand</th><th>Cost source</th><th>Status</th><th class="r">This month</th></tr></thead><tbody>{rows}</tbody></table>
    </div>
    <footer>Variable cost only (fixed subscriptions, incl. the $200 Anthropic Max, are out of scope). Figures are directional estimates from token counts, cache-aware; the Anthropic Console holds the ground-truth bill. Data current through {data_through}.</footer>"""


def render(views, brands, generated):
    e = html.escape
    toggle = "".join(
        f'<button role="tab" aria-selected="{"true" if i == 0 else "false"}" '
        f'onclick="b(this,\'{e(br)}\')">{e(br)}</button>'
        for i, br in enumerate(brands))
    bodies = "".join(
        f'<div class="brandview" data-brand="{e(br)}"{"" if i == 0 else " hidden"}>{views[br]}</div>'
        for i, br in enumerate(brands))

    # Ops tab: per-process last-cost-seen, brand-labeled (global; not toggled)
    ops_rows = ""
    all_ctx = views["_ops"]
    for p in sorted(all_ctx["processes"], key=lambda p: p.get("Brand", "")):
        name = p.get("Process", "?")
        fl = all_ctx["flags"].get(name, {})
        last = fl.get("last_date")
        cell = last or "no cost recorded yet"
        if last:
            age = (generated.date() - dt.date.fromisoformat(last)).days
            if age > 3:
                cell = f'{last} <span class="stale">({age}d ago)</span>'
        ops_rows += (f'<tr><td class="proc">{e(name)}</td><td class="src bt">{e(p.get("Brand", ""))}</td>'
                     f'<td class="src">last cost seen: {cell}</td></tr>')

    repl = {
        "__GEN__": generated.strftime("%Y-%m-%d %H:%M"),
        "__BRAND_TOGGLE__": toggle, "__BRAND_VIEWS__": bodies, "__OPS_ROWS__": ops_rows,
    }
    out = _TPL
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


_TPL = r"""<meta name="robots" content="noindex, nofollow">
<title>Ops · Cost</title>
<style>
  :root{--paper:#F3F5F7;--surface:#FFFFFF;--sunk:#EEF1F4;--ink:#171A20;--ink-soft:#565D6A;--muted:#8A93A0;--line:#DCE2E8;--accent:#3F6E86;--good:#2E7D57;--good-soft:#E2F0E9;--good-line:#A7CDBB;--warn:#B4640F;--warn-soft:#F7E9D6;--warn-line:#E0B27C;--zero:#5B6675;--zero-soft:#E7EAEE;--zero-line:#C4CBD4;--bal:#B42318;--bal-soft:#FBE9E7;--bal-line:#E5A79F;--chip:#EDF0F3;--chip-on:#171A20;}
  @media (prefers-color-scheme:dark){:root{--paper:#0F1216;--surface:#181C22;--sunk:#12161B;--ink:#EAECEF;--ink-soft:#9AA4B0;--muted:#6C7683;--line:#272C34;--accent:#6FA6BE;--good:#63B98C;--good-soft:#15251D;--good-line:#2E4C3B;--warn:#E4A24A;--warn-soft:#2A2015;--warn-line:#6B4E22;--zero:#8A93A0;--zero-soft:#1D222A;--zero-line:#333A44;--bal:#F0857A;--bal-soft:#2A1512;--bal-line:#5C2A24;--chip:#20262E;--chip-on:#EAECEF;}}
  :root[data-theme="light"]{--paper:#F3F5F7;--surface:#FFFFFF;--sunk:#EEF1F4;--ink:#171A20;--ink-soft:#565D6A;--muted:#8A93A0;--line:#DCE2E8;--accent:#3F6E86;--good:#2E7D57;--good-soft:#E2F0E9;--good-line:#A7CDBB;--warn:#B4640F;--warn-soft:#F7E9D6;--warn-line:#E0B27C;--zero:#5B6675;--zero-soft:#E7EAEE;--zero-line:#C4CBD4;--bal:#B42318;--bal-soft:#FBE9E7;--bal-line:#E5A79F;--chip:#EDF0F3;--chip-on:#171A20;}
  :root[data-theme="dark"]{--paper:#0F1216;--surface:#181C22;--sunk:#12161B;--ink:#EAECEF;--ink-soft:#9AA4B0;--muted:#6C7683;--line:#272C34;--accent:#6FA6BE;--good:#63B98C;--good-soft:#15251D;--good-line:#2E4C3B;--warn:#E4A24A;--warn-soft:#2A2015;--warn-line:#6B4E22;--zero:#8A93A0;--zero-soft:#1D222A;--zero-line:#333A44;--bal:#F0857A;--bal-soft:#2A1512;--bal-line:#5C2A24;--chip:#20262E;--chip-on:#EAECEF;}
  *{box-sizing:border-box;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;-webkit-font-smoothing:antialiased;}
  .wrap{max-width:760px;margin:0 auto;padding:clamp(20px,4vw,36px) clamp(16px,4vw,28px) 56px;}
  .mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;}
  h1{font-size:clamp(20px,4vw,26px);margin:0;letter-spacing:-0.02em;font-weight:700;}
  h1 span{color:var(--muted);font-weight:600;}
  .updated{font-family:ui-monospace,monospace;font-size:11.5px;color:var(--muted);letter-spacing:.03em;}
  nav{display:flex;gap:4px;margin:16px 0 14px;border-bottom:1px solid var(--line);}
  nav button{appearance:none;background:none;border:none;font:inherit;font-size:14px;color:var(--ink-soft);padding:9px 14px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;border-radius:6px 6px 0 0;}
  nav button[aria-selected="true"]{color:var(--ink);font-weight:600;border-bottom-color:var(--accent);}
  .brands{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 20px;}
  .brands button{appearance:none;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;padding:6px 13px;border-radius:20px;border:1px solid var(--line);background:var(--chip);color:var(--ink-soft);}
  .brands button[aria-selected="true"]{background:var(--chip-on);color:var(--paper);border-color:var(--chip-on);}
  .panel{display:none;} .panel.on{display:block;}
  .brandview[hidden]{display:none;}
  .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px;}
  @media (max-width:560px){.kpis{grid-template-columns:1fr;}}
  .kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:15px 16px;}
  .kpi .lab{font-family:ui-monospace,monospace;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);}
  .kpi .val{font-size:30px;font-weight:700;letter-spacing:-0.02em;margin:6px 0 2px;font-variant-numeric:tabular-nums;}
  .kpi .sub{font-size:12.5px;color:var(--ink-soft);}
  .banner{display:grid;grid-template-columns:auto 1fr;gap:11px;align-items:start;margin-bottom:20px;padding:12px 15px;border-radius:12px;}
  .banner.ok{background:var(--good-soft);border:1px solid var(--good-line);}
  .banner.alert{background:var(--bal-soft);border:1px solid var(--bal-line);}
  .banner .k{font-family:ui-monospace,monospace;font-weight:700;font-size:11px;border-radius:6px;padding:3px 8px;white-space:nowrap;}
  .banner.ok .k{color:var(--good);border:1.5px solid var(--good-line);}
  .banner.alert .k{color:var(--bal);border:1.5px solid var(--bal-line);}
  .banner p{margin:0;font-size:13.5px;color:var(--ink);}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:20px;}
  .card h2{font-size:14px;margin:0 0 2px;font-weight:650;}
  .card .cap{font-size:12.5px;color:var(--ink-soft);margin:0 0 16px;}
  .chart{display:flex;align-items:flex-end;gap:8px;height:150px;padding-top:18px;border-bottom:1.5px solid var(--line);}
  .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;gap:6px;min-width:0;}
  .col .v{font-family:ui-monospace,monospace;font-size:10px;color:var(--ink-soft);}
  .bar{width:100%;max-width:46px;background:var(--accent);border-radius:4px 4px 0 0;}
  .xlabels{display:flex;gap:8px;margin-top:8px;} .xlabels div{flex:1;text-align:center;font-family:ui-monospace,monospace;font-size:10px;color:var(--muted);}
  table{border-collapse:collapse;width:100%;font-size:13.5px;}
  thead th{text-align:left;font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:0 10px 8px;border-bottom:1px solid var(--line);}
  th.r,td.r{text-align:right;}
  tbody td{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:middle;}
  tbody tr:last-child td{border-bottom:none;}
  td.proc{font-family:ui-monospace,monospace;font-weight:600;}
  td.src{color:var(--ink-soft);font-size:12.5px;}
  td.bt{font-family:ui-monospace,monospace;font-size:11.5px;}
  td.amt{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;font-weight:600;}
  tr.has-steps{cursor:pointer;}
  tr.has-steps:hover td{background:var(--sunk);}
  .caret{display:inline-block;color:var(--muted);font-size:9px;transition:transform .12s;margin-right:2px;}
  tr.open .caret{transform:rotate(90deg);}
  tr.stepbrk td{padding:4px 10px 14px 24px;background:var(--sunk);}
  .brkbar{display:flex;gap:2px;height:10px;border-radius:5px;overflow:hidden;margin-bottom:9px;max-width:520px;}
  .brkbar .seg{height:100%;}
  .legend{display:flex;flex-wrap:wrap;gap:6px 16px;}
  .lg{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-soft);}
  .lg i{width:9px;height:9px;border-radius:2px;flex:none;}
  .lg b{font-family:ui-monospace,monospace;color:var(--ink);font-weight:600;}
  .lg em{font-family:ui-monospace,monospace;font-style:normal;color:var(--muted);}
  .seg.s0,.lg i.s0{background:#3F6E86;} .seg.s1,.lg i.s1{background:#2E7D57;}
  .seg.s2,.lg i.s2{background:#B4640F;} .seg.s3,.lg i.s3{background:#7A5EA6;}
  .seg.s4,.lg i.s4{background:#4C8C7D;} .seg.s5,.lg i.s5{background:#9AA0A8;}
  .st{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,monospace;font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:20px;white-space:nowrap;}
  .st .dot{width:6px;height:6px;border-radius:50%;background:currentColor;}
  .st.good{background:var(--good-soft);color:var(--good);border:1px solid var(--good-line);}
  .st.zero{background:var(--zero-soft);color:var(--zero);border:1px solid var(--zero-line);}
  .st.warn{background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn-line);}
  .st.bal{background:var(--bal-soft);color:var(--bal);border:1px solid var(--bal-line);}
  .stale{font-family:ui-monospace,monospace;font-size:10.5px;color:var(--warn);}
  .stub{background:var(--sunk);border:1px dashed var(--line);border-radius:14px;padding:22px 20px;text-align:center;color:var(--ink-soft);font-size:13.5px;line-height:1.6;}
  footer{margin-top:8px;color:var(--muted);font-size:12px;line-height:1.6;}
</style>
<div class="wrap">
  <header><h1>Ops <span>· variable cost</span></h1><span class="updated">generated __GEN__</span></header>
  <nav role="tablist">
    <button role="tab" aria-selected="true" onclick="t(this,'cost')">Cost</button>
    <button role="tab" aria-selected="false" onclick="t(this,'ops')">Ops</button>
    <button role="tab" aria-selected="false" onclick="t(this,'misses')">Misses</button>
  </nav>
  <section class="panel on" id="cost">
    <div class="brands" role="tablist" aria-label="Brand">__BRAND_TOGGLE__</div>
    __BRAND_VIEWS__
  </section>
  <section class="panel" id="ops"><div class="card"><h2>Process activity</h2><p class="cap">When each process last recorded cost, across all brands. A process that goes quiet may have stopped running.</p>
    <table><tbody>__OPS_ROWS__</tbody></table></div>
    <div class="stub">Full ops-health (per-run success / fail + a link to each process's folder) comes next.</div></section>
  <section class="panel" id="misses"><div class="stub"><b>Missed posts</b><br>A scoreboard of scheduled posts that did not go live, from the Post-Watch watchdog. Coming next.</div></section>
</div>
<script>
function t(b,id){document.querySelectorAll('nav [role=tab]').forEach(x=>x.setAttribute('aria-selected',x===b));document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id===id));}
function b(btn,brand){document.querySelectorAll('.brands button').forEach(x=>x.setAttribute('aria-selected',x===btn));document.querySelectorAll('.brandview').forEach(v=>v.hidden=(v.dataset.brand!==brand));}
function st(tr){var n=tr.nextElementSibling;if(n&&n.classList.contains('stepbrk')){n.hidden=!n.hidden;tr.classList.toggle('open');}}
</script>
"""


def main():
    if not BRAND_ABBR:
        sys.exit("build_dashboard: BRAND_MAP not configured (env var or .env). Refusing to render "
                 "so spelled-out brand names are never exposed.")
    o = _load_ops()
    key, base = o["AIRTABLE_API_KEY"].strip(), o["AIRTABLE_BASE_ID"].strip()
    sv = _fetch("Spend_Variable", key, base)
    processes = _fetch("Processes", key, base)
    for f in sv:  # abbreviate brands + strip brand words from process names before anything renders
        f["Brand"] = _mask_brand(f.get("Brand"))
        f["Process"] = _mask_proc(f.get("Process"))
    for p in processes:
        p["Brand"] = _mask_brand(p.get("Brand"))
        p["Process"] = _mask_proc(p.get("Process"))
    generated = dt.datetime.now()
    today = generated.date()

    present = {_brand_of(f) for f in sv} | {p.get("Brand") for p in processes if p.get("Brand")}
    # always show the known brands (empty ones render a ready-and-waiting stub), then any extras
    brands = ["All"] + KNOWN_BRANDS + [b for b in sorted(present) if b and b not in KNOWN_BRANDS]

    views = {}
    for br in brands:
        if br == "All":
            fsv, fproc = sv, processes
        else:
            fsv = [f for f in sv if _brand_of(f) == br]
            fproc = [p for p in processes if p.get("Brand") == br]
        ctx = compute(fsv, fproc, today)
        views[br] = _cost_body(ctx, br, generated)
    views["_ops"] = compute(sv, processes, today)  # global, for the Ops tab

    path = os.path.join(HERE, "dashboard.html")
    with open(path, "w") as f:
        f.write(render(views, brands, generated))
    all_ctx = compute(sv, processes, today)
    print(f"wrote {path}")
    print(f"  brands: {', '.join(brands)}")
    print(f"  all-brands this month {_money(all_ctx['month_total'])}; "
          f"coverage {all_ctx['wired']}/{all_ctx['n_proc']}; flagged: {all_ctx['flagged'] or 'none'}")


if __name__ == "__main__":
    main()
