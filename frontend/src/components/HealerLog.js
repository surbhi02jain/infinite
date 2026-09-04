import { Wrench, AlertOctagon, HelpCircle, ArrowRight } from "lucide-react";
import { Empty, SectionHeader } from "@/components/TestPlanView";

const DECISION = {
  script: { icon: Wrench, cls: "text-amber-300 border-amber-500/30 bg-amber-950/30", label: "SCRIPT ISSUE · HEALED", bar: "bg-amber-400" },
  defect: { icon: AlertOctagon, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "APP DEFECT", bar: "bg-rose-500" },
  review: { icon: HelpCircle, cls: "text-slate-300 border-slate-600 bg-slate-800/30", label: "NEEDS REVIEW", bar: "bg-slate-400" },
};
const SEV = { critical: "text-rose-300", high: "text-rose-300", medium: "text-amber-300", low: "text-slate-400" };

export default function HealerLog({ healer }) {
  if (!healer.length) return <Empty text="No failures yet — Healer decisions will stream here…" />;
  return (
    <div className="space-y-3 max-w-4xl">
      <SectionHeader title="Self-Healing & Defect Classification" sub="Heuristic rules first, LLM confirmation second — honest, confidence-scored" />
      {healer.map((a) => {
        const meta = DECISION[a.decision] || DECISION.review;
        const Icon = meta.icon;
        const conf = Math.round((a.confidence || 0) * 100);
        return (
          <div key={a.id} data-testid={`healer-action-${a.flow_id}`} className={`rounded-xl border p-4 ${meta.cls}`}>
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="flex items-center gap-2.5 min-w-0">
                <Icon className="w-4 h-4 shrink-0" />
                <span className="font-heading text-[14px] font-semibold text-slate-100 truncate">{a.flow_name}</span>
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-slate-800/60 text-slate-400">{a.fail_type}</span>
              </div>
              <span className={`shrink-0 px-2 py-0.5 rounded border font-mono text-[10px] font-bold ${meta.cls}`}>{meta.label}</span>
            </div>

            {/* confidence meter */}
            <div className="mb-3">
              <div className="flex items-center justify-between font-mono text-[10px] mb-1">
                <span className="text-slate-500 uppercase tracking-widest">Confidence</span>
                <span className="text-slate-200">{conf}%{a.decision === "defect" && <span className={`ml-2 uppercase ${SEV[a.severity] || "text-slate-400"}`}>{a.severity}</span>}</span>
              </div>
              <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div className={`h-full ${meta.bar}`} style={{ width: `${conf}%` }} />
              </div>
            </div>

            {a.heal && (a.heal.old_selector || a.heal.new_selector) && (
              <div className="mb-3 flex items-center gap-2 font-mono text-[11px] rounded-lg bg-[#07090e] border border-slate-800 p-2.5 overflow-x-auto">
                <span className="text-rose-300 line-through whitespace-nowrap">{a.heal.old_selector}</span>
                <ArrowRight className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                <span className="text-emerald-300 whitespace-nowrap">{a.heal.new_selector}</span>
              </div>
            )}

            <p className="text-[13px] text-slate-300 leading-relaxed">{a.rationale}</p>
            {a.result && (
              a.decision === "script" ? (
                <p className="mt-2 text-[12px] text-emerald-300 font-mono">✓ {a.result}</p>
              ) : (
                <p className="mt-2 text-[12px] text-amber-300 font-mono">⚠ {a.result}</p>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}
