#!/usr/bin/env python3
"""Generate the internal Ops dashboard as a self-contained static HTML page.

Four tabs, all baked server-side from the live Ops base:
  Cost      Spend_Variable + Processes: monthly spend per process, a PLAIN 14-day baseline with
            a >=2x balloon flag, a trend chart, coverage, and a freshness stamp, with a brand
            toggle (All + one chip per brand, abbreviated).
  Ops       Processes + Process_Runs: one stoplight per process, from the run history the
            collector reads out of GitHub Actions and the two launchd logs.
  In basket Open_Items: everything waiting on Lazar, 30-second fixes first.
  Misses    the missed-post scoreboard, which needs a token that reaches the Posts bases.

This page is committed into a PUBLIC repo, so every word of Open_Items prose passes
ops_common.public_text before it is rendered. Stdlib only.

  python3 build_dashboard.py
"""
import os
import re
import sys
import json
import html
import datetime as dt
import urllib.request
import urllib.error
from collections import defaultdict

import ops_common as oc

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


# ------------------------------------------------------------------- Ops tab and In basket tab

# The four status words, each with the icon that always ships beside it. Colour is never the
# only signal: every cell carries the icon and the word.
FLOWING, NEEDS_YOU, BROKEN, UNVERIFIED = "Flowing", "Needs you", "Broken / stalled", "Unverified"
STATUS_ICON = {FLOWING: "●", NEEDS_YOU: "▲", BROKEN: "■", UNVERIFIED: "◌"}
STATUS_CLASS = {FLOWING: "flow", NEEDS_YOU: "need", BROKEN: "brok", UNVERIFIED: "unv"}
STATUS_ORDER = [BROKEN, NEEDS_YOU, UNVERIFIED, FLOWING]

# Triggers that only move when a person sits down with them.
PERSON_TRIGGERS = {"terminal_only", "watch_loop", "desktop_launcher"}
# Triggers that report somewhere this page cannot read.
BLIND_TRIGGERS = {"cloud_routine"}

KIND_ORDER = ["30-second fix", "approval", "decision", "your own task", "info"]


