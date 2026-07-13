#!/usr/bin/env python3
"""Generate the PUBLIC, rolled-up cost telemetry page for a personal website.

A portfolio piece: proof that variable AI spend is instrumented and controlled, live.
Reads the same Ops base as the internal dashboard but shows ONLY aggregates - no brand
toggle, no step drill-down, no process or brand names. Reuses the internal generator's
fetch + balloon logic so both stay honest and in sync. Stdlib only.

  python3 build_public.py     ->  public.html
"""
import os
import datetime as dt
from collections import defaultdict

import build_dashboard as bd  # reuse _load_ops, _fetch, _money, compute (same dir)

HERE = os.path.dirname(os.path.abspath(__file__))


def _tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _spark(series):
    """Small SVG area chart from [(date, value)]. Emphasized endpoint; classes carry color."""
    W, H, pad, top = 880.0, 190.0, 6.0, 26.0
    n = len(series)
    mx = max((v for _, v in series), default=0) or 1.0
    xs = [pad + (i * (W - 2 * pad) / (n - 1) if n > 1 else 0) for i in range(n)]
    ys = [H - pad - (v / mx) * (H - pad - top) for _, v in series]
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in zip(xs, ys))
    area = f"{line} L{xs[-1]:.1f} {H - pad:.1f} L{xs[0]:.1f} {H - pad:.1f} Z"
    lx, ly = xs[-1], ys[-1]
    return (f'<svg class="spark" viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none" '
            f'role="img" aria-label="Daily metered spend, last {n} days">'
            f'<line class="base" x1="{pad}" y1="{H - pad:.1f}" x2="{W - pad:.1f}" y2="{H - pad:.1f}"/>'
            f'<path class="area" d="{area}"/><path class="line" d="{line}"/>'
            f'<circle class="dot" cx="{lx:.1f}" cy="{ly:.1f}" r="4.5"/></svg>')


