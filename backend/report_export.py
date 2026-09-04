"""Standalone HTML report builder for AutoQA runs."""


def build_html_report(run: dict, report: dict) -> str:
    s = report.get("summary", {})
    run = run or {}

    def rows(items, cols):
        out = ""
        for it in items:
            out += "<tr>" + "".join(f"<td>{_esc(it.get(c, ''))}</td>" for c in cols) + "</tr>"
        return out or "<tr><td colspan='9' class='muted'>None</td></tr>"

    defects = report.get("defects", [])
    gaps = report.get("coverage_gaps", [])
    healer = report.get("healer_actions", [])
    execs = report.get("executions", [])
    prd_gaps = report.get("prd_gaps", [])

    metric_cards = "".join(f"""
      <div class="card">
        <div class="k">{v}</div><div class="l">{k}</div>
      </div>""" for k, v in [
        ("Pass Rate %", s.get("pass_rate", 0)),
        ("Total Flows", s.get("total_flows", 0)),
        ("Passed", s.get("passed", 0)),
        ("Self-Healed", s.get("healed", 0)),
        ("Defects", s.get("defects", 0)),
        ("Needs Review", s.get("needs_review", 0)),
        ("Coverage Gaps", s.get("coverage_gaps", 0)),
        ("Untested Risk Index", s.get("untested_risk_index", 0)),
    ])

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>AutoQA Report — {_esc(run.get('url',''))}</title>
<style>
  body{{background:#07090e;color:#e2e8f0;font-family:'IBM Plex Sans',system-ui,sans-serif;margin:0;padding:40px;}}
  h1{{font-size:26px;margin:0 0 4px;}} .sub{{color:#64748b;font-family:monospace;margin-bottom:28px;}}
  .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:32px;}}
  .card{{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:18px;}}
  .card .k{{font-size:30px;font-weight:700;color:#34d399;}} .card .l{{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-top:6px;}}
  h2{{border-bottom:1px solid #1e293b;padding-bottom:8px;margin-top:36px;font-size:18px;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px;}}
  th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid #1e293b;}} th{{color:#94a3b8;text-transform:uppercase;font-size:11px;letter-spacing:1px;}}
  .muted{{color:#64748b;}} .pill{{padding:2px 8px;border-radius:20px;font-size:11px;font-family:monospace;}}
  li{{margin:6px 0;}}
</style></head><body>
  <h1>AutoQA — Autonomous Test Quality Report</h1>
  <div class="sub">Target: {_esc(run.get('url',''))} · Mode: {_esc(run.get('auth_mode',''))} · Generated: {_esc(report.get('created_at',''))}</div>
  <div class="grid">{metric_cards}</div>

  <h2>Classified Defects (confidence-scored)</h2>
  <table><tr><th>Flow</th><th>Fail Type</th><th>Severity</th><th>Confidence</th><th>Rationale</th></tr>
    {rows(defects, ['flow_name','fail_type','severity','confidence','rationale'])}
  </table>

  <h2>Self-Healer Actions</h2>
  <table><tr><th>Flow</th><th>Decision</th><th>Confidence</th><th>Rationale</th></tr>
    {rows(healer, ['flow_name','decision','confidence','rationale'])}
  </table>

  <h2>Coverage Gaps</h2>
  <table><tr><th>Area</th><th>Severity</th><th>Detail</th></tr>
    {rows(gaps, ['area','severity','detail'])}
  </table>

  <h2>PRD Coverage Gaps</h2>
  <ul>{''.join(f'<li>{_esc(g)}</li>' for g in prd_gaps) or '<li class=muted>No PRD gaps.</li>'}</ul>

  <h2>Execution Results</h2>
  <table><tr><th>Flow</th><th>Type</th><th>Status</th><th>Final</th><th>Duration</th></tr>
    {rows(execs, ['flow_name','flow_type','status','final_status','duration'])}
  </table>
</body></html>"""


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
