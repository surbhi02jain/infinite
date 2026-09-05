import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RTooltip } from "recharts";
import { FileJson, FileCode2, ShieldAlert, Gauge, Target, PieChart as PieIcon, Repeat } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/api";
import { Empty } from "@/components/TestPlanView";
import { Button } from "@/components/ui/button";

const BREAKDOWN_COLORS = { Passed: "#34d399", "Self-Healed": "#fbbf24", Defects: "#fb7185", "Needs Review": "#94a3b8" };

function useCountUp(target, duration = 700) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    const to = Number(target) || 0;
    let raf;
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(Math.round(to * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return val;
}

function MetricCard({ k, v, suffix, cls }) {
  const animated = useCountUp(v);
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      data-testid={`metric-${k.toLowerCase().replace(/ /g, "-")}`} className="rounded-xl border border-slate-800 bg-[#0b111c] p-4">
      <div className={`font-heading text-3xl font-bold ${cls}`}>{animated}{suffix || ""}</div>
      <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500 mt-1">{k}</div>
    </motion.div>
  );
}

export default function FinalReport({ report, runId, run, onViewEvidence }) {
  if (!report) return <Empty text="Final quality report generates at the end of the pipeline…" />;
  const s = report.summary || {};
  const hadPrd = Boolean(run?.config?.prd);

  const exportReport = async (fmt) => {
    try {
      const res = await fetch(`${API}/runs/${runId}/export?fmt=${fmt}`);
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `qalchemist-${runId.slice(0, 8)}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Exported ${fmt.toUpperCase()} report`);
    } catch (err) {
      toast.error(err.message || "Export failed");
    }
  };

  const metrics = [
    { k: "Pass Rate", v: s.pass_rate, suffix: "%", cls: "text-emerald-400" },
    { k: "Total Flows", v: s.total_flows, cls: "text-slate-100" },
    { k: "Passed", v: s.passed, cls: "text-emerald-400" },
    { k: "Self-Healed", v: s.healed, cls: "text-amber-300" },
    { k: "Defects", v: s.defects, cls: "text-rose-400" },
    { k: "Needs Review", v: s.needs_review, cls: "text-slate-300" },
    { k: "Coverage Gaps", v: s.coverage_gaps, cls: "text-cyan-400" },
  ];

  const breakdown = [
    { name: "Passed", value: s.passed || 0 },
    { name: "Self-Healed", value: s.healed || 0 },
    { name: "Defects", value: s.defects || 0 },
    { name: "Needs Review", value: s.needs_review || 0 },
  ].filter((d) => d.value > 0);

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
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
        {metrics.map((m) => <MetricCard key={m.k} {...m} />)}
      </div>

      {/* risk index / breakdown / prd */}
      <div className="grid md:grid-cols-3 gap-4">
        <div className="rounded-xl border border-slate-800 bg-[#0b111c] p-5">
          <div className="flex items-center gap-2 mb-3"><Gauge className="w-4 h-4 text-cyan-400" /><span className="font-heading text-sm font-semibold text-slate-200">Untested-Flow Risk Index</span></div>
          <div className="flex items-end gap-3">
            <span className={`font-heading text-4xl font-bold ${riskColor(s.untested_risk_index)}`}>{s.untested_risk_index}</span>
            <span className="text-slate-500 text-sm mb-1.5">/ 100</span>
          </div>
          <div className="mt-3 h-2 rounded-full bg-slate-800 overflow-hidden">
            <motion.div initial={{ width: 0 }} animate={{ width: `${s.untested_risk_index}%` }} transition={{ duration: 0.6, ease: "easeOut" }}
              className={`h-full ${riskBar(s.untested_risk_index)}`} />
          </div>
          <p className="mt-2 text-[12px] text-slate-500">Derived from {s.coverage_gaps} coverage gaps, {s.defects} defects & {s.needs_review} unresolved items.</p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-[#0b111c] p-5">
          <div className="flex items-center gap-2 mb-3"><PieIcon className="w-4 h-4 text-emerald-400" /><span className="font-heading text-sm font-semibold text-slate-200">Execution Breakdown</span></div>
          {breakdown.length ? (
            <div className="flex items-center gap-4">
              <div className="w-24 h-24 shrink-0">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={breakdown} dataKey="value" nameKey="name" innerRadius={28} outerRadius={44}
                      paddingAngle={breakdown.length > 1 ? 3 : 0} isAnimationActive animationDuration={700} stroke="none">
                      {breakdown.map((d) => <Cell key={d.name} fill={BREAKDOWN_COLORS[d.name]} />)}
                    </Pie>
                    <RTooltip contentStyle={{ background: "#0d131f", border: "1px solid #1e293b", borderRadius: 8, fontSize: 11 }}
                      itemStyle={{ color: "#e2e8f0" }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-1.5 min-w-0">
                {breakdown.map((d) => (
                  <div key={d.name} className="flex items-center gap-2 text-[12px]">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: BREAKDOWN_COLORS[d.name] }} />
                    <span className="text-slate-400 truncate">{d.name}</span>
                    <span className="text-slate-200 font-mono ml-auto">{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : <p className="text-[13px] text-slate-500">No executions recorded.</p>}
        </div>

        <div className="rounded-xl border border-slate-800 bg-[#0b111c] p-5">
          <div className="flex items-center gap-2 mb-3"><Target className="w-4 h-4 text-violet-400" /><span className="font-heading text-sm font-semibold text-slate-200">PRD Coverage</span></div>
          {report.prd_gaps?.length ? (
            <div className="space-y-1.5">
              {report.prd_gaps.map((p, i) => <div key={i} className="text-[12px] text-violet-200 flex gap-2"><span className="text-violet-500">▸</span>{p}</div>)}
            </div>
          ) : hadPrd ? (
            <p className="text-[13px] text-emerald-400">✓ No PRD requirements missed by the plan.</p>
          ) : (
            <p className="text-[13px] text-slate-500">No PRD was submitted for this run — nothing to check coverage against.</p>
          )}
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
                <th className="text-left p-3">Flow</th><th className="text-left p-3">Type</th><th className="text-left p-3">Severity</th><th className="text-left p-3">Confidence</th><th className="text-left p-3">Rationale</th><th className="text-left p-3"></th>
              </tr></thead>
              <tbody>
                {report.defects.map((d, i) => (
                  <tr key={i} className="border-t border-slate-800 bg-[#0b111c]">
                    <td className="p-3 text-slate-200">{d.flow_name}</td>
                    <td className="p-3 font-mono text-[11px] text-slate-400">{d.fail_type}</td>
                    <td className="p-3"><span className={`font-mono text-[11px] uppercase ${d.severity === "critical" || d.severity === "high" ? "text-rose-300" : "text-amber-300"}`}>{d.severity}</span></td>
                    <td className="p-3 font-mono text-slate-300">{Math.round((d.confidence || 0) * 100)}%</td>
                    <td className="p-3 text-[12px] text-slate-400 max-w-md">{d.rationale}</td>
                    <td className="p-3">
                      {onViewEvidence && (
                        <button type="button" data-testid={`defect-view-evidence-${d.flow_id}`}
                          onClick={() => onViewEvidence(d.flow_id)}
                          className="font-mono text-[11px] text-slate-500 hover:text-cyan-400 whitespace-nowrap">
                          View evidence →
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="text-[13px] text-emerald-400 rounded-lg border border-slate-800 bg-[#0b111c] p-4">✓ No genuine application defects flagged.</p>}
      </div>

      {/* flakiness trend across runs against this same target */}
      {report.flakiness_trend?.length > 0 && (
        <div>
          <h4 className="font-mono text-[11px] uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <Repeat className="w-3.5 h-3.5 text-amber-400" /> Flakiness Trend (this target, across runs)
          </h4>
          <p className="text-[12px] text-slate-500 mb-2">
            A flow needing healing repeatedly is a maintenance smell, not a win — tracked here across every run against this same URL, not just this one.
          </p>
          <div className="rounded-xl border border-slate-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-[#0a0f18]"><tr className="text-slate-500 font-mono text-[10px] uppercase tracking-wider">
                <th className="text-left p-3">Flow</th><th className="text-left p-3">Healed</th><th className="text-left p-3">Runs Seen</th><th className="text-left p-3">Heal Rate</th>
              </tr></thead>
              <tbody>
                {report.flakiness_trend.map((f, i) => (
                  <tr key={i} className="border-t border-slate-800 bg-[#0b111c]">
                    <td className="p-3 text-slate-200">{f.flow_name}</td>
                    <td className="p-3 font-mono text-slate-300">{f.heal_count}</td>
                    <td className="p-3 font-mono text-slate-400">{f.runs_seen}/{f.total_runs}</td>
                    <td className="p-3"><span className={`font-mono text-[11px] ${f.rate >= 50 ? "text-rose-300" : "text-amber-300"}`}>{f.rate}%</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function riskColor(v) { return v >= 60 ? "text-rose-400" : v >= 30 ? "text-amber-400" : "text-emerald-400"; }
function riskBar(v) { return v >= 60 ? "bg-rose-500" : v >= 30 ? "bg-amber-400" : "bg-emerald-400"; }
