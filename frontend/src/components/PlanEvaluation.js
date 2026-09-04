import { AlertTriangle, FileWarning, Info } from "lucide-react";
import { Empty, SectionHeader } from "@/components/TestPlanView";

const SEV = {
  high: "text-rose-400 bg-rose-950/40 border-rose-500/30",
  medium: "text-amber-400 bg-amber-950/40 border-amber-500/30",
  low: "text-slate-400 bg-slate-800/40 border-slate-700",
};

export default function PlanEvaluation({ gaps, prdGaps, riskNotes }) {
  if (!gaps.length && !prdGaps.length && !riskNotes.length)
    return <Empty text="Plan Evaluator will audit coverage gaps here…" />;
  return (
    <div className="space-y-6 max-w-4xl">
      <SectionHeader title="Coverage Gap Audit" sub="Evaluator critique of the plan before code generation" />

      {gaps.length > 0 && (
        <div>
          <h4 className="font-mono text-[11px] uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400" /> Coverage Gaps
          </h4>
          <div className="space-y-2">
            {gaps.map((g, i) => (
              <div key={i} data-testid={`coverage-gap-${i}`} className="rounded-lg border border-slate-800 bg-[#0b111c] p-3 flex items-start gap-3">
                <span className={`px-2 py-0.5 rounded border text-[10px] font-mono uppercase shrink-0 ${SEV[String(g.severity).toLowerCase()] || SEV.low}`}>{g.severity}</span>
                <div>
                  <div className="text-[13px] font-medium text-slate-200">{g.area}</div>
                  <div className="text-[12px] text-slate-400 mt-0.5">{g.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {prdGaps.length > 0 && (
        <div>
          <h4 className="font-mono text-[11px] uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <FileWarning className="w-3.5 h-3.5 text-violet-400" /> PRD-to-Plan Gaps
          </h4>
          <div className="rounded-lg border border-violet-500/20 bg-violet-950/20 p-3 space-y-1.5">
            {prdGaps.map((p, i) => (
              <div key={i} className="text-[13px] text-violet-200 flex items-start gap-2">
                <span className="text-violet-500 mt-0.5">▸</span>{p}
              </div>
            ))}
          </div>
        </div>
      )}

      {riskNotes.length > 0 && (
        <div>
          <h4 className="font-mono text-[11px] uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <Info className="w-3.5 h-3.5 text-cyan-400" /> Risk Notes
          </h4>
          <div className="space-y-1.5">
            {riskNotes.map((r, i) => (
              <div key={i} className="text-[13px] text-slate-300 rounded-lg border border-slate-800 bg-[#0b111c] p-3">{r}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
