import { useState } from "react";
import { AlertTriangle, ChevronDown, ArrowRight } from "lucide-react";

export default function ReviewCallout({ items, onJump }) {
  const [expanded, setExpanded] = useState(false);
  if (!items?.length) return null;

  return (
    <div data-testid="needs-review-callout"
      className="border-b border-amber-500/20 bg-amber-950/20 shrink-0">
      <button type="button" data-testid="needs-review-toggle" onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-amber-950/30 transition-colors">
        <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
        <span className="text-[13px] text-amber-200 font-medium">
          {items.length} flow{items.length > 1 ? "s" : ""} need{items.length > 1 ? "" : "s"} human review
        </span>
        <span className="text-[12px] text-amber-400/70 font-mono hidden sm:inline">
          — an automated fix couldn't be verified, so the healer escalated instead of guessing
        </span>
        <ChevronDown className={`w-3.5 h-3.5 text-amber-400 shrink-0 ml-auto transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>

      {expanded && (
        <div className="px-4 pb-3 space-y-1.5">
          {items.map((a) => (
            <div key={a.id} data-testid={`needs-review-item-${a.flow_id}`}
              className="flex items-start justify-between gap-3 rounded-lg bg-[#07090e]/60 border border-amber-500/10 px-3 py-2">
              <div className="min-w-0">
                <div className="text-[13px] text-slate-200 font-medium truncate">{a.flow_name}</div>
                <div className="text-[12px] text-slate-400 mt-0.5 leading-snug">{a.result || a.rationale}</div>
              </div>
              <button type="button" data-testid={`needs-review-jump-${a.flow_id}`} onClick={onJump}
                className="shrink-0 flex items-center gap-1 text-[11px] font-mono text-amber-300 hover:text-amber-200 mt-0.5">
                Review <ArrowRight className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
