"""Standalone HTML report builder for QAlchemist runs — a polished, shareable quality report."""
from datetime import datetime

SEVERITY = {
    "critical": "#fb7185", "high": "#fb7185", "medium": "#fbbf24", "low": "#94a3b8",
}
STATUS_META = {
    "passed": ("PASSED", "#34d399", "#0d2b22"),
    "healed": ("HEALED", "#fbbf24", "#2b220d"),
    "defect": ("DEFECT", "#fb7185", "#2b1418"),
    "review": ("NEEDS REVIEW", "#94a3b8", "#1e293b"),
    "failed": ("FAILED", "#fb7185", "#2b1418"),
}


def build_html_report(run: dict, report: dict, origin: str = "") -> str:
    s = report.get("summary", {})
    run = run or {}

    defects = report.get("defects", [])
    gaps = report.get("coverage_gaps", [])
    healer = report.get("healer_actions", [])
    execs = report.get("executions", [])
    prd_gaps = report.get("prd_gaps", [])
    exec_by_id = {e.get("id"): e for e in execs}

    pass_rate = s.get("pass_rate", 0)
    risk = s.get("untested_risk_index", 0)
    verdict, verdict_color = _verdict(pass_rate, risk)
    created = _fmt_date(report.get("created_at", ""))

    metric_cards = "".join(_metric_card(k, v) for k, v in [
        ("Total Flows", s.get("total_flows", 0)),
        ("Passed", s.get("passed", 0)),
        ("Self-Healed", s.get("healed", 0)),
        ("Defects", s.get("defects", 0)),
        ("Needs Review", s.get("needs_review", 0)),
        ("Coverage Gaps", s.get("coverage_gaps", 0)),
    ])

    defect_cards = "".join(_defect_card(d, exec_by_id, origin) for d in defects) or _empty("No genuine application defects flagged — nice work.")
    healer_cards = "".join(_healer_card(a) for a in healer) or _empty("No self-heal actions were needed this run.")
    gap_cards = "".join(_gap_card(g) for g in gaps) or _empty("No coverage gaps identified.")
    prd_items = "".join(f'<li>{_esc(g)}</li>' for g in prd_gaps) or '<li class="muted">No PRD requirements missed by the plan.</li>'
    exec_cards = "".join(_exec_card(e, origin) for e in execs) or _empty("No executions recorded.")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QAlchemist Report — {_esc(run.get('url',''))}</title>
