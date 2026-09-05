import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "sonner";
import { Activity, Radio, Cpu, Zap } from "lucide-react";
import { api, streamRun } from "@/api";
import { deriveState } from "@/lib/derive";
import RunForm from "@/components/RunForm";
import RunsSidebar from "@/components/RunsSidebar";
import PipelineDAG from "@/components/PipelineDAG";
import HandoffFeed from "@/components/HandoffFeed";
import EventConsole from "@/components/EventConsole";
import WorkspaceTabs from "@/components/WorkspaceTabs";
import ReviewCallout from "@/components/ReviewCallout";
import { Button } from "@/components/ui/button";

export default function Dashboard() {
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [run, setRun] = useState(null);
  const [events, setEvents] = useState([]);
  const [showForm, setShowForm] = useState(true);
  const [live, setLive] = useState(false);
  const [activeTab, setActiveTab] = useState("plan");
  const [highlightFlowId, setHighlightFlowId] = useState(null);
  const connRef = useRef(null);
  const pollRef = useRef(null);
  const seqRef = useRef(0);
  const activeRef = useRef(null);

  const derived = deriveState(events);

  const loadRuns = useCallback(async () => {
    try {
      const { data } = await api.get("/runs");
      setRuns(data);
    } catch (_) {}
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const cleanup = useCallback(() => {
    if (connRef.current) { connRef.current.close(); connRef.current = null; }
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const pushEvent = useCallback((ev) => {
    if (ev.seq > seqRef.current) seqRef.current = ev.seq;
    setEvents((prev) => (prev.some((p) => p.id === ev.id) ? prev : [...prev, ev]));
  }, []);

  const finishLive = useCallback(async (runId) => {
    cleanup();
    setLive(false);
    try {
      const { data } = await api.get(`/runs/${runId}`);
      if (activeRef.current === runId) { setRun(data.run); setEvents(data.events || []); }
    } catch (_) {}
    loadRuns();
  }, [cleanup, loadRuns]);

  const startLive = useCallback((runId) => {
    setLive(true);
    connRef.current = streamRun(runId, () => seqRef.current, pushEvent, () => finishLive(runId));
    // polling backstop — guarantees completion even if SSE fully drops
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/runs/${runId}/events?after_seq=${seqRef.current}`);
        (data.events || []).forEach(pushEvent);
        if (["completed", "failed", "aborted"].includes(data.status)) finishLive(runId);
      } catch (_) {}
    }, 5000);
  }, [pushEvent, finishLive]);

  const openRun = useCallback(async (runId) => {
    cleanup();
    activeRef.current = runId;
    setActiveRunId(runId);
    setShowForm(false);
    setActiveTab("plan");
    setEvents([]);
    seqRef.current = 0;
    const { data } = await api.get(`/runs/${runId}`);
    setRun(data.run);
    setEvents(data.events || []);
    seqRef.current = (data.events || []).reduce((m, e) => Math.max(m, e.seq), 0);
    if (["queued", "running", "paused"].includes(data.run.status)) {
      startLive(runId);
    } else {
      setLive(false);
    }
  }, [cleanup, startLive]);

  const startRun = async (config) => {
    try {
      const { data } = await api.post("/runs", config);
      toast.success("Run launched", { description: `Meta-agent orchestrating ${data.url}` });
      await loadRuns();
      openRun(data.id);
    } catch (e) {
      toast.error("Failed to start run", { description: e.response?.data?.detail || e.message });
    }
  };

  const resume = async () => {
    try {
      await api.post(`/runs/${activeRunId}/resume`);
      toast.success("Plan approved — resuming pipeline");
    } catch (e) {
      toast.error("Could not resume");
    }
  };

  const abort = async () => {
    try {
      await api.post(`/runs/${activeRunId}/abort`);
      toast.success("Abort requested", { description: "The pipeline will stop after the current step unwinds." });
    } catch (e) {
      toast.error("Could not abort", { description: e.response?.data?.detail || e.message });
    }
  };

  const rerun = async (runId = activeRunId) => {
    try {
      const { data } = await api.post(`/runs/${runId}/rerun`);
      toast.success("Rerun launched", { description: `Same config as before · ${data.url}` });
      await loadRuns();
      openRun(data.id);
    } catch (e) {
      toast.error("Could not rerun", { description: e.response?.data?.detail || e.message });
    }
  };

  useEffect(() => () => cleanup(), [cleanup]);

  const newRun = () => { cleanup(); setShowForm(true); setActiveRunId(null); activeRef.current = null; setRun(null); setEvents([]); setLive(false); };

  // "View Evidence" jump: a defect/heal claim made elsewhere (Report, Healer log) should lead
  // straight to the concrete proof for it, not make the user go hunt through the Runner tab.
  const viewEvidence = useCallback((flowId) => {
    setActiveTab("exec");
    setHighlightFlowId(flowId);
    setTimeout(() => setHighlightFlowId((cur) => (cur === flowId ? null : cur)), 2500);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-[#07090e] text-slate-100 overflow-hidden grain">
      {/* header */}
      <header className="h-14 border-b border-slate-800/80 px-5 flex items-center justify-between bg-[#0a0f18]/90 backdrop-blur-xl z-50 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-400 to-cyan-500 flex items-center justify-center shadow-[0_0_20px_rgba(16,185,129,0.5)]">
            <Zap className="w-4 h-4 text-[#07090e]" strokeWidth={2.5} />
          </div>
          <div>
            <h1 className="font-heading text-lg font-bold tracking-tight leading-none">QAlchemist</h1>
            <div className="font-mono text-[10px] text-slate-500 tracking-widest uppercase">Autonomous Test Orchestration</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="hidden md:flex items-center gap-2 font-mono text-[11px] text-slate-400">
            <Cpu className="w-3.5 h-3.5 text-emerald-400" /> sarvam-105b
          </div>
          <div className="flex items-center gap-2 font-mono text-[11px]">
            {live ? (
              <><Radio className="w-3.5 h-3.5 text-cyan-400 animate-pulse" /><span className="text-cyan-400">LIVE</span></>
            ) : run?.status === "completed" ? (
              <><Activity className="w-3.5 h-3.5 text-emerald-400" /><span className="text-emerald-400">COMPLETED</span></>
            ) : run?.status === "failed" ? (
              <><Activity className="w-3.5 h-3.5 text-rose-400" /><span className="text-rose-400">FAILED</span></>
            ) : run?.status === "aborted" ? (
              <><Activity className="w-3.5 h-3.5 text-amber-400" /><span className="text-amber-400">ABORTED</span></>
            ) : (
              <><Activity className="w-3.5 h-3.5 text-slate-500" /><span className="text-slate-500">IDLE</span></>
            )}
          </div>
          <Button data-testid="new-run-header-button" onClick={newRun} size="sm"
            className="bg-emerald-500 hover:bg-emerald-400 text-[#07090e] font-semibold h-8">
            + New Run
          </Button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        <RunsSidebar runs={runs} activeRunId={activeRunId} onSelect={openRun} onRerun={rerun} />

        <main className="flex-1 flex flex-col overflow-hidden bg-[#07090e]">
          {showForm ? (
            <RunForm onSubmit={startRun} />
          ) : (
            <>
              <PipelineDAG stageStatus={derived.stageStatus} stageDuration={derived.stageDuration}
                run={run} awaiting={derived.awaiting} onResume={resume}
                onAbort={abort} onRerun={() => rerun(activeRunId)}
                activeTab={activeTab} onStageClick={setActiveTab}
                handoffs={derived.handoffs} replan={derived.replan} />
              <HandoffFeed handoffs={derived.handoffs} live={live} onSelect={setActiveTab} />
              <ReviewCallout items={derived.needsReview} onJump={() => setActiveTab("heal")} />
              <div className="flex-1 flex overflow-hidden">
                <div className="flex-1 overflow-hidden flex flex-col border-r border-slate-800/80">
                  <WorkspaceTabs derived={derived} runId={activeRunId} run={run}
                    activeTab={activeTab} onTabChange={setActiveTab} onEventAppend={pushEvent}
                    highlightFlowId={highlightFlowId} onViewEvidence={viewEvidence} />
                </div>
                <EventConsole events={events} live={live} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
