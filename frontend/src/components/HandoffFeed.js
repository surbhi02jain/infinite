import { AGENT_DISPLAY, HANDOFF_TAB, STAGE_META } from "@/api";

export default function HandoffFeed({ handoffs = [], live, onSelect }) {
  return (
    <div data-testid="handoff-feed" className="px-4 py-2.5 border-b border-slate-800/80 bg-[#0b101c] shrink-0">
      <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-1.5">Agent Handoffs</div>
      {handoffs.length === 0 ? (
        <div className="text-slate-600 text-xs">Awaiting first handoff…</div>
      ) : (
        <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
          {handoffs.map((h, i) => {
            const last = i === handoffs.length - 1;
            const meta = STAGE_META[h.stage] || {};
            return (
              <button
                key={h.id || h.seq}
                data-testid={`handoff-chip-${h.seq}`}
                type="button"
                onClick={() => onSelect?.(HANDOFF_TAB[h.to] || "plan")}
                className={`shrink-0 rounded-lg border px-2 py-1 text-left transition-colors hover:border-slate-500 ${
                  meta.border || "border-slate-700"} ${meta.bg || "bg-slate-800/40"} ${
                  live && last ? "animate-pulse" : ""}`}
              >
                <div className={`text-[10px] font-semibold leading-tight ${meta.text || "text-slate-300"}`}>
                  {AGENT_DISPLAY[h.from] || h.from} → {AGENT_DISPLAY[h.to] || h.to}
                </div>
                <div className="font-mono text-[9px] text-slate-400 mt-0.5 whitespace-nowrap">
                  {h.artifact} · {h.summary}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
