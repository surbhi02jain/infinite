import { useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { PanelLeftClose, PanelLeftOpen, RotateCcw } from "lucide-react";
import TipIconButton from "@/components/TipIconButton";

const STATUS_STYLE = {
  completed: "text-emerald-400 bg-emerald-950/60 border-emerald-500/30",
  running: "text-cyan-400 bg-cyan-950/60 border-cyan-500/30 animate-pulse",
  queued: "text-slate-400 bg-slate-900 border-slate-800",
  paused: "text-amber-300 bg-amber-950/60 border-amber-500/30",
  failed: "text-rose-400 bg-rose-950/60 border-rose-500/30",
  aborted: "text-amber-300 bg-amber-950/60 border-amber-500/30",
};

const TERMINAL = ["completed", "failed", "aborted"];

export default function RunsSidebar({ runs, activeRunId, onSelect, onRerun }) {
  // narrow/tablet screens can't fit both side panels + the workspace at once — start collapsed
  // there so the main content is usable; desktop keeps its normal default (open).
  const [collapsed, setCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth < 1024);

  if (collapsed) {
    return (
      <aside data-testid="runs-sidebar-collapsed"
        className="w-10 shrink-0 flex flex-col items-center gap-3 pt-3 bg-[#080d16] border-r border-slate-800/80">
        <TipIconButton data-testid="runs-sidebar-expand-button" label="Expand Run History" side="right"
          onClick={() => setCollapsed(false)} className="text-slate-400 hover:text-emerald-400">
          <PanelLeftOpen className="w-4 h-4" />
        </TipIconButton>
        {runs.length > 0 && (
          <span className="font-mono text-[10px] text-slate-500 tracking-widest">{runs.length}</span>
        )}
        <span className="font-mono text-[10px] text-slate-500 tracking-widest uppercase [writing-mode:vertical-rl]">
          Run History
        </span>
      </aside>
    );
  }

  return (
    <aside className="w-72 lg:w-80 border-r border-slate-800/80 bg-[#080d16] flex flex-col shrink-0">
      <div className="px-4 pt-4 pb-2 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">Run History</span>
        <TipIconButton data-testid="runs-sidebar-collapse-button" label="Collapse Run History"
          onClick={() => setCollapsed(true)} className="text-slate-500 hover:text-emerald-400">
          <PanelLeftClose className="w-3.5 h-3.5" />
        </TipIconButton>
      </div>
      <div data-testid="runs-history-list" className="flex-1 overflow-y-auto px-3 pb-4 space-y-2">
        {runs.length === 0 && <div className="text-slate-600 text-xs px-2 py-4">No runs yet. Launch one to begin.</div>}
        {runs.map((r) => {
          const s = r.report_summary;
          const active = r.id === activeRunId;
          return (
            <div key={r.id}
              className={`rounded-xl border transition-colors duration-200 ${
                active ? "border-emerald-500/50 bg-[#0f1a26]" : "border-slate-800 bg-[#0b111c] hover:border-slate-700"}`}>
              <button data-testid="run-history-item" type="button" onClick={() => onSelect(r.id)}
                className="w-full text-left p-3">
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <div className="font-mono text-[11px] text-slate-300 truncate flex-1">{prettyUrl(r.url)}</div>
                  <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-mono uppercase ${STATUS_STYLE[r.status] || STATUS_STYLE.queued}`}>{r.status}</span>
                </div>
                <div className="flex items-center gap-3 text-[10px] text-slate-500 font-mono">
                  <span>{r.created_at ? formatDistanceToNow(new Date(r.created_at), { addSuffix: true }) : ""}</span>
                  {r.auth_mode === "authenticated" && <span className="text-amber-400">auth</span>}
                </div>
                {s && (
                  <div className="flex items-center gap-2 mt-2 text-[10px] font-mono">
                    <span className="text-emerald-400">{s.pass_rate}%</span>
                    <span className="text-slate-600">·</span>
                    <span className="text-slate-400">{s.passed}✓</span>
                    {s.healed > 0 && <span className="text-amber-400">{s.healed}⟳</span>}
                    {s.defects > 0 && <span className="text-rose-400">{s.defects}⚠</span>}
                  </div>
                )}
              </button>
              {TERMINAL.includes(r.status) && (
                <div className="px-3 pb-2 flex justify-end -mt-1">
                  <TipIconButton data-testid={`rerun-history-${r.id}`} label="Rerun with the same configuration"
                    onClick={() => onRerun?.(r.id)} className="text-slate-500 hover:text-emerald-400 h-6 w-6">
                    <RotateCcw className="w-3 h-3" />
                  </TipIconButton>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function prettyUrl(u) {
  try { return new URL(u).hostname + new URL(u).pathname.replace(/\/$/, ""); } catch { return u; }
}
