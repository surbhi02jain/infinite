import { CheckCircle2, XCircle, Wrench, HelpCircle, Cpu, Camera, Film, FileArchive } from "lucide-react";
import { Empty, SectionHeader } from "@/components/TestPlanView";

const FINAL = {
  passed: { icon: CheckCircle2, cls: "text-emerald-400 border-emerald-500/30 bg-emerald-950/30", label: "PASSED" },
  healed: { icon: Wrench, cls: "text-amber-300 border-amber-500/30 bg-amber-950/30", label: "HEALED" },
  defect: { icon: XCircle, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "DEFECT" },
  review: { icon: HelpCircle, cls: "text-slate-300 border-slate-600 bg-slate-800/30", label: "REVIEW" },
  failed: { icon: XCircle, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "FAILED" },
};

export default function ExecutionFeed({ executions }) {
  if (!executions.length) return <Empty text="Live pass/fail feed streams here as workers run…" />;
  return (
    <div className="space-y-3 max-w-4xl">
      <SectionHeader title="Live Runner Feed" sub="Headless Chromium execution across parallel workers" />
      {executions.map((e) => {
        const status = e.final_status || e.status;
        const meta = FINAL[status] || FINAL.failed;
        const Icon = meta.icon;
        return (
          <div key={e.flow_id} data-testid={`execution-${e.flow_id}`}
            className={`rounded-xl border p-4 ${meta.cls}`}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5 min-w-0">
                <Icon className="w-4 h-4 shrink-0" />
                <span className="font-heading text-[14px] font-semibold text-slate-100 truncate">{e.flow_name}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0 font-mono text-[10px]">
                <span className="flex items-center gap-1 text-slate-400"><Cpu className="w-3 h-3" /> w{e.worker}</span>
                <span className="text-slate-400">{e.duration}s</span>
                <span className={`px-2 py-0.5 rounded border font-bold ${meta.cls}`}>{meta.label}</span>
              </div>
            </div>
            {e.error && (
              <pre className="mt-3 p-2.5 rounded-lg bg-[#07090e] border border-slate-800 font-mono text-[11px] text-rose-300 whitespace-pre-wrap overflow-x-auto">{e.error}</pre>
            )}
            {e.artifacts && (
              <div className="mt-3 flex items-center gap-3 font-mono text-[10px] text-slate-500">
                <span className="flex items-center gap-1"><Camera className="w-3 h-3" />{e.artifacts.screenshot}</span>
                <span className="flex items-center gap-1"><FileArchive className="w-3 h-3" />{e.artifacts.trace}</span>
                <span className="flex items-center gap-1"><Film className="w-3 h-3" />{e.artifacts.video}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
