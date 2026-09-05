import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "sonner";
import { Activity, Radio, Cpu, Zap } from "lucide-react";
import { api, streamRun, STAGES } from "@/api";
import { deriveState } from "@/lib/derive";
import RunForm from "@/components/RunForm";
import RunsSidebar from "@/components/RunsSidebar";
import PipelineDAG from "@/components/PipelineDAG";
import EventConsole from "@/components/EventConsole";
import WorkspaceTabs from "@/components/WorkspaceTabs";
import { Button } from "@/components/ui/button";

export default function Dashboard() {
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [run, setRun] = useState(null);
  const [events, setEvents] = useState([]);
  const [showForm, setShowForm] = useState(true);
  const [live, setLive] = useState(false);
  const [activeTab, setActiveTab] = useState("plan");
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
        if (["completed", "failed"].includes(data.status)) finishLive(runId);
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

  useEffect(() => () => cleanup(), [cleanup]);

  const newRun = () => { cleanup(); setShowForm(true); setActiveRunId(null); activeRef.current = null; setRun(null); setEvents([]); setLive(false); };

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
        <RunsSidebar runs={runs} activeRunId={activeRunId} onSelect={openRun} />

        <main className="flex-1 flex flex-col overflow-hidden bg-[#07090e]">
          {showForm ? (
            <RunForm onSubmit={startRun} />
          ) : (
            <>
              <PipelineDAG stageStatus={derived.stageStatus} stageDuration={derived.stageDuration}
                run={run} awaiting={derived.awaiting} onResume={resume}
                activeTab={activeTab} onStageClick={setActiveTab} />
              <div className="flex-1 flex overflow-hidden">
                <div className="flex-1 overflow-hidden flex flex-col border-r border-slate-800/80">
                  <WorkspaceTabs derived={derived} runId={activeRunId} run={run}
                    activeTab={activeTab} onTabChange={setActiveTab} />
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
