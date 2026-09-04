import { ChevronRight, Sparkles } from "lucide-react";

const TYPE_STYLE = {
  happy: "text-emerald-400 bg-emerald-950/50 border-emerald-500/30",
  edge: "text-cyan-400 bg-cyan-950/50 border-cyan-500/30",
  error: "text-rose-400 bg-rose-950/50 border-rose-500/30",
};
const PRIORITY_STYLE = { high: "text-rose-300", medium: "text-amber-300", low: "text-slate-400" };

export default function TestPlanView({ flows }) {
  if (!flows.length) return <Empty text="Planner will stream discovered user flows here…" />;
  return (
    <div className="space-y-3 max-w-4xl">
      <SectionHeader title="Test Plan" sub={`${flows.length} flows synthesized from the discovered surface`} />
      {flows.map((f) => (
        <div key={f.flow_id} data-testid={`plan-flow-${f.flow_id}`}
          className="rounded-xl border border-slate-800 bg-[#0b111c] p-4 hover:border-slate-700 transition-colors duration-200">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="font-mono text-[10px] text-slate-500">{f.flow_id}</span>
              <span className={`px-2 py-0.5 rounded border text-[10px] font-mono uppercase ${TYPE_STYLE[f.type] || TYPE_STYLE.edge}`}>{f.type}</span>
              <h4 className="font-heading text-[15px] font-semibold text-slate-100 truncate">{f.name}</h4>
              {f.added_by_evaluator && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-violet-950/60 border border-violet-500/30 text-violet-300 text-[9px] font-mono">
                  <Sparkles className="w-2.5 h-2.5" /> AUTO-ADDED
                </span>
              )}
            </div>
            <span className={`font-mono text-[10px] uppercase shrink-0 ${PRIORITY_STYLE[f.priority] || "text-slate-400"}`}>{f.priority}</span>
          </div>
          <ol className="mt-3 space-y-1">
            {(f.steps || []).map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-[13px] text-slate-300">
                <ChevronRight className="w-3.5 h-3.5 text-slate-600 mt-0.5 shrink-0" />
                <span>{s}</span>
              </li>
            ))}
          </ol>
          <div className="mt-3 pt-3 border-t border-slate-800/70 text-[12px] text-slate-400">
            <span className="font-mono text-[10px] uppercase text-slate-500">Expected · </span>{f.expected_outcome}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Empty({ text }) {
  return <div className="flex items-center justify-center h-64 text-slate-600 text-sm font-mono">{text}</div>;
}
export function SectionHeader({ title, sub }) {
  return (
    <div className="mb-4">
      <h3 className="font-heading text-xl font-semibold text-white">{title}</h3>
      {sub && <p className="text-slate-500 text-sm mt-0.5">{sub}</p>}
    </div>
  );
}
