import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;
export const ARTIFACT_BASE = BACKEND_URL;

export const api = axios.create({ baseURL: API });

export const STAGES = ["EXPLORE", "PLAN", "EVALUATE", "GENERATE", "RUN", "HEAL", "REPORT"];

export const STAGE_META = {
  EXPLORE: { text: "text-cyan-400", border: "border-cyan-500/50", bg: "bg-cyan-500/10", dot: "bg-cyan-400", ring: "shadow-[0_0_18px_rgba(6,182,212,0.4)]", agent: "Explorer" },
  PLAN: { text: "text-indigo-400", border: "border-indigo-500/50", bg: "bg-indigo-500/10", dot: "bg-indigo-400", ring: "shadow-[0_0_18px_rgba(99,102,241,0.4)]", agent: "Planner" },
  EVALUATE: { text: "text-violet-400", border: "border-violet-500/50", bg: "bg-violet-500/10", dot: "bg-violet-400", ring: "shadow-[0_0_18px_rgba(139,92,246,0.4)]", agent: "Evaluator" },
  GENERATE: { text: "text-fuchsia-400", border: "border-fuchsia-500/50", bg: "bg-fuchsia-500/10", dot: "bg-fuchsia-400", ring: "shadow-[0_0_18px_rgba(217,70,239,0.4)]", agent: "Generator" },
  RUN: { text: "text-blue-400", border: "border-blue-500/50", bg: "bg-blue-500/10", dot: "bg-blue-400", ring: "shadow-[0_0_18px_rgba(59,130,246,0.4)]", agent: "Runner" },
  HEAL: { text: "text-amber-400", border: "border-amber-500/50", bg: "bg-amber-500/10", dot: "bg-amber-400", ring: "shadow-[0_0_18px_rgba(245,158,11,0.4)]", agent: "Healer" },
  REPORT: { text: "text-emerald-400", border: "border-emerald-500/50", bg: "bg-emerald-500/10", dot: "bg-emerald-400", ring: "shadow-[0_0_18px_rgba(16,185,129,0.4)]", agent: "Reporter" },
};

export const AGENT_DISPLAY = {
  meta: "Meta-agent",
  explorer: "Explorer",
  planner: "Planner",
  evaluator: "Evaluator",
  generator: "Generator",
  runner: "Runner",
  healer: "Healer",
  reporter: "Reporter",
  operator: "Operator",
};

export const HANDOFF_TAB = {
  planner: "plan",
  evaluator: "eval",
  generator: "code",
  runner: "exec",
  healer: "heal",
  reporter: "report",
  operator: "report",
};

export const EDGE_HANDOFF = {
  EXPLORE: { from: "explorer", to: "planner", artifact: "surface" },
  PLAN: { from: "planner", to: "evaluator", artifact: "flows" },
  EVALUATE: { from: "evaluator", to: "generator", artifact: "evaluation" },
  GENERATE: { from: "generator", to: "runner", artifact: "specs" },
  RUN: { from: "runner", to: "healer", artifact: "executions" },
  HEAL: { from: "healer", to: "reporter", artifact: "healer_actions" },
};

export function streamRun(runId, getAfterSeq, onEvent, onEnd) {
  let closed = false;
  let es = null;
  const connect = () => {
    if (closed) return;
    es = new EventSource(`${API}/runs/${runId}/stream?after_seq=${getAfterSeq()}`);
    es.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch (_) {}
    };
    es.addEventListener("end", () => {
      if (es) es.close();
      if (!closed) { closed = true; onEnd && onEnd(); }
    });
    es.onerror = () => {
      if (es) es.close();
      if (closed) return;
      // ingress caps SSE responses (~60s) — reconnect from the last seq we saw
      setTimeout(connect, 1200);
    };
  };
  connect();
  return { close: () => { closed = true; if (es) es.close(); } };
}