def _ts(s):
    """An Airtable dateTime string as a naive UTC datetime, or None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _age_words(when, now):
    if not when:
        return "never"
    h = (now - when).total_seconds() / 3600.0
    if h < 1:
        return "just now"
    if h < 48:
        return f"{int(h)}h ago"
    return f"{int(h / 24)}d ago"


def _ops_status(trigger, expected, runs, now):
    """(status word, last run, last ok, fail streak). runs is newest first."""
    last = runs[0] if runs else None
    last_ok = next((r for r in runs if r.get("Status") == "ok"), None)
    streak = 0
    for r in runs:
        if r.get("Status") in ("fail", "blocked"):
            streak += 1
        else:
            break

    if trigger in PERSON_TRIGGERS:
        return NEEDS_YOU, last, last_ok, streak
    if not runs:
        return UNVERIFIED, None, None, 0
    if last.get("Status") in ("fail", "blocked"):
        return BROKEN, last, last_ok, streak
    if not expected:                       # on demand: the last run is all there is to judge
        return FLOWING, last, last_ok, streak
    if last_ok is None:
        return BROKEN, last, last_ok, streak
    age_h = (now - _ts(last_ok["Ran_At"])).total_seconds() / 3600.0
    if age_h > 3 * expected:
        return BROKEN, last, last_ok, streak
    if age_h > 1.5 * expected:
        return NEEDS_YOU, last, last_ok, streak
    return FLOWING, last, last_ok, streak


def _every_words(trigger, expected):
    if trigger in PERSON_TRIGGERS:
        return "when you run it"
    if not expected:
        return "on demand"
    if expected == 1:
        return "hourly"
    if expected % 24 == 0 and expected >= 24:
        d = expected // 24
        return "daily" if d == 1 else f"every {d} days"
    return f"every {expected}h"


def _safe_href(url, brand_map):
    """A docs link may only be rendered if the URL itself carries no spelled-out brand name."""
    if not url:
        return None
    for full in (brand_map or {}):
        words = [re.escape(w) for w in re.split(r"[ _-]+", full) if w]
        if words and re.search("(?i)" + r"[-_ ]?".join(words), url):
            return None
    return url


def _pill(word):
    return (f'<span class="ost {STATUS_CLASS[word]}"><span class="ic">{STATUS_ICON[word]}</span>'
            f'{html.escape(word)}</span>')


def ops_tab(processes, runs, open_items, brand_map, generated, freshness=None):
    e = html.escape
    now = generated
    by_proc = defaultdict(list)
    for r in runs:
        by_proc[r.get("Process")].append(r)
    for v in by_proc.values():
        v.sort(key=lambda r: r.get("Ran_At") or "", reverse=True)

    open_by_engine = defaultdict(int)
    for f in open_items:
        if f.get("Status") == "open":
            open_by_engine[f.get("Engine")] += 1

    known = {p.get("Process") for p in processes}
    rows_in = list(processes) + [{"Process": p, "Brand": "", "Trigger": "", "Expected_Every_Hours": 0}
                                 for p in sorted(by_proc) if p not in known]

    computed = []
    for p in rows_in:
        name = p.get("Process") or "?"
        trigger = p.get("Trigger") or ""
        expected = p.get("Expected_Every_Hours") or 0
        word, last, last_ok, streak = _ops_status(trigger, expected, by_proc.get(name, []), now)
        computed.append({
            "name": name, "brand": p.get("Brand") or "", "trigger": trigger,
            "expected": expected, "word": word, "last": last, "last_ok": last_ok,
            "streak": streak, "open": open_by_engine.get(name, 0),
            "docs": _safe_href(p.get("Docs_Link"), brand_map),
        })

    counts = {w: sum(1 for c in computed if c["word"] == w) for w in STATUS_ORDER}
    summary = ", ".join(f"{counts[w]} {w.lower()}" for w in STATUS_ORDER if counts[w])
    computed.sort(key=lambda c: (STATUS_ORDER.index(c["word"]), -c["open"], c["name"]))

    fresh = freshness or {}
    body = ""
    for c in computed:
        if c["last"]:
            when = _ts(c["last"]["Ran_At"])
            last_cell = f'{when.strftime("%b %d")} <span class="dim">{_age_words(when, now)}</span>'
        elif c["name"] in fresh:
            # nobody logs a run for this one, so date it by what it last produced
            when, label = fresh[c["name"]]
            last_cell = (f'{when.strftime("%b %d")} '
                         f'<span class="dim">{_age_words(when, now)}, {e(label)}</span>')
        else:
            last_cell = '<span class="dim">no run log</span>'
        ok_cell = (_ts(c["last_ok"]["Ran_At"]).strftime("%b %d") if c["last_ok"]
                   else '<span class="dim">never</span>')
        streak = f'<b class="bad">{c["streak"]}</b>' if c["streak"] else "0"
        openc = (f'<span class="obadge">{c["open"]}</span>' if c["open"] else '<span class="dim">0</span>')
        name = (f'<a href="{e(c["docs"])}" rel="noreferrer noopener">{e(c["name"])}</a>'
                if c["docs"] else e(c["name"]))
        # Status sits second so a phone shows the whole word without swiping: this table scrolls
        # sideways at 390, and with Brand in between the status read "Brok..." off the edge.
        body += (f'<tr><td class="proc">{name}</td>'
                 f'<td>{_pill(c["word"])}</td>'
                 f'<td class="src bt">{e(c["brand"])}</td>'
                 f'<td class="src">{e(c["trigger"]) or "-"}</td>'
                 f'<td class="src">{last_cell}</td><td class="src">{ok_cell}</td>'
                 f'<td class="r src">{streak}</td>'
                 f'<td class="src">{_every_words(c["trigger"], c["expected"])}</td>'
                 f'<td class="r">{openc}</td></tr>')

    return f"""
    <div class="sumline">{e(summary) or "nothing to report"}</div>
    <div class="card"><h2>Every process</h2>
      <p class="cap">Run history comes from GitHub Actions and from the two scheduled jobs on the Mac.
      A process marked "{e(NEEDS_YOU)}" is either one a person has to start, or a scheduled one that
      is running late. "{e(UNVERIFIED)}" means nothing has reported in yet, so this page cannot say.
      A process nobody logs a run for is dated by what it last produced, and the cell says which.
      A process name is a link when its docs can be named here.</p>
      <div class="scroll"><table>
        <thead><tr><th>Process</th><th>Status</th><th>Brand</th><th>Started by</th><th>Last run</th>
        <th>Last ok</th><th class="r">Fails</th><th>Expected</th><th class="r">Waiting on you</th>
        </tr></thead><tbody>{body}</tbody></table></div>
    </div>
    <div class="legend2">{"".join(_pill(w) for w in STATUS_ORDER)}</div>"""


def basket_tab(open_items, brand_map, generated):
    e = html.escape
    today = generated.date()
    live = [f for f in open_items if f.get("Status") == "open"]
    quick = sum(1 for f in live if f.get("Kind") == "30-second fix")

    groups = defaultdict(list)
    for f in live:
        groups[f.get("Kind") or "info"].append(f)

    held_total = 0
    sections = ""
    for kind in KIND_ORDER + [k for k in sorted(groups) if k not in KIND_ORDER]:
        items = groups.get(kind)
        if not items:
            continue
        rows = ""
        for f in sorted(items, key=lambda x: x.get("Asked_On") or ""):
            text, held = oc.public_text(f.get("Item"), brand_map)
            engine, _ = oc.public_text(f.get("Engine"), brand_map)
            source, src_held = oc.public_text(f.get("Source"), brand_map)
            asked = f.get("Asked_On")
            age = ""
            if asked:
                try:
                    age = f"{(today - dt.date.fromisoformat(asked)).days}d"
                except ValueError:
                    age = ""
            if held:
                held_total += 1
                cell = ('<span class="dim">held back: this page is public, so read this one in '
                        'the Ops base</span>')
            else:
                cell = e(text)
            rows += (f'<tr><td class="src bt">{e(engine)}</td><td>{cell}</td>'
                     f'<td class="r src">{e(age)}</td>'
                     f'<td class="src">{"" if src_held else e(source)}</td></tr>')
        sections += (f'<div class="card"><h2>{e(kind)} <span class="n">{len(items)}</span></h2>'
                     '<div class="scroll"><table><thead><tr><th>Process</th><th>Item</th>'
                     '<th class="r">Waiting</th><th>Where it came from</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div></div>')

    if not live:
        return '<div class="stub"><b>Nothing is waiting on you.</b></div>'

    note = ""
    if held_total:
        note = (f'<p class="cap">{held_total} of these mention a credential or a file path, so their '
                'words are held back here and live only in the Ops base.</p>')
    return f"""
    <div class="sumline">{len(live)} waiting on you, {quick} of them are 30-second fixes</div>
    {note}
    <p class="cap">Answer any of these in the Ops base in Airtable: set Status to decided or done and
    write the answer in Decision. The next refresh takes it off this list.</p>
    {sections}"""


def _posts_creds():
    """(key, {abbreviation: base id}) for the posting calendars, which live outside the Ops base.

    The Ops token cannot reach them, so this needs a second one, and neither the token nor the
    base ids may sit in this public source.

    In CI both arrive as encrypted repo secrets: POSTS_AIRTABLE_API_KEY and POSTS_BASES.

    On the Mac neither is set, so both come from `posts_sources.json` beside the .env this repo
    resolves, which is in the private ops folder. That file names the .env ALREADY holding the
    token and the variable inside it; the value is read at run time, so no second copy of a key
    is ever made and no base id lands in this public repo.
    """
    o = _load_ops()

    def val(name):
        return (os.environ.get(name) or o.get(name) or "").strip()

    key = val("POSTS_AIRTABLE_API_KEY")
    try:
        bases = json.loads(val("POSTS_BASES") or "{}")
    except ValueError:
        bases = {}

    spec = _posts_spec()
    bases = bases or spec.get("bases") or {}
    src = os.path.expanduser(spec.get("env_file") or "")
    want = (spec.get("env_var") or "AIRTABLE_API_KEY") + "="
    if not key and src and os.path.exists(src):
        for line in open(src, errors="ignore"):
            if line.strip().startswith(want):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return key, (bases if key else {})


def _posts_spec():
    """`posts_sources.json` from beside the resolved .env, or {} when it is not there."""
    side = os.path.join(os.path.dirname(os.path.realpath(os.path.join(HERE, ".env"))),
                        "posts_sources.json")
    if not os.path.exists(side):
        return {}
    try:
        return json.load(open(side))
    except ValueError:
        return {}


def last_activity(key, bases):
    """{process: (datetime, label)} for processes a person starts by hand.

    They write no run row, so the Ops tab dates them by what they PRODUCE. Which table stands
    for which process is named outside this public source, in posts_sources.json, because those
    process names spell out the brands. A process with no reachable source is simply absent here
    and its row keeps saying "no run log", which is the honest answer.
    """
    out = {}
    if not (key and bases):
        return out
    for spec in _posts_spec().get("freshness", []):
        base = bases.get(spec.get("base"))
        if not base:
            continue
        try:
            rows = _fetch(spec["table"], key, base)
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError):
            continue
        stamps = sorted(str(r.get(spec["field"])) for r in rows if r.get(spec["field"]))
        if not stamps:
            continue
        # these columns are dateTime in one table and a plain date in another
        newest = stamps[-1]
        when = _ts(newest) or _ts(newest + "Z") or _ts(newest + "T00:00:00Z")
        if when:
            out[_mask_proc(spec["process"])] = (when, spec.get("label") or "last output")
    return out


def misses_tab(key, bases, generated=None):
    """Last 14 days of scheduled posts per brand, plus the watchdog's unhandled misses."""
    e = html.escape
    generated = generated or dt.datetime.now()
    if not bases:
        return ('<div class="stub"><b>Missed posts are not readable yet.</b><br>'
                'This page reads one Airtable base. The posting calendars live in two others, and '
                'the token it uses cannot reach them.<br>The In basket tab carries the 30-second '
                'fix that opens this up.</div>')

    today = generated.date()
    days = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
    # A slot in the future has not been missed yet, whatever its status says.
    settled = [d for d in days if d < today]
    strips, watch_rows, errors, headline = "", "", [], []
    for ab, base in sorted(bases.items()):
        try:
            sched = _fetch("Posting_Schedule", key, base)
        except urllib.error.HTTPError as ex:
            errors.append(f"the {ab} calendar ({ex.code})")
            continue
        by_day = defaultdict(list)
        for f in sched:
            d = (f.get("Slot_At") or "")[:10]
            if d:
                by_day[d].append((f.get("Status") or "").lower())
        cells, missed, posted, planned = "", 0, 0, 0
        for d in days:
            st = by_day.get(d.isoformat(), [])
            if any(s == "missed" for s in st):
                word, cls, ic = "missed", "brok", "■"
            elif any(s == "posted" for s in st):
                word, cls, ic = "posted", "flow", "●"
            elif st:
                word, cls, ic = "planned", "unv", "◌"
            else:
                word, cls, ic = "no slot", "unv", "·"
            if d in settled:
                missed += word == "missed"
                posted += word == "posted"
                planned += word == "planned"
            cells += (f'<div class="daycell {cls}" title="{d.isoformat()} {word}">'
                      f'<span class="ic">{ic}</span><span class="dn">{d.strftime("%d")}</span></div>')
        scored = missed + posted + planned
        if scored:
            headline.append(f"{ab} missed {missed} of its last {scored} posting days")
        note = (f'{missed} missed, {posted} posted, {planned} still marked planned'
                if scored else 'no slots in this window')
        strips += (f'<div class="card"><h2>{e(ab)} <span class="n">last 14 days</span></h2>'
                   f'<p class="cap">{e(note)}. A day counts as missed when any slot on it is '
                   'marked missed. Today and anything later is not scored yet.</p>'
                   f'<div class="strip">{cells}</div></div>')

        try:
            for f in _fetch("Post_Watch", key, base):
                if (f.get("Status") or "").lower() == "missed" and not f.get("Handled"):
                    when, _ = oc.public_text(f.get("Scheduled_At") or "", BRAND_ABBR)
                    why, held = oc.public_text(f.get("Reason") or f.get("Note") or "", BRAND_ABBR)
                    watch_rows += (f'<tr><td class="src bt">{e(ab)}</td>'
                                   f'<td class="src">{e(f.get("Platform") or f.get("Provider") or "")}</td>'
                                   f'<td class="src">{e(when[:16])}</td>'
                                   f'<td>{"" if held else e(why)}</td></tr>')
        except urllib.error.HTTPError:
            pass   # a brand with no watchdog table is normal, not a fault worth a red line

    watch = ('<div class="card"><h2>Unhandled misses <span class="n">from the watchdog</span></h2>'
             '<div class="scroll"><table><thead><tr><th>Brand</th><th>Where</th><th>Slot</th>'
             f'<th>Why</th></tr></thead><tbody>{watch_rows}</tbody></table></div></div>'
             ) if watch_rows else ('<div class="stub">The watchdog is not holding any missed post '
                                   'that nobody has dealt with.</div>')
    err = f'<p class="cap">Could not read {e(", ".join(errors))}.</p>' if errors else ""
    sumline = (f'<div class="sumline">{e("; ".join(headline))}</div>' if headline else "")
    legend = ('<div class="legend2">'
              '<span class="ost brok"><span class="ic">■</span>missed</span>'
              '<span class="ost flow"><span class="ic">●</span>posted</span>'
              '<span class="ost unv"><span class="ic">◌</span>planned</span>'
              '<span class="ost unv"><span class="ic">·</span>no slot</span></div>')
    return f'{sumline}{err}{legend}{strips}{watch}'


