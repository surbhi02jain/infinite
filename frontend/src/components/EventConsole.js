import { useRef, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Terminal, Trash2, ListEnd, Download, PanelRightClose, PanelRightOpen } from "lucide-react";
import { toast } from "sonner";
import { STAGE_META } from "@/api";
import TipIconButton from "@/components/TipIconButton";
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
  // narrow/tablet screens can't fit both side panels + the workspace at once — start collapsed
  // there so the main content is usable; desktop keeps its normal default (open).
  const [collapsed, setCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth < 1024);
  const endRef = useRef(null);

  const visible = events.filter((e, i) => i >= cleared && (filter === "all" || e.stage === filter || e.level === filter));

  useEffect(() => {
    if (autoScroll && endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" });
  }, [visible.length, autoScroll]);

  const exportEvents = () => {
    try {
      const blob = new Blob([JSON.stringify(visible, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `qalchemist-events-${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Exported event log");
    } catch (_) {
      toast.error("Export failed");
    }
  };

  if (collapsed) {
    return (
      <div data-testid="live-event-stream-container-collapsed"
        className="w-10 shrink-0 flex flex-col items-center gap-3 pt-3 bg-[#070b12] border-l border-slate-800/80">
        <TipIconButton data-testid="event-stream-expand-button" label="Expand Decision Stream" side="left"
          onClick={() => setCollapsed(false)} className="text-slate-400 hover:text-emerald-400">
          <PanelRightOpen className="w-4 h-4" />
        </TipIconButton>
        {live && <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse shrink-0" />}
        <span className="font-mono text-[10px] text-slate-500 tracking-widest uppercase [writing-mode:vertical-rl]">
          Decision Stream
        </span>
      </div>
    );
  }

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
          <TipIconButton data-testid="event-stream-autoscroll-button"
            label={autoScroll ? "Auto-scroll: on (following latest)" : "Auto-scroll: off (click to follow latest)"}
            onClick={() => setAutoScroll(!autoScroll)}
            className={autoScroll ? "text-emerald-400" : "text-slate-500"}>
            <ListEnd className="w-3.5 h-3.5" />
          </TipIconButton>
          <TipIconButton data-testid="event-stream-download-button" label="Export visible events as JSON"
            onClick={exportEvents} disabled={visible.length === 0}
            className="text-slate-500 hover:text-emerald-400 disabled:opacity-40 disabled:pointer-events-none">
            <Download className="w-3.5 h-3.5" />
          </TipIconButton>
          <TipIconButton data-testid="event-stream-clear-button" label="Clear from view"
            onClick={() => setCleared(events.length)} className="text-slate-500 hover:text-rose-400">
            <Trash2 className="w-3.5 h-3.5" />
          </TipIconButton>
          <div className="w-px h-4 bg-slate-800 mx-0.5" />
          <TipIconButton data-testid="event-stream-collapse-button" label="Collapse Decision Stream"
            onClick={() => setCollapsed(true)} className="text-slate-500 hover:text-emerald-400">
            <PanelRightClose className="w-3.5 h-3.5" />
          </TipIconButton>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed space-y-1.5">
        {visible.length === 0 && <div className="text-slate-600 text-xs">Awaiting agent decisions…</div>}
        {visible.map((e) => {
          const meta = STAGE_META[e.stage] || {};
          return (
            <motion.div key={e.id} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.25, ease: "easeOut" }} className="flex gap-2 items-start">
              <span className="text-slate-600 shrink-0">{new Date(e.ts).toLocaleTimeString("en-US", { hour12: false })}</span>
              <span className={`shrink-0 px-1 rounded ${meta.bg || "bg-slate-800"} ${meta.text || "text-slate-400"} text-[9px] font-bold uppercase w-[62px] text-center`}>
                {meta.agent || e.agent}
              </span>
              <span className={`${LEVEL_COLOR[e.level] || "text-slate-300"} break-words`}>{e.message}</span>
            </motion.div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
