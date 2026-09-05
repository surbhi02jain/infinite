import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2, Search, ListChecks, ShieldAlert, Code2, PlayCircle, Wrench, FileBarChart, PlayCircle as Play, Square, RotateCcw } from "lucide-react";
import { STAGES, STAGE_META, EDGE_HANDOFF } from "@/api";
import { Button } from "@/components/ui/button";

const ICONS = {
  EXPLORE: Search, PLAN: ListChecks, EVALUATE: ShieldAlert, GENERATE: Code2,
  RUN: PlayCircle, HEAL: Wrench, REPORT: FileBarChart,
};

// stages without their own workspace tab jump to the closest relevant one
const STAGE_TAB = {
  EXPLORE: "plan", PLAN: "plan", EVALUATE: "eval", GENERATE: "code",
  RUN: "exec", HEAL: "heal", REPORT: "report",
};

// reverse map: which single stage "owns" the active-node highlight for a given tab — EXPLORE and
// PLAN both navigate to the "plan" tab, but only one node should ever show as active at a time
const TAB_OWNER_STAGE = {
  plan: "PLAN", eval: "EVALUATE", code: "GENERATE", exec: "RUN", heal: "HEAL", report: "REPORT",
};

function fmtDur(ms) {
  if (ms == null) return "done";
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function edgeFired(handoffs, edge) {
  if (!edge) return false;
  return (handoffs || []).some((h) => h.from === edge.from && h.to === edge.to && h.artifact === edge.artifact);
}

const ACTIVE = ["queued", "running", "paused"];
const TERMINAL = ["completed", "failed", "aborted"];

export default function PipelineDAG({ stageStatus, stageDuration, run, awaiting, onResume, onAbort, onRerun, activeTab, onStageClick, handoffs, replan }) {
  return (
    <div data-testid="pipeline-dag-container" className="p-4 border-b border-slate-800/80 bg-[#0b101c] shrink-0">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">State Machine</span>
          {run && <span className="font-mono text-[11px] text-slate-400 truncate">· {run.url}</span>}
          {run?.auth_mode === "authenticated" && (
            <span className="px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-950/60 text-amber-300 text-[9px] font-mono uppercase">authenticated</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {awaiting && (
            <Button data-testid="resume-run-button" onClick={onResume} size="sm"
              className="h-7 bg-amber-500 hover:bg-amber-400 text-[#07090e] font-semibold text-xs animate-pulse">
              <Play className="w-3 h-3 mr-1" /> Approve Plan & Resume
            </Button>
          )}
          {ACTIVE.includes(run?.status) && (
            <Button data-testid="abort-run-button" onClick={onAbort} size="sm" variant="outline"
              className="h-7 border-rose-500/40 bg-rose-950/40 text-rose-300 hover:bg-rose-900/50 hover:text-rose-200 text-xs">
              <Square className="w-3 h-3 mr-1 fill-current" /> Abort
            </Button>
          )}
          {TERMINAL.includes(run?.status) && (
            <Button data-testid="rerun-run-button" onClick={onRerun} size="sm" variant="outline"
              className="h-7 border-emerald-500/40 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/50 hover:text-emerald-200 text-xs">
              <RotateCcw className="w-3 h-3 mr-1" /> Rerun
            </Button>
          )}
        </div>
      </div>

      {/* px/py give the active-node ring (ring-offset) and hover lift room so they aren't
          clipped by this row's own overflow-x-auto — per spec, overflow-x:auto forces
          overflow-y to auto too, so vertical space here must come from padding, not overflow-y:visible */}
      <div className={`flex items-stretch gap-1 overflow-x-auto px-2 ${replan ? "pt-6 pb-4" : "py-1 pb-4"}`}>
        {STAGES.map((stage, i) => {
          const status = stageStatus[stage] || "pending";
          const meta = STAGE_META[stage];
          const Icon = ICONS[stage];
          const done = status === "done";
          const running = status === "running";
          const tab = STAGE_TAB[stage];
          const isActiveTab = TAB_OWNER_STAGE[activeTab] === stage;
          const edge = EDGE_HANDOFF[stage];
          const labeled = edgeFired(handoffs, edge);
          return (
            <div key={stage} className="flex items-center flex-1 min-w-[120px]">
              <button data-testid={`dag-node-${stage.toLowerCase()}`} type="button"
                onClick={() => onStageClick?.(tab)}
                className={`relative flex-1 text-left rounded-xl border px-3 py-2.5 transition-all duration-300 cursor-pointer hover:-translate-y-0.5 hover:shadow-lg ${
                  running ? `${meta.border} ${meta.bg} ${meta.ring}` :
                  done ? `border-slate-700 bg-[#0f1725] hover:border-slate-600` : "border-slate-800 bg-[#0a0f18] hover:border-slate-700"} ${
                  isActiveTab ? "ring-2 ring-inset ring-cyan-400/70" : ""}`}>
                <div className="flex items-center gap-2">
                  <div className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${
                    running ? meta.bg : done ? "bg-emerald-500/15" : "bg-slate-800/60"}`}>
                    <AnimatePresence mode="wait" initial={false}>
                      {done ? (
                        <motion.span key="done" initial={{ scale: 0.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                          transition={{ type: "spring", stiffness: 500, damping: 20 }}>
                          <Check className="w-3.5 h-3.5 text-emerald-400" strokeWidth={3} />
                        </motion.span>
                      ) : running ? (
                        <motion.span key="running" initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
                          <Loader2 className={`w-3.5 h-3.5 ${meta.text} animate-spin`} />
                        </motion.span>
                      ) : (
                        <motion.span key="pending" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                          <Icon className="w-3.5 h-3.5 text-slate-600" />
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </div>
                  <div className="min-w-0">
                    <div className={`font-heading text-[13px] font-semibold leading-none ${
                      running ? meta.text : done ? "text-slate-200" : "text-slate-600"}`}>{stage}</div>
                    <div className={`font-mono text-[9px] mt-0.5 ${running ? meta.text : done ? "text-slate-400" : "text-slate-600"}`}>
                      {meta.agent}
                    </div>
                    <div className="font-mono text-[9px] text-slate-500">
                      {running ? "running…" : done ? fmtDur(stageDuration[stage]) : "pending"}
                    </div>
                  </div>
                </div>
                {running && (
                  <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden rounded-b-xl">
                    <div className={`h-full w-1/3 ${meta.dot} animate-beam`} />
                  </div>
                )}
              </button>
              {i < STAGES.length - 1 && (
                <div className="relative w-12 flex items-center justify-center shrink-0 self-stretch">
                  <div className={`h-0.5 w-full transition-colors duration-500 ${labeled ? "bg-emerald-500/40" : "bg-slate-800"}`} />
                  {labeled && (
                    <span className="absolute top-[calc(50%+4px)] font-mono text-[8px] text-slate-500 whitespace-nowrap">
                      {edge.artifact}
                    </span>
                  )}
                  {stage === "PLAN" && replan && (
                    <svg className="absolute left-0 right-0 -top-5 h-5 w-full overflow-visible pointer-events-none"
                      viewBox="0 0 48 20" preserveAspectRatio="none" aria-hidden>
                      <path d="M2 18 C 10 2, 38 2, 46 18" fill="none" stroke="rgb(245,158,11)" strokeOpacity="0.75" strokeWidth="1.4" />
                      <text x="24" y="8" textAnchor="middle" fill="rgb(251,191,36)" fontSize="7" fontFamily="ui-monospace, monospace">re-plan</text>
                    </svg>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