def build():
    o = bd._load_ops()
    key, base = o["AIRTABLE_API_KEY"].strip(), o["AIRTABLE_BASE_ID"].strip()
    sv = bd._fetch("Spend_Variable", key, base)
    processes = bd._fetch("Processes", key, base)
    now = dt.datetime.now()
    today = now.date()
    month = today.strftime("%Y-%m")

    month_total = sum((f.get("Cost_USD") or 0) for f in sv if (f.get("Date") or "").startswith(month))
    tokens_month = sum(int(f.get("Units") or 0) for f in sv if (f.get("Date") or "").startswith(month))
    dates = [(f.get("Date") or "")[:10] for f in sv if f.get("Date")]
    earliest = min(dates) if dates else today.isoformat()

    daily = defaultdict(float)
    for f in sv:
        d = (f.get("Date") or "")[:10]
        if d:
            daily[d] += (f.get("Cost_USD") or 0)
    series = [((today - dt.timedelta(days=i)).isoformat(),
               daily.get((today - dt.timedelta(days=i)).isoformat(), 0.0)) for i in range(13, -1, -1)]

    agg = bd.compute(sv, processes, today)
    clear = not agg["flagged"]
    n_pipelines = agg["wired"]
    n_brands = len({p.get("Brand") for p in processes if p.get("Brand")})

    m = bd._money(month_total)
    dollars, cents = (m[1:].split(".") + [""])[:2]
    status = ('<span class="chip ok"><span class="ping"></span>All clear</span>' if clear
              else f'<span class="chip hot"><span class="ping"></span>{len(agg["flagged"])} running hot</span>')
    stamp = now.strftime("%b %-d, %Y · %-I:%M %p")
    since = dt.date.fromisoformat(earliest).strftime("%b %Y")

    gauges = [("Tokens metered", _tokens(tokens_month), "this month"),
              ("Pipelines instrumented", str(n_pipelines), "auto-recording"),
              ("Brands operated", str(n_brands), "one shared meter")]
    gauge_html = "".join(
        f'<div class="gauge"><div class="gv">{v}</div><div class="gl">{l}</div><div class="gs">{s}</div></div>'
        for l, v, s in gauges)

    body = f"""<div class="wrap">
  <header class="masthead">
    <div class="eyebrow"><span class="live"></span>Live telemetry · self-instrumented</div>
    <h1>The cost of my own AI, measured live.</h1>
    <p class="lede">Across my personal and business operations, I use AI as often as I can &mdash; trying to
      stay on the cutting edge. This panel measures that usage and what it costs, live: every figure is
      computed the moment an API call returns, cache-aware, refreshed on each run. A working instrument,
      not a screenshot.</p>
  </header>

  <section class="readout" aria-label="Metered spend this month">
    <div class="ro-label">Metered this month</div>
    <div class="ro-figure"><span class="cur">$</span><span class="amt" data-target="{month_total:.2f}">{dollars}</span><span class="dec">.{cents}</span></div>
    <div class="ro-foot"><span class="live"></span>updated {stamp}</div>
  </section>

  <section class="gauges">{gauge_html}</section>

  <section class="signal">
    <div class="sig-head">
      <div><div class="sig-title">Signal</div><div class="sig-sub">Daily metered spend · last 14 days</div></div>
      {status}
    </div>
    {_spark(series)}
    <p class="sig-note">Anomaly watch runs on every pipeline: each is measured against its own 14-day
      baseline, and anything crossing 2&times; its norm is flagged. Tracking since {since}.</p>
  </section>

  <section class="opnote"><span class="op-tick"></span><p>Behind this public readout, the same meter
    breaks down by brand and by process, and feeds an agent I run on a two-week cycle to hunt for
    efficiency opportunities &mdash; where a stage can be cached, downshifted to a cheaper model, or cut.</p></section>

  <section class="how">
    <div class="how-lead">How the meter works</div>
    <div class="principles">
      <div class="pr"><div class="pt">Measured, not estimated</div><div class="pd">Cost is derived from each response's own token counts as it returns &mdash; not reconstructed from a monthly bill.</div></div>
      <div class="pr"><div class="pt">Cache-aware</div><div class="pd">Cached reads are priced at a tenth of fresh input, so a caching optimization shows up as the number visibly dropping.</div></div>
      <div class="pr"><div class="pt">A smoke alarm, not a ledger</div><div class="pd">Built to catch a process whose cost balloons, not to reconcile invoices. Directional accuracy, deliberately.</div></div>
      <div class="pr"><div class="pt">Fail-soft, forward-only</div><div class="pd">Metering runs beside the work and can never break it; it records from the moment a pipeline runs onward.</div></div>
    </div>
  </section>

  <footer class="foot">
    Generated {stamp} from live data. Variable cost only &mdash; fixed subscriptions excluded. Figures are
    directional estimates from metered tokens; the provider console holds the ground-truth bill.
  </footer>
</div>"""

    html_out = ('<meta name="robots" content="noindex, nofollow">\n<title>Live AI Cost Telemetry</title>\n'
                "<style>\n" + _CSS + "\n</style>\n" + body + "\n<script>\n" + _JS + "\n</script>\n")
    path = os.path.join(HERE, "public.html")
    with open(path, "w") as f:
        f.write(html_out)
    print(f"wrote {path}")
    print(f"  metered this month {m} · tokens {_tokens(tokens_month)} · pipelines {n_pipelines} · "
          f"brands {n_brands} · {'clear' if clear else 'FLAGGED'}")


