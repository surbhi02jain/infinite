import { useRef, useEffect, useState } from "react";
import { Terminal, Trash2, ArrowDownToLine } from "lucide-react";
import { STAGE_META } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const LEVEL_COLOR = {
  info: "text-slate-300", success: "text-emerald-400", warn: "text-amber-400",
  error: "text-rose-400",
};

export default function EventConsole({ events, live }) {
  const [filter, setFilter] = useState("all");
  const [autoScroll, setAutoScroll] = useState(true);
  const [cleared, setCleared] = useState(0);
  const endRef = useRef(null);

  const visible = events.filter((e, i) => i >= cleared && (filter === "all" || e.stage === filter || e.level === filter));

  useEffect(() => {
    if (autoScroll && endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" });
  }, [visible.length, autoScroll]);

  return (
    <div data-testid="live-event-stream-container" className="w-[360px] xl:w-[440px] shrink-0 flex flex-col bg-[#070b12] border-l border-slate-800/80">
      <div className="h-11 px-3 flex items-center justify-between border-b border-slate-800/80 shrink-0">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <span className="font-heading text-sm font-semibold">Decision Stream</span>
          {live && <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />}
        </div>
        <div className="flex items-center gap-1.5">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger data-testid="event-stream-filter-select" className="h-7 w-[104px] bg-[#0d131f] border-slate-700 text-[11px] font-mono"><SelectValue /></SelectTrigger>
            <SelectContent className="bg-[#0d131f] border-slate-700 text-slate-200">
              <SelectItem value="all">All</SelectItem>
              {Object.keys(STAGE_META).map((s) => <SelectItem key={s} value={s} className="font-mono text-xs">{s}</SelectItem>)}
              <SelectItem value="error">Errors</SelectItem>
              <SelectItem value="warn">Warnings</SelectItem>
            </SelectContent>
          </Select>
          <Button data-testid="event-stream-autoscroll-button" size="icon" variant="ghost"
            onClick={() => setAutoScroll(!autoScroll)}
            className={`h-7 w-7 ${autoScroll ? "text-emerald-400" : "text-slate-500"}`}>
            <ArrowDownToLine className="w-3.5 h-3.5" />
          </Button>
          <Button data-testid="event-stream-clear-button" size="icon" variant="ghost"
            onClick={() => setCleared(events.length)} className="h-7 w-7 text-slate-500 hover:text-rose-400">
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed space-y-1.5">
        {visible.length === 0 && <div className="text-slate-600 text-xs">Awaiting agent decisions…</div>}
        {visible.map((e) => {
          const meta = STAGE_META[e.stage] || {};
          return (
            <div key={e.id} className="animate-fadein flex gap-2 items-start">
              <span className="text-slate-600 shrink-0">{new Date(e.ts).toLocaleTimeString("en-US", { hour12: false })}</span>
              <span className={`shrink-0 px-1 rounded ${meta.bg || "bg-slate-800"} ${meta.text || "text-slate-400"} text-[9px] font-bold uppercase w-[62px] text-center`}>
                {meta.agent || e.agent}
              </span>
              <span className={`${LEVEL_COLOR[e.level] || "text-slate-300"} break-words`}>{e.message}</span>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
