import { formatDistanceToNow } from "date-fns";
import { Plus, CircleDot } from "lucide-react";

const STATUS_STYLE = {
  completed: "text-emerald-400 bg-emerald-950/60 border-emerald-500/30",
  running: "text-cyan-400 bg-cyan-950/60 border-cyan-500/30 animate-pulse",
  queued: "text-slate-400 bg-slate-900 border-slate-800",
  paused: "text-amber-300 bg-amber-950/60 border-amber-500/30",
  failed: "text-rose-400 bg-rose-950/60 border-rose-500/30",
};

export default function RunsSidebar({ runs, activeRunId, onSelect, onNew }) {
  return (
    <aside className="w-72 lg:w-80 border-r border-slate-800/80 bg-[#080d16] flex flex-col shrink-0">
      <div className="p-4 border-b border-slate-800/80">
        <button data-testid="sidebar-new-run-button" onClick={onNew}
          className="w-full flex items-center justify-center gap-2 h-10 rounded-lg border border-dashed border-slate-700 text-slate-300 hover:border-emerald-500/60 hover:text-emerald-400 transition-colors text-sm font-medium">
          <Plus className="w-4 h-4" /> New Run
        </button>
      </div>
      <div className="px-4 pt-4 pb-2 font-mono text-[10px] uppercase tracking-widest text-slate-500">Run History</div>
      <div data-testid="runs-history-list" className="flex-1 overflow-y-auto px-3 pb-4 space-y-2">
        {runs.length === 0 && <div className="text-slate-600 text-xs px-2 py-4">No runs yet. Launch one to begin.</div>}
        {runs.map((r) => {
          const s = r.report_summary;
          const active = r.id === activeRunId;
          return (
            <button key={r.id} data-testid="run-history-item" onClick={() => onSelect(r.id)}
              className={`w-full text-left p-3 rounded-xl border transition-colors duration-200 ${
                active ? "border-emerald-500/50 bg-[#0f1a26]" : "border-slate-800 bg-[#0b111c] hover:border-slate-700"}`}>
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
          );
        })}
      </div>
    </aside>
  );
}

function prettyUrl(u) {
  try { return new URL(u).hostname + new URL(u).pathname.replace(/\/$/, ""); } catch { return u; }
}
