import { STAGES } from "../api";

export function deriveState(events) {
  const stageStatus = {};
  STAGES.forEach((s) => (stageStatus[s] = "pending"));
  const stageDuration = {};
  const stageStart = {};

  const flowsMap = {};
  const gaps = [];
  const prdGaps = [];
  let riskNotes = [];
  const specsMap = {};
  const execMap = {};
  const healer = [];
  const handoffs = [];
  let report = null;
  let awaiting = false;
  let complete = false;

  for (const e of events) {
    const t = e.type;
    if (t === "stage_start") {
      if (stageStatus[e.stage] !== "done") stageStatus[e.stage] = "running";
      stageStart[e.stage] = e.ts;
      if (e.stage === "GENERATE") awaiting = false;
    } else if (t === "stage_complete") {
      stageStatus[e.stage] = "done";
      if (stageStart[e.stage]) {
        stageDuration[e.stage] = new Date(e.ts) - new Date(stageStart[e.stage]);
      }
      if (e.stage === "EVALUATE") awaiting = false;
      if (e.stage === "EVALUATE" && e.data?.evaluation?.risk_notes) {
        riskNotes = e.data.evaluation.risk_notes;
      }
    } else if (t === "awaiting_approval") {
      awaiting = true;
    } else if (t === "resumed" || t === "auto_resume") {
      awaiting = false;
    } else if (t === "plan_flow" && e.data?.flow) {
      flowsMap[e.data.flow.flow_id] = e.data.flow;
    } else if (t === "gap" && e.data?.gap) {
      gaps.push(e.data.gap);
    } else if (t === "prd_gap") {
      prdGaps.push(e.message.replace("PRD gap: ", ""));
    } else if (t === "spec" && e.data?.spec) {
      specsMap[e.data.spec.flow_id] = e.data.spec;
    } else if (t === "spec_healed" && e.data?.spec) {
      // a verified heal patched this spec's code/selectors in place — replace, don't merge, so a
      // spec's `selectors` list always reflects the healed state, not a stale+healed mashup.
      specsMap[e.data.spec.flow_id] = e.data.spec;
    } else if (t === "exec_result" && e.data?.execution) {
      const ex = e.data.execution;
      execMap[ex.flow_id] = { ...(execMap[ex.flow_id] || {}), ...ex };
    } else if (t === "healer_action" && e.data?.action) {
      healer.push(e.data.action);
      const a = e.data.action;
      if (execMap[a.flow_id]) {
        execMap[a.flow_id].final_status =
          a.decision === "script" ? "healed" : a.decision === "defect" ? "defect" : "review";
        execMap[a.flow_id].healer = a;
        // a verified heal replaces the execution's screenshot with the passing replay's — keep the
        // original failure screenshot too so the UI can show proof of both broken and fixed states.
        if (a.artifacts) execMap[a.flow_id].artifacts = a.artifacts;
        if (a.original_artifacts) execMap[a.flow_id].original_artifacts = a.original_artifacts;
      }
    } else if (t === "healer_action_resolved" && e.data) {
      const { action_id, flow_id, resolution, summary_patch, defects } = e.data;
      const hIdx = healer.findIndex((a) => a.id === action_id);
      if (hIdx !== -1) healer[hIdx] = { ...healer[hIdx], decision: resolution === "defect" ? "defect" : "dismissed" };
      if (execMap[flow_id]) execMap[flow_id].final_status = resolution === "defect" ? "defect" : "resolved";
      if (report && summary_patch && Object.keys(summary_patch).length) {
        report = { ...report, summary: { ...report.summary, ...summary_patch },
                  defects: defects !== undefined ? defects : report.defects };
      }
    } else if (t === "handoff" && e.data) {
      handoffs.push({
        id: e.id,
        seq: e.seq,
        from: e.data.from,
        to: e.data.to,
        artifact: e.data.artifact,
        summary: e.data.summary,
        message: e.message,
        stage: e.stage,
        ts: e.ts,
      });
    } else if (t === "report" && e.data?.report) {
      report = e.data.report;
    } else if (t === "run_complete") {
      complete = true;
    }
  }

  // if run finished but a stage was left running, mark done
  if (complete) STAGES.forEach((s) => { if (stageStatus[s] === "running") stageStatus[s] = "done"; });

  return {
    stageStatus, stageDuration,
    flows: Object.values(flowsMap),
    gaps, prdGaps, riskNotes,
    specs: Object.values(specsMap),
    executions: Object.values(execMap),
    healer, report, awaiting, complete,
    handoffs,
    replan: handoffs.some((h) => h.artifact === "feedback"),
    needsReview: healer.filter((a) => a.decision === "review"),
  };
}
