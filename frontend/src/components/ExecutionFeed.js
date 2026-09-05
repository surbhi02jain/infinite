import { useEffect, useRef, useState } from "react";
import { CheckCircle2, XCircle, Wrench, HelpCircle, CircleSlash, Cpu, Film, FileArchive, ChevronRight, ExternalLink, ArrowRight, Terminal, WifiOff, ChevronDown } from "lucide-react";
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

// Playwright's own hosted trace viewer can load a trace.zip straight from a URL — turns a "download
// a zip nobody opens" link into a one-click, fully interactive replay of the exact failing run.
function traceViewerUrl(traceUrl) {
  return `https://trace.playwright.dev/?trace=${encodeURIComponent(traceUrl)}`;
}

function ConsoleNetworkEvidence({ execution: e }) {
  const [open, setOpen] = useState(false);
  const consoleErrors = e.console_errors || [];
  const networkErrors = e.network || [];
  if (!consoleErrors.length && !networkErrors.length) return null;
  return (
    <div className="mt-3">
      <button type="button" onClick={() => setOpen((o) => !o)}
        data-testid={`execution-evidence-toggle-${e.flow_id}`}
        className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-slate-500 hover:text-slate-300">
        <ChevronDown className={`w-3 h-3 transition-transform ${open ? "" : "-rotate-90"}`} />
        Console & Network Evidence ({consoleErrors.length + networkErrors.length})
      </button>
      {open && (
        <div className="mt-2 space-y-1.5">
          {consoleErrors.map((c, i) => (
            <div key={`c${i}`} className="flex items-start gap-1.5 font-mono text-[11px] text-rose-300 bg-[#07090e] border border-slate-800 rounded-lg p-2">
              <Terminal className="w-3 h-3 mt-0.5 shrink-0 text-rose-500" /> <span className="break-all">{c}</span>
            </div>
          ))}
          {networkErrors.map((n, i) => (
            <div key={`n${i}`} className="flex items-start gap-1.5 font-mono text-[11px] text-amber-300 bg-[#07090e] border border-slate-800 rounded-lg p-2">
              <WifiOff className="w-3 h-3 mt-0.5 shrink-0 text-amber-500" />
              <span className="break-all">{n.status ? `${n.status} — ` : ""}{n.url || JSON.stringify(n)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ExecutionFeed({ executions, highlightFlowId }) {
  const [lightbox, setLightbox] = useState(null);
  const cardRefs = useRef({});

  useEffect(() => {
    if (highlightFlowId && cardRefs.current[highlightFlowId]) {
      cardRefs.current[highlightFlowId].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlightFlowId]);

  if (!executions.length) return <Empty text="Live pass/fail feed streams here as workers run…" />;
  return (
    <div className="space-y-3 max-w-4xl">
      <SectionHeader title="Live Runner Feed" sub="Real headless Chromium execution — actual steps, screenshots & artifacts" />
      {executions.map((e) => {
        const status = e.final_status || e.status;
        const meta = FINAL[status] || FINAL.failed;
        const Icon = meta.icon;
        const shot = e.artifacts?.screenshot;
        const beforeShot = status === "healed" ? e.original_artifacts?.screenshot : null;
        const isHighlighted = highlightFlowId === e.flow_id;
        return (
          <div key={e.flow_id} ref={(el) => (cardRefs.current[e.flow_id] = el)} data-testid={`execution-${e.flow_id}`}
            className={`rounded-xl border p-4 transition-all duration-500 ${meta.cls} ${
              isHighlighted ? "ring-2 ring-cyan-400/70 ring-offset-2 ring-offset-[#07090e]" : ""}`}>
            <div className="flex items-start gap-4">
              {beforeShot ? (
                <div className="shrink-0 flex items-center gap-1.5" data-testid={`execution-before-after-${e.flow_id}`}>
                  <div className="flex flex-col items-center gap-0.5">
                    <button type="button" onClick={() => setLightbox({ kind: "image", url: `${ARTIFACT_BASE}${beforeShot}`, name: `${e.flow_name} — before (failed)` })}>
                      <img src={`${ARTIFACT_BASE}${beforeShot}`} alt="" loading="lazy"
                        className="w-20 h-14 object-cover object-top rounded-lg border border-rose-500/40 bg-black hover:border-rose-400 transition-colors cursor-zoom-in" />
                    </button>
                    <span className="text-[8px] font-mono uppercase text-rose-400">before</span>
                  </div>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                  <div className="flex flex-col items-center gap-0.5">
                    <button type="button" data-testid={`execution-screenshot-${e.flow_id}`}
                      onClick={() => setLightbox({ kind: "image", url: `${ARTIFACT_BASE}${shot}`, name: `${e.flow_name} — after (healed)` })}>
                      <img src={`${ARTIFACT_BASE}${shot}`} alt="" loading="lazy"
                        className="w-20 h-14 object-cover object-top rounded-lg border border-emerald-500/40 bg-black hover:border-emerald-400 transition-colors cursor-zoom-in" />
                    </button>
                    <span className="text-[8px] font-mono uppercase text-emerald-400">after</span>
                  </div>
                </div>
              ) : shot && (
                <button type="button" data-testid={`execution-screenshot-${e.flow_id}`}
                  onClick={() => setLightbox({ kind: "image", url: `${ARTIFACT_BASE}${shot}`, name: e.flow_name })}
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
                <ConsoleNetworkEvidence execution={e} />
                {e.artifacts && (
                  <div className="mt-3 flex items-center gap-4 font-mono text-[10px] text-slate-500">
                    {e.artifacts.trace && (
                      <a href={traceViewerUrl(`${ARTIFACT_BASE}${e.artifacts.trace}`)} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1 hover:text-slate-300" title="Open in Playwright's trace viewer">
                        <FileArchive className="w-3 h-3" /> trace
                      </a>
                    )}
                    {e.artifacts.video && (
                      <button type="button" onClick={() => setLightbox({ kind: "video", url: `${ARTIFACT_BASE}${e.artifacts.video}`, name: e.flow_name })}
                        className="flex items-center gap-1 hover:text-slate-300">
                        <Film className="w-3 h-3" /> video
                      </button>
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
          {lightbox && (lightbox.kind === "video" ? (
            <video src={lightbox.url} controls autoPlay className="w-full rounded-lg border border-slate-800 bg-black" />
          ) : (
            <img src={lightbox.url} alt={lightbox.name}
              className="w-full rounded-lg border border-slate-800 bg-black" />
          ))}
        </DialogContent>
      </Dialog>
    </div>
  );
}