_CSS = r"""
:root{
  --ground:#0C1013; --panel:#141A1E; --panel-2:#10161A; --line:#232D33;
  --ink:#ECEFEC; --muted:#7E8C92; --dim:#55636A;
  --signal:#E0A64B; --signal-wash:rgba(224,166,75,.12); --signal-line:#3A3322;
  --ok:#5FB98C; --ok-wash:rgba(95,185,140,.13);
  --hot:#E08A4B;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Menlo",Consolas,monospace;
}
@media (prefers-color-scheme:light){:root{
  --ground:#EEF1F0; --panel:#FFFFFF; --panel-2:#F6F8F7; --line:#DFE5E3;
  --ink:#141A1C; --muted:#586369; --dim:#8A959A;
  --signal:#A66A12; --signal-wash:rgba(166,106,18,.10); --signal-line:#E7DCC7;
  --ok:#2E7D57; --ok-wash:rgba(46,125,87,.10); --hot:#B4640F;
}}
:root[data-theme="dark"]{
  --ground:#0C1013; --panel:#141A1E; --panel-2:#10161A; --line:#232D33;
  --ink:#ECEFEC; --muted:#7E8C92; --dim:#55636A;
  --signal:#E0A64B; --signal-wash:rgba(224,166,75,.12); --signal-line:#3A3322;
  --ok:#5FB98C; --ok-wash:rgba(95,185,140,.13); --hot:#E08A4B;
}
:root[data-theme="light"]{
  --ground:#EEF1F0; --panel:#FFFFFF; --panel-2:#F6F8F7; --line:#DFE5E3;
  --ink:#141A1C; --muted:#586369; --dim:#8A959A;
  --signal:#A66A12; --signal-wash:rgba(166,106,18,.10); --signal-line:#E7DCC7;
  --ok:#2E7D57; --ok-wash:rgba(46,125,87,.10); --hot:#B4640F;
}
*{box-sizing:border-box;}
html{-webkit-text-size-adjust:100%;}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.6;
  -webkit-font-smoothing:antialiased;
  background-image:radial-gradient(1100px 500px at 50% -8%, rgba(224,166,75,.05), transparent 70%);}
:root[data-theme="light"] body,@media (prefers-color-scheme:light){body{background-image:radial-gradient(1100px 500px at 50% -8%, rgba(166,106,18,.05), transparent 70%);}}
.wrap{max-width:900px;margin:0 auto;padding:clamp(28px,6vw,72px) clamp(18px,5vw,40px) 64px;}

.eyebrow{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;
  letter-spacing:.22em;text-transform:uppercase;color:var(--muted);margin-bottom:22px;}
.live{width:7px;height:7px;border-radius:50%;background:var(--signal);
  box-shadow:0 0 0 0 var(--signal);animation:ping 2.4s ease-out infinite;}
@keyframes ping{0%{box-shadow:0 0 0 0 rgba(224,166,75,.5);}70%,100%{box-shadow:0 0 0 7px rgba(224,166,75,0);}}
.masthead h1{font-family:var(--sans);font-size:clamp(30px,6.2vw,58px);line-height:1.04;
  letter-spacing:-0.03em;font-weight:680;margin:0 0 18px;max-width:16ch;text-wrap:balance;}
.lede{color:var(--muted);font-size:clamp(15px,2.2vw,17.5px);max-width:60ch;margin:0;}

.readout{margin:clamp(34px,6vw,56px) 0 26px;padding:clamp(22px,4vw,34px) clamp(20px,4vw,34px) 24px;
  background:linear-gradient(180deg,var(--panel),var(--panel-2));border:1px solid var(--line);
  border-radius:20px;position:relative;overflow:hidden;}
.readout::before{content:"";position:absolute;inset:0 0 auto 0;height:1px;
  background:linear-gradient(90deg,transparent,var(--signal),transparent);opacity:.5;}
.ro-label{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);}
.ro-figure{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink);
  display:flex;align-items:baseline;gap:2px;margin:10px 0 12px;line-height:1;}
.ro-figure .cur{font-size:clamp(26px,5vw,40px);color:var(--signal);align-self:flex-start;margin-top:.35em;}
.ro-figure .amt{font-size:clamp(56px,15vw,124px);letter-spacing:-0.03em;}
.ro-figure .dec{font-size:clamp(26px,5vw,44px);color:var(--muted);}
.ro-foot{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:12px;color:var(--dim);}

.gauges{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:26px;}
@media (max-width:520px){.gauges{grid-template-columns:1fr;}}
.gauge{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 16px 15px;}
.gauge .gv{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:clamp(26px,4.5vw,34px);
  font-weight:600;letter-spacing:-0.02em;line-height:1;}
.gauge .gl{font-size:12.5px;color:var(--ink);margin-top:9px;font-weight:550;}
.gauge .gs{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-top:2px;}

.signal{background:var(--panel);border:1px solid var(--line);border-radius:18px;
  padding:clamp(18px,3.5vw,26px);margin-bottom:26px;}
.sig-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px;}
.sig-title{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);}
.sig-sub{font-size:13.5px;color:var(--ink);margin-top:4px;}
.chip{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.04em;padding:5px 11px;border-radius:20px;white-space:nowrap;}
.chip.ok{color:var(--ok);background:var(--ok-wash);border:1px solid var(--ok);}
.chip.hot{color:var(--hot);background:var(--signal-wash);border:1px solid var(--hot);}
.chip .ping{width:7px;height:7px;border-radius:50%;background:currentColor;animation:ping2 2.4s ease-out infinite;}
@keyframes ping2{0%{box-shadow:0 0 0 0 currentColor;}70%,100%{box-shadow:0 0 0 6px transparent;}}
.spark{width:100%;height:auto;display:block;margin:2px 0 4px;}
.spark .base{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke;}
.spark .area{fill:var(--signal-wash);}
.spark .line{fill:none;stroke:var(--signal);stroke-width:2;vector-effect:non-scaling-stroke;
  stroke-linejoin:round;stroke-linecap:round;stroke-dasharray:2200;stroke-dashoffset:0;}
.spark .dot{fill:var(--signal);}
.sig-note{font-size:13px;color:var(--muted);margin:12px 0 0;max-width:66ch;}

.opnote{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:start;margin-bottom:30px;
  padding:16px 18px;background:var(--panel-2);border:1px solid var(--line);border-radius:14px;}
.opnote .op-tick{width:3px;align-self:stretch;border-radius:2px;background:var(--signal);min-height:100%;}
.opnote p{margin:0;font-size:14px;color:var(--muted);max-width:64ch;}
.how{border-top:1px solid var(--line);padding-top:30px;}
.how-lead{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:18px;}
.principles{display:grid;grid-template-columns:1fr 1fr;gap:24px 34px;}
@media (max-width:640px){.principles{grid-template-columns:1fr;gap:20px;}}
.pr .pt{font-family:var(--mono);font-size:13.5px;font-weight:600;color:var(--ink);margin-bottom:6px;
  padding-left:14px;position:relative;}
.pr .pt::before{content:"";position:absolute;left:0;top:.42em;width:6px;height:6px;border-radius:1px;
  background:var(--signal);}
.pr .pd{font-size:14px;color:var(--muted);padding-left:14px;max-width:46ch;}

.foot{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:11.5px;line-height:1.7;color:var(--dim);max-width:70ch;}

@media (prefers-reduced-motion:reduce){
  .live,.chip .ping{animation:none;}
  .spark .line{stroke-dasharray:none;}
}
"""

_JS = r"""
(function(){
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var amt = document.querySelector('.amt');
  if(amt){
    var target = parseFloat(amt.getAttribute('data-target')) || 0;
    var whole = Math.floor(target);
    if(reduce || whole < 1){ amt.textContent = whole.toLocaleString(); }
    else {
      var t0 = null, dur = 900;
      function tick(ts){
        if(!t0) t0 = ts;
        var k = Math.min(1,(ts-t0)/dur);
        var e = 1-Math.pow(1-k,3);
        amt.textContent = Math.round(e*whole).toLocaleString();
        if(k<1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }
  }
  var line = document.querySelector('.spark .line');
  if(line && !reduce){
    line.style.strokeDashoffset = '2200';
    requestAnimationFrame(function(){
      line.style.transition = 'stroke-dashoffset 1.15s ease-out';
      line.style.strokeDashoffset = '0';
    });
  }
})();
"""


if __name__ == "__main__":
    build()
