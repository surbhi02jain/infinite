import { Download, FileJson, FileCode2, RotateCw, ShieldAlert, Gauge, Target } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/api";
import { Empty } from "@/components/TestPlanView";
import { Button } from "@/components/ui/button";

export default function FinalReport({ report, runId }) {
  if (!report) return <Empty text="Final quality report generates at the end of the pipeline…" />;
  const s = report.summary || {};

  const exportReport = async (fmt) => {
    try {
      const res = await fetch(`${API}/runs/${runId}/export?fmt=${fmt}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `autoqa-${runId.slice(0, 8)}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Exported ${fmt.toUpperCase()} report`);
    } catch (_) {
      toast.error("Export failed");
    }
  };

  const metrics = [
    { k: "Pass Rate", v: `${s.pass_rate}%`, cls: "text-emerald-400" },
    { k: "Total Flows", v: s.total_flows, cls: "text-slate-100" },
    { k: "Passed", v: s.passed, cls: "text-emerald-400" },
    { k: "Self-Healed", v: s.healed, cls: "text-amber-300" },
    { k: "Defects", v: s.defects, cls: "text-rose-400" },
    { k: "Needs Review", v: s.needs_review, cls: "text-slate-300" },
  ];

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-heading text-2xl font-bold text-white">Test Quality Report</h3>
          <p className="text-slate-500 text-sm mt-0.5">Autonomous coverage, results, healer actions & untested-flow risk</p>
        </div>
        <div className="flex items-center gap-2">
          <Button data-testid="report-export-json-button" onClick={() => exportReport("json")} size="sm" variant="outline"
            className="h-9 border-slate-700 bg-[#0d131f] text-slate-200 hover:bg-slate-800 hover:text-white">
            <FileJson className="w-4 h-4 mr-1.5" /> JSON
          </Button>
          <Button data-testid="report-export-html-button" onClick={() => exportReport("html")} size="sm"
            className="h-9 bg-emerald-500 hover:bg-emerald-400 text-[#07090e] font-semibold">
            <FileCode2 className="w-4 h-4 mr-1.5" /> HTML
          </Button>
        </div>
      </div>

      {/* metric bento */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {metrics.map((m) => (
          <div key={m.k} data-testid={`metric-${m.k.toLowerCase().replace(/ /g, "-")}`} className="rounded-xl border border-slate-800 bg-[#0b111c] p-4">
            <div className={`font-heading text-3xl font-bold ${m.cls}`}>{m.v}</div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500 mt-1">{m.k}</div>
          </div>
        ))}
      </div>

      {/* risk index */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="rounded-xl border border-slate-800 bg-[#0b111c] p-5">
          <div className="flex items-center gap-2 mb-3"><Gauge className="w-4 h-4 text-cyan-400" /><span className="font-heading text-sm font-semibold text-slate-200">Untested-Flow Risk Index</span></div>
          <div className="flex items-end gap-3">
            <span className={`font-heading text-4xl font-bold ${riskColor(s.untested_risk_index)}`}>{s.untested_risk_index}</span>
            <span className="text-slate-500 text-sm mb-1.5">/ 100</span>
          </div>
          <div className="mt-3 h-2 rounded-full bg-slate-800 overflow-hidden">
            <div className={`h-full ${riskBar(s.untested_risk_index)}`} style={{ width: `${s.untested_risk_index}%` }} />
          </div>
          <p className="mt-2 text-[12px] text-slate-500">Derived from {s.coverage_gaps} coverage gaps, {s.defects} defects & {s.needs_review} unresolved items.</p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-[#0b111c] p-5">
          <div className="flex items-center gap-2 mb-3"><Target className="w-4 h-4 text-violet-400" /><span className="font-heading text-sm font-semibold text-slate-200">PRD Coverage</span></div>
          {report.prd_gaps?.length ? (
            <div className="space-y-1.5">
              {report.prd_gaps.map((p, i) => <div key={i} className="text-[12px] text-violet-200 flex gap-2"><span className="text-violet-500">▸</span>{p}</div>)}
            </div>
          ) : <p className="text-[13px] text-emerald-400">✓ No PRD requirements missed by the plan.</p>}
        </div>
      </div>

      {/* defect matrix */}
      <div>
        <h4 className="font-mono text-[11px] uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
          <ShieldAlert className="w-3.5 h-3.5 text-rose-400" /> Classified Defects
        </h4>
        {report.defects?.length ? (
          <div className="rounded-xl border border-slate-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-[#0a0f18]"><tr className="text-slate-500 font-mono text-[10px] uppercase tracking-wider">
                <th className="text-left p-3">Flow</th><th className="text-left p-3">Type</th><th className="text-left p-3">Severity</th><th className="text-left p-3">Confidence</th><th className="text-left p-3">Rationale</th>
              </tr></thead>
              <tbody>
                {report.defects.map((d, i) => (
                  <tr key={i} className="border-t border-slate-800 bg-[#0b111c]">
                    <td className="p-3 text-slate-200">{d.flow_name}</td>
                    <td className="p-3 font-mono text-[11px] text-slate-400">{d.fail_type}</td>
                    <td className="p-3"><span className={`font-mono text-[11px] uppercase ${d.severity === "critical" || d.severity === "high" ? "text-rose-300" : "text-amber-300"}`}>{d.severity}</span></td>
                    <td className="p-3 font-mono text-slate-300">{Math.round((d.confidence || 0) * 100)}%</td>
                    <td className="p-3 text-[12px] text-slate-400 max-w-md">{d.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="text-[13px] text-emerald-400 rounded-lg border border-slate-800 bg-[#0b111c] p-4">✓ No genuine application defects flagged.</p>}
      </div>
    </div>
  );
}

function riskColor(v) { return v >= 60 ? "text-rose-400" : v >= 30 ? "text-amber-400" : "text-emerald-400"; }
function riskBar(v) { return v >= 60 ? "bg-rose-500" : v >= 30 ? "bg-amber-400" : "bg-emerald-400"; }
