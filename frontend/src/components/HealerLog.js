import { useState } from "react";
import { Wrench, AlertOctagon, HelpCircle, CircleSlash, ArrowRight, Loader2, ImageIcon } from "lucide-react";
import { toast } from "sonner";
import { api, ARTIFACT_BASE } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Empty, SectionHeader } from "@/components/TestPlanView";

const DECISION = {
  script: { icon: Wrench, cls: "text-amber-300 border-amber-500/30 bg-amber-950/30", label: "SCRIPT ISSUE · HEALED", bar: "bg-amber-400" },
  defect: { icon: AlertOctagon, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "APP DEFECT", bar: "bg-rose-500" },
  review: { icon: HelpCircle, cls: "text-slate-300 border-slate-600 bg-slate-800/30", label: "NEEDS REVIEW", bar: "bg-slate-400" },
  dismissed: { icon: CircleSlash, cls: "text-slate-500 border-slate-700 bg-slate-900/40", label: "DISMISSED", bar: "bg-slate-600" },
};
const SEV = { critical: "text-rose-300", high: "text-rose-300", medium: "text-amber-300", low: "text-slate-400" };

function ReviewActions({ action, runId, onEventAppend }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(null); // "defect" | "dismissed" | null

  const resolve = async (resolution) => {
    setBusy(resolution);
    try {
      const { data } = await api.post(`/runs/${runId}/healer-actions/${action.id}/resolve`,
        { resolution, note: note.trim() || null });
      onEventAppend?.(data);
      toast.success(resolution === "defect" ? "Escalated to defect" : "Dismissed as false positive");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Failed to resolve");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-3 pt-3 border-t border-slate-800/70 flex items-center gap-2">
      <Input data-testid={`healer-resolve-note-${action.flow_id}`} value={note} onChange={(e) => setNote(e.target.value)}
        placeholder="Optional note (why?)" disabled={Boolean(busy)}
        className="h-8 flex-1 bg-[#07090e] border-slate-700 text-[12px]" />
      <Button data-testid={`healer-resolve-defect-${action.flow_id}`} size="sm" disabled={Boolean(busy)}
        onClick={() => resolve("defect")}
        className="h-8 bg-rose-500/15 border border-rose-500/40 text-rose-300 hover:bg-rose-500/25 text-[11px] font-mono">
        {busy === "defect" ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <AlertOctagon className="w-3 h-3 mr-1" />}
        Flag as Defect
      </Button>
      <Button data-testid={`healer-resolve-dismiss-${action.flow_id}`} size="sm" variant="outline" disabled={Boolean(busy)}
        onClick={() => resolve("dismissed")}
        className="h-8 border-slate-700 bg-[#0d131f] text-slate-300 hover:bg-slate-800 text-[11px] font-mono">
        {busy === "dismissed" ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <CircleSlash className="w-3 h-3 mr-1" />}
        Dismiss
      </Button>
    </div>
  );
}

export default function HealerLog({ healer, runId, onEventAppend, onViewEvidence }) {
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

            {/* the claim "healed" is only as good as the proof — show it here, not just in the log line */}
            {a.original_artifacts?.screenshot && a.artifacts?.screenshot && (
              <div className="mb-3 flex items-center gap-2" data-testid={`healer-before-after-${a.flow_id}`}>
                <div className="flex flex-col items-center gap-0.5">
                  <img src={`${ARTIFACT_BASE}${a.original_artifacts.screenshot}`} alt="" loading="lazy"
                    className="w-24 h-16 object-cover object-top rounded-lg border border-rose-500/40 bg-black" />
                  <span className="text-[8px] font-mono uppercase text-rose-400">before</span>
                </div>
                <ArrowRight className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                <div className="flex flex-col items-center gap-0.5">
                  <img src={`${ARTIFACT_BASE}${a.artifacts.screenshot}`} alt="" loading="lazy"
                    className="w-24 h-16 object-cover object-top rounded-lg border border-emerald-500/40 bg-black" />
                  <span className="text-[8px] font-mono uppercase text-emerald-400">after</span>
                </div>
              </div>
            )}

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

            {a.decision === "review" && runId && (
              <ReviewActions action={a} runId={runId} onEventAppend={onEventAppend} />
            )}
            {onViewEvidence && (
              <button type="button" data-testid={`healer-view-evidence-${a.flow_id}`}
                onClick={() => onViewEvidence(a.flow_id)}
                className="mt-3 flex items-center gap-1.5 text-[11px] font-mono text-slate-500 hover:text-cyan-400">
                <ImageIcon className="w-3.5 h-3.5" /> View full evidence (steps, video, trace) →
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
