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
      }
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
    needsReview: healer.filter((a) => a.decision === "review"),
  };
}
