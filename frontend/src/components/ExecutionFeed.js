import { CheckCircle2, XCircle, Wrench, HelpCircle, Cpu, Film, FileArchive, ChevronRight } from "lucide-react";
import { Empty, SectionHeader } from "@/components/TestPlanView";
import { ARTIFACT_BASE } from "@/api";

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
      <SectionHeader title="Live Runner Feed" sub="Real headless Chromium execution — actual steps, screenshots & artifacts" />
      {executions.map((e) => {
        const status = e.final_status || e.status;
        const meta = FINAL[status] || FINAL.failed;
        const Icon = meta.icon;
        const shot = e.artifacts?.screenshot;
        return (
          <div key={e.flow_id} data-testid={`execution-${e.flow_id}`}
            className={`rounded-xl border p-4 ${meta.cls}`}>
            <div className="flex items-start gap-4">
              {shot && (
                <a href={`${ARTIFACT_BASE}${shot}`} target="_blank" rel="noreferrer" className="shrink-0">
                  <img src={`${ARTIFACT_BASE}${shot}`} alt="" loading="lazy"
                    className="w-24 h-16 object-cover object-top rounded-lg border border-slate-700 bg-black hover:border-slate-500 transition-colors" />
                </a>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Icon className="w-4 h-4 shrink-0" />
                    <span className="font-heading text-[14px] font-semibold text-slate-100 truncate">{e.flow_name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0 font-mono text-[10px]">
                    {e.worker && <span className="flex items-center gap-1 text-slate-400"><Cpu className="w-3 h-3" /> w{e.worker}</span>}
                    <span className="text-slate-400">{e.duration}s</span>
                    <span className={`px-2 py-0.5 rounded border font-bold ${meta.cls}`}>{meta.label}</span>
                  </div>
                </div>
                {e.error && (
                  <pre className="mt-3 p-2.5 rounded-lg bg-[#07090e] border border-slate-800 font-mono text-[11px] text-rose-300 whitespace-pre-wrap overflow-x-auto">{e.error}</pre>
                )}
                {e.steps?.length > 0 && (
                  <ol className="mt-3 space-y-1">
                    {e.steps.map((s) => (
                      <li key={s.index} className="flex items-start gap-1.5 text-[11px] text-slate-400">
                        <ChevronRight className={`w-3 h-3 mt-0.5 shrink-0 ${s.ok ? "text-slate-600" : "text-rose-500"}`} />
                        <span className={s.ok ? "" : "text-rose-300"}>{s.description}</span>
                      </li>
                    ))}
                  </ol>
                )}
                {e.artifacts && (
                  <div className="mt-3 flex items-center gap-4 font-mono text-[10px] text-slate-500">
                    {e.artifacts.trace && (
                      <a href={`${ARTIFACT_BASE}${e.artifacts.trace}`} className="flex items-center gap-1 hover:text-slate-300">
                        <FileArchive className="w-3 h-3" /> trace
                      </a>
                    )}
                    {e.artifacts.video && (
                      <a href={`${ARTIFACT_BASE}${e.artifacts.video}`} className="flex items-center gap-1 hover:text-slate-300">
                        <Film className="w-3 h-3" /> video
                      </a>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
