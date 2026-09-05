import { useState } from "react";
import { CheckCircle2, XCircle, Wrench, HelpCircle, CircleSlash, Cpu, Film, FileArchive, ChevronRight, ExternalLink } from "lucide-react";
import { Empty, SectionHeader } from "@/components/TestPlanView";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { ARTIFACT_BASE } from "@/api";

const FINAL = {
  passed: { icon: CheckCircle2, cls: "text-emerald-400 border-emerald-500/30 bg-emerald-950/30", label: "PASSED" },
  healed: { icon: Wrench, cls: "text-amber-300 border-amber-500/30 bg-amber-950/30", label: "HEALED" },
  defect: { icon: XCircle, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "DEFECT" },
  review: { icon: HelpCircle, cls: "text-slate-300 border-slate-600 bg-slate-800/30", label: "REVIEW" },
  resolved: { icon: CircleSlash, cls: "text-slate-500 border-slate-700 bg-slate-900/40", label: "DISMISSED" },
  failed: { icon: XCircle, cls: "text-rose-400 border-rose-500/30 bg-rose-950/30", label: "FAILED" },
};

export default function ExecutionFeed({ executions }) {
  const [lightbox, setLightbox] = useState(null);
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
                <button type="button" data-testid={`execution-screenshot-${e.flow_id}`}
                  onClick={() => setLightbox({ url: `${ARTIFACT_BASE}${shot}`, name: e.flow_name })}
                  className="shrink-0">
                  <img src={`${ARTIFACT_BASE}${shot}`} alt="" loading="lazy"
                    className="w-24 h-16 object-cover object-top rounded-lg border border-slate-700 bg-black hover:border-slate-500 transition-colors cursor-zoom-in" />
                </button>
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
                        <div className="min-w-0">
                          <span className={s.ok ? "" : "text-rose-300"}>{s.description}</span>
                          {s.locator && (
                            <span className="ml-2 font-mono text-[10px] text-cyan-400/80" title="Resolved locator actually used for this step">
                              → {s.locator}
                            </span>
                          )}
                          {s.note && <div className="text-[10px] text-slate-500 mt-0.5">{s.note}</div>}
                        </div>
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

      <Dialog open={Boolean(lightbox)} onOpenChange={(open) => !open && setLightbox(null)}>
        <DialogContent className="max-w-3xl bg-[#0b111c] border-slate-800 text-slate-100 p-4">
          <DialogTitle className="text-sm font-heading flex items-center justify-between gap-3 pr-6">
            <span className="truncate">{lightbox?.name}</span>
            {lightbox && (
              <a href={lightbox.url} target="_blank" rel="noreferrer"
                className="shrink-0 flex items-center gap-1 text-[11px] font-mono font-normal text-slate-400 hover:text-emerald-400">
                <ExternalLink className="w-3 h-3" /> open original
              </a>
            )}
          </DialogTitle>
          {lightbox && (
            <img src={lightbox.url} alt={lightbox.name}
              className="w-full rounded-lg border border-slate-800 bg-black" />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