def render(views, brands, generated, tabs):
    e = html.escape
    toggle = "".join(
        f'<button role="tab" aria-selected="{"true" if i == 0 else "false"}" '
        f'onclick="b(this,\'{e(br)}\')">{e(br)}</button>'
        for i, br in enumerate(brands))
    bodies = "".join(
        f'<div class="brandview" data-brand="{e(br)}"{"" if i == 0 else " hidden"}>{views[br]}</div>'
        for i, br in enumerate(brands))

    repl = {
        "__GEN__": generated.strftime("%Y-%m-%d %H:%M"),
        "__BRAND_TOGGLE__": toggle, "__BRAND_VIEWS__": bodies,
        "__OPS_TAB__": tabs["ops"], "__BASKET_TAB__": tabs["basket"], "__MISSES_TAB__": tabs["misses"],
    }
    out = _TPL
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


_TPL = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Ops dashboard</title>
<style>
  :root{--paper:#F3F5F7;--surface:#FFFFFF;--sunk:#EEF1F4;--ink:#171A20;--ink-soft:#565D6A;--muted:#8A93A0;--line:#DCE2E8;--accent:#3F6E86;--good:#2E7D57;--good-soft:#E2F0E9;--good-line:#A7CDBB;--warn:#B4640F;--warn-soft:#F7E9D6;--warn-line:#E0B27C;--zero:#5B6675;--zero-soft:#E7EAEE;--zero-line:#C4CBD4;--bal:#B42318;--bal-soft:#FBE9E7;--bal-line:#E5A79F;--chip:#EDF0F3;--chip-on:#171A20;}
  @media (prefers-color-scheme:dark){:root{--paper:#0F1216;--surface:#181C22;--sunk:#12161B;--ink:#EAECEF;--ink-soft:#9AA4B0;--muted:#6C7683;--line:#272C34;--accent:#6FA6BE;--good:#63B98C;--good-soft:#15251D;--good-line:#2E4C3B;--warn:#E4A24A;--warn-soft:#2A2015;--warn-line:#6B4E22;--zero:#8A93A0;--zero-soft:#1D222A;--zero-line:#333A44;--bal:#F0857A;--bal-soft:#2A1512;--bal-line:#5C2A24;--chip:#20262E;--chip-on:#EAECEF;}}
  :root[data-theme="light"]{--paper:#F3F5F7;--surface:#FFFFFF;--sunk:#EEF1F4;--ink:#171A20;--ink-soft:#565D6A;--muted:#8A93A0;--line:#DCE2E8;--accent:#3F6E86;--good:#2E7D57;--good-soft:#E2F0E9;--good-line:#A7CDBB;--warn:#B4640F;--warn-soft:#F7E9D6;--warn-line:#E0B27C;--zero:#5B6675;--zero-soft:#E7EAEE;--zero-line:#C4CBD4;--bal:#B42318;--bal-soft:#FBE9E7;--bal-line:#E5A79F;--chip:#EDF0F3;--chip-on:#171A20;}
  :root[data-theme="dark"]{--paper:#0F1216;--surface:#181C22;--sunk:#12161B;--ink:#EAECEF;--ink-soft:#9AA4B0;--muted:#6C7683;--line:#272C34;--accent:#6FA6BE;--good:#63B98C;--good-soft:#15251D;--good-line:#2E4C3B;--warn:#E4A24A;--warn-soft:#2A2015;--warn-line:#6B4E22;--zero:#8A93A0;--zero-soft:#1D222A;--zero-line:#333A44;--bal:#F0857A;--bal-soft:#2A1512;--bal-line:#5C2A24;--chip:#20262E;--chip-on:#EAECEF;}
  *{box-sizing:border-box;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;-webkit-font-smoothing:antialiased;}
  .wrap{max-width:760px;margin:0 auto;padding:clamp(20px,4vw,36px) clamp(16px,4vw,28px) 56px;transition:max-width .12s;}
  body.wide .wrap{max-width:1120px;}
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
  td.proc{font-family:ui-monospace,monospace;font-weight:600;white-space:nowrap;}
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
  .ost{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:650;padding:3px 9px;border-radius:20px;white-space:nowrap;}
  .ost .ic{font-size:10px;line-height:1;}
  .ost.flow{background:var(--good-soft);color:var(--good);border:1px solid var(--good-line);}
  .ost.need{background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn-line);}
  .ost.brok{background:var(--bal-soft);color:var(--bal);border:1px solid var(--bal-line);}
  .ost.unv{background:var(--zero-soft);color:var(--zero);border:1px solid var(--zero-line);}
  .sumline{font-size:15px;font-weight:600;margin:0 0 16px;letter-spacing:-0.01em;}
  .legend2{display:flex;gap:8px;flex-wrap:wrap;margin:-6px 0 20px;}
  .dim{color:var(--muted);}
  .bad{color:var(--bal);}
  .obadge{display:inline-block;min-width:22px;text-align:center;font-family:ui-monospace,monospace;font-size:11px;font-weight:700;padding:2px 6px;border-radius:20px;background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn-line);}
  .card h2 .n{font-family:ui-monospace,monospace;font-size:11px;font-weight:700;color:var(--muted);margin-left:6px;}
  .scroll{overflow-x:auto;}
  .scroll table{min-width:640px;}
  #basket .scroll table{min-width:520px;}
  #basket td{font-size:13px;}
  #basket td.bt{white-space:nowrap;}
  @media (max-width:560px){#basket td.bt{white-space:normal;}}
  #ops td.src,#ops thead th{white-space:nowrap;}
  /* On a phone this table scrolls sideways, so the two columns that must be readable without
     any swiping are the name and the status. Tighten both just enough that the longest status
     word fits inside 390px instead of being clipped at its last letter. */
  @media (max-width:560px){#ops .ost{font-size:11px;padding:3px 7px;}#ops td.proc{font-size:12.5px;}}
  .card a{color:var(--accent);}
  .strip{display:flex;gap:4px;flex-wrap:wrap;}
  .daycell{flex:1;min-width:34px;display:flex;flex-direction:column;align-items:center;gap:2px;padding:7px 2px;border-radius:8px;font-size:10px;font-family:ui-monospace,monospace;}
  .daycell.flow{background:var(--good-soft);color:var(--good);border:1px solid var(--good-line);}
  .daycell.brok{background:var(--bal-soft);color:var(--bal);border:1px solid var(--bal-line);}
  .daycell.unv{background:var(--zero-soft);color:var(--zero);border:1px solid var(--zero-line);}
  .daycell .dn{font-weight:700;}
  .stub{background:var(--sunk);border:1px dashed var(--line);border-radius:14px;padding:22px 20px;text-align:center;color:var(--ink-soft);font-size:13.5px;line-height:1.6;}
  footer{margin-top:8px;color:var(--muted);font-size:12px;line-height:1.6;}
</style>
<div class="wrap">
  <header><h1>Ops <span>· what is running, what is waiting</span></h1><span class="updated">generated __GEN__</span></header>
  <nav role="tablist">
    <button role="tab" aria-selected="true" onclick="t(this,'cost')">Cost</button>
    <button role="tab" aria-selected="false" onclick="t(this,'ops')">Ops</button>
    <button role="tab" aria-selected="false" onclick="t(this,'basket')">In basket</button>
    <button role="tab" aria-selected="false" onclick="t(this,'misses')">Misses</button>
  </nav>
  <section class="panel on" id="cost">
    <div class="brands" role="tablist" aria-label="Brand">__BRAND_TOGGLE__</div>
    __BRAND_VIEWS__
  </section>
  <section class="panel" id="ops">__OPS_TAB__</section>
  <section class="panel" id="basket">__BASKET_TAB__</section>
  <section class="panel" id="misses">__MISSES_TAB__</section>
</div>
<script>
function t(b,id){document.querySelectorAll('nav [role=tab]').forEach(x=>x.setAttribute('aria-selected',x===b));document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id===id));document.body.classList.toggle('wide',id==='ops'||id==='misses');window.scrollTo(0,0);}
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
    runs = _fetch("Process_Runs", key, base)
    open_items = _fetch("Open_Items", key, base)
    posts_key, posts_bases = _posts_creds()
    freshness = last_activity(posts_key, posts_bases)
    for r in runs:
        r["Process"] = _mask_proc(r.get("Process"))
    for f in open_items:
        f["Engine"] = _mask_proc(f.get("Engine"))
        f["Brand"] = _mask_brand(f.get("Brand"))
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
    views["_ops"] = compute(sv, processes, today)  # global, for the Cost tab's coverage numbers

    tabs = {
        "ops": ops_tab(processes, runs, open_items, BRAND_ABBR, generated, freshness),
        "basket": basket_tab(open_items, BRAND_ABBR, generated),
        "misses": misses_tab(posts_key, posts_bases, generated),
    }

    path = os.path.join(HERE, "dashboard.html")
    with open(path, "w") as f:
        f.write(render(views, brands, generated, tabs))
    all_ctx = compute(sv, processes, today)
    print(f"wrote {path}")
    print(f"  brands: {', '.join(brands)}")
    print(f"  all-brands this month {_money(all_ctx['month_total'])}; "
          f"coverage {all_ctx['wired']}/{all_ctx['n_proc']}; flagged: {all_ctx['flagged'] or 'none'}")
    print("  ops tab: " + re.sub("<[^>]+>", "", tabs["ops"]).strip().splitlines()[0])
    print("  in basket: " + re.sub("<[^>]+>", "", tabs["basket"]).strip().splitlines()[0])


if __name__ == "__main__":
    main()