<style>
  :root {{
    --bg:#07090e; --panel:#0d131f; --panel-2:#0b111c; --border:#1e293b; --border-soft:#17202f;
    --text:#e2e8f0; --muted:#7c8aa3; --accent:#34d399;
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,'Inter',system-ui,sans-serif;
          margin:0; padding:0 0 64px; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:0 28px; }}

  .hero {{ padding:44px 0 32px; border-bottom:1px solid var(--border); margin-bottom:32px; }}
  .kicker {{ font-family:ui-monospace,monospace; font-size:11px; letter-spacing:2px; text-transform:uppercase;
             color:var(--accent); margin-bottom:10px; }}
  h1 {{ font-size:28px; margin:0 0 8px; letter-spacing:-0.02em; }}
  .sub {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:13px; }}
  .sub a {{ color:#7dd3fc; text-decoration:none; }}

  .verdict-row {{ display:flex; align-items:center; gap:24px; margin-top:28px; flex-wrap:wrap; }}
  .ring-wrap {{ display:flex; align-items:center; gap:16px; }}
  .ring-num {{ font-size:34px; font-weight:800; }}
  .ring-label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-top:2px; }}
  .verdict-pill {{ padding:8px 16px; border-radius:999px; font-size:13px; font-weight:700; border:1px solid; }}

  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:40px; }}
  @media (max-width:640px) {{ .grid {{ grid-template-columns:repeat(2,1fr); }} }}
  .card {{ background:var(--panel-2); border:1px solid var(--border-soft); border-radius:14px; padding:18px; }}
  .card .k {{ font-size:26px; font-weight:800; color:var(--text); }}
  .card .l {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:1px; margin-top:6px; }}

  section {{ margin-bottom:40px; }}
  h2 {{ display:flex; align-items:center; gap:10px; font-size:15px; margin:0 0 16px;
        text-transform:uppercase; letter-spacing:1px; color:#cbd5e1; }}
  h2 .count {{ font-family:ui-monospace,monospace; font-size:11px; color:var(--muted); font-weight:400; }}

  .item {{ background:var(--panel-2); border:1px solid var(--border-soft); border-left:3px solid var(--border);
           border-radius:10px; padding:14px 16px; margin-bottom:10px; }}
  .item-top {{ display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
  .item-title {{ font-weight:600; font-size:14px; }}
  .item-detail {{ color:var(--muted); font-size:13px; margin-top:6px; line-height:1.5; }}
  .badge {{ font-family:ui-monospace,monospace; font-size:10px; font-weight:700; text-transform:uppercase;
            padding:3px 8px; border-radius:6px; letter-spacing:0.5px; white-space:nowrap; }}
  .heal-diff {{ font-family:ui-monospace,monospace; font-size:12px; margin-top:8px; display:flex;
                align-items:center; gap:8px; flex-wrap:wrap; }}
  .heal-diff .old {{ color:#fb7185; text-decoration:line-through; }}
  .heal-diff .new {{ color:#34d399; }}
  .thumb {{ display:block; margin-top:10px; border-radius:8px; border:1px solid var(--border); max-width:220px; }}
  .links {{ margin-top:8px; display:flex; gap:14px; }}
  .links a {{ color:#7dd3fc; text-decoration:none; font-family:ui-monospace,monospace; font-size:11px; }}
  .links a:hover {{ text-decoration:underline; }}

  .exec-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; }}
  .exec-card {{ background:var(--panel-2); border:1px solid var(--border-soft); border-radius:12px;
                overflow:hidden; }}
  .exec-card img {{ width:100%; height:120px; object-fit:cover; object-position:top; display:block;
                     background:#000; border-bottom:1px solid var(--border-soft); }}
  .exec-body {{ padding:12px 14px; }}
  .exec-name {{ font-size:13px; font-weight:600; margin-bottom:6px; }}
  .exec-meta {{ display:flex; align-items:center; justify-content:space-between; font-family:ui-monospace,monospace; font-size:11px; color:var(--muted); }}

  .muted {{ color:var(--muted); }}
  ul {{ margin:0; padding-left:0; list-style:none; }}
  li {{ padding:8px 0; border-bottom:1px solid var(--border-soft); font-size:13px; color:#cbd5e1; }}
  li:last-child {{ border-bottom:none; }}
  .empty {{ color:var(--muted); font-size:13px; padding:16px; text-align:center; border:1px dashed var(--border);
            border-radius:10px; }}
  footer {{ text-align:center; color:var(--muted); font-family:ui-monospace,monospace; font-size:11px;
            margin-top:56px; }}
</style></head><body>
<div class="wrap">

  <div class="hero">
    <div class="kicker">QAlchemist · Autonomous Test Quality Report</div>
    <h1>{_esc(run.get('url','') or 'Untitled Run')}</h1>
    <div class="sub">Mode: {_esc(run.get('auth_mode','public'))} · Generated {_esc(created)}</div>

    <div class="verdict-row">
      <div class="ring-wrap">
        {_donut_svg(pass_rate)}
        <div>
          <div class="ring-label">Pass Rate</div>
          <div class="ring-num">{pass_rate}%</div>
        </div>
      </div>
      <span class="verdict-pill" style="color:{verdict_color};border-color:{verdict_color}44;background:{verdict_color}14;">{verdict}</span>
      <span class="verdict-pill" style="color:#7dd3fc;border-color:#7dd3fc44;background:#7dd3fc14;">Risk Index {risk}/100</span>
    </div>
  </div>

  <div class="grid">{metric_cards}</div>

  <section>
    <h2>Classified Defects <span class="count">{len(defects)}</span></h2>
    {defect_cards}
  </section>

  <section>
    <h2>Self-Healer Actions <span class="count">{len(healer)}</span></h2>
    {healer_cards}
  </section>

  <section>
    <h2>Coverage Gaps <span class="count">{len(gaps)}</span></h2>
    {gap_cards}
  </section>

  <section>
    <h2>PRD Coverage Gaps <span class="count">{len(prd_gaps)}</span></h2>
    <ul>{prd_items}</ul>
  </section>

  <section>
    <h2>Execution Results <span class="count">{len(execs)}</span></h2>
    <div class="exec-grid">{exec_cards}</div>
  </section>

  <footer>Generated by QAlchemist — Autonomous Test Orchestration</footer>
</div>
</body></html>"""


def _verdict(pass_rate, risk):
    if pass_rate >= 90 and risk < 20:
        return "HEALTHY", "#34d399"
    if pass_rate >= 60 and risk < 50:
        return "NEEDS ATTENTION", "#fbbf24"
    return "AT RISK", "#fb7185"


def _fmt_date(iso_str):
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).strftime("%b %d, %Y · %H:%M UTC")
    except Exception:
        return iso_str or "unknown"


def _donut_svg(pct, size=64, stroke=7):
    pct = max(0, min(100, pct or 0))
    r = (size - stroke) / 2
    c = 2 * 3.14159265 * r
    offset = c * (1 - pct / 100)
    color = "#34d399" if pct >= 80 else "#fbbf24" if pct >= 50 else "#fb7185"
    cx = cy = size / 2
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#1e293b" stroke-width="{stroke}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-dasharray="{c:.1f}" stroke-dashoffset="{offset:.1f}" stroke-linecap="round" '
            f'transform="rotate(-90 {cx} {cy})"/></svg>')


def _metric_card(label, value):
    return f'<div class="card"><div class="k">{_esc(value)}</div><div class="l">{_esc(label)}</div></div>'


def _empty(text):
    return f'<div class="empty">{_esc(text)}</div>'


def _artifact_link(origin, path, label):
    if not path:
        return ""
    href = f"{origin}{path}" if origin else path
    return f'<a href="{_esc(href)}" target="_blank" rel="noopener">{_esc(label)}</a>'


def _defect_card(d, exec_by_id, origin):
    sev = str(d.get("severity", "medium")).lower()
    color = SEVERITY.get(sev, "#94a3b8")
    ex = exec_by_id.get(d.get("execution_id"), {})
    art = ex.get("artifacts") or {}
    thumb = f'<img class="thumb" src="{_esc((origin + art["screenshot"]) if origin else art["screenshot"])}" alt="screenshot">' if art.get("screenshot") else ""
    links = " ".join(filter(None, [
        _artifact_link(origin, art.get("video"), "▸ video"),
        _artifact_link(origin, art.get("trace"), "▸ trace"),
    ]))
    return f"""<div class="item" style="border-left-color:{color};">
      <div class="item-top">
        <span class="item-title">{_esc(d.get('flow_name',''))}</span>
        <span class="badge" style="color:{color};background:{color}1a;">{_esc(sev)} · {_esc(d.get('fail_type',''))}</span>
      </div>
      <div class="item-detail">{_esc(d.get('rationale',''))} <span class="muted">(confidence {_esc(d.get('confidence',''))})</span></div>
      {thumb}
      {f'<div class="links">{links}</div>' if links else ''}
    </div>"""


def _healer_card(a):
    heal = a.get("heal") or {}
    diff = ""
    if heal.get("old_selector") or heal.get("new_selector"):
        diff = (f'<div class="heal-diff"><span class="old">{_esc(heal.get("old_selector",""))}</span>'
                f' → <span class="new">{_esc(heal.get("new_selector",""))}</span></div>')
    decision = a.get("decision", "")
    color = "#34d399" if decision == "script" else "#fb7185" if decision == "defect" else "#94a3b8"
    return f"""<div class="item" style="border-left-color:{color};">
      <div class="item-top">
        <span class="item-title">{_esc(a.get('flow_name',''))}</span>
        <span class="badge" style="color:{color};background:{color}1a;">{_esc(decision)}</span>
      </div>
      <div class="item-detail">{_esc(a.get('rationale',''))} <span class="muted">(confidence {_esc(a.get('confidence',''))})</span></div>
      {diff}
    </div>"""


def _gap_card(g):
    sev = str(g.get("severity", "medium")).lower()
    color = SEVERITY.get(sev, "#94a3b8")
    return f"""<div class="item" style="border-left-color:{color};">
      <div class="item-top">
        <span class="item-title">{_esc(g.get('area',''))}</span>
        <span class="badge" style="color:{color};background:{color}1a;">{_esc(sev)}</span>
      </div>
      <div class="item-detail">{_esc(g.get('detail',''))}</div>
    </div>"""


def _exec_card(e, origin):
    status = e.get("final_status") or e.get("status") or "failed"
    label, color, bg = STATUS_META.get(status, STATUS_META["failed"])
    art = e.get("artifacts") or {}
    shot = art.get("screenshot")
    img = f'<img src="{_esc((origin + shot) if origin else shot)}" alt="">' if shot else ""
    return f"""<div class="exec-card">
      {img}
      <div class="exec-body">
        <div class="exec-name">{_esc(e.get('flow_name',''))}</div>
        <div class="exec-meta">
          <span style="color:{color};font-weight:700;">{label}</span>
          <span>{_esc(e.get('duration',''))}s</span>
        </div>
      </div>
    </div>"""


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
