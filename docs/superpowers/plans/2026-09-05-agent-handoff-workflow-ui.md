# Agent Handoff Workflow UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit first-class `handoff` SSE events at each agent transfer and show them on the run screen as a labeled DAG plus a compact handoff feed.

**Architecture:** `Orchestrator._handoff` wraps existing `emit`. Frontend `deriveState` collects those events. `PipelineDAG` labels edges and draws a re-plan arc; new `HandoffFeed` lists chips under the DAG. No new API or collection.

**Tech Stack:** FastAPI / Mongo / existing SSE · React + Tailwind · pytest for backend

## Global Constraints

- Event `type` is exactly `handoff`; `data` keys are `from`, `to`, `artifact`, `summary`.
- Artifact values: `surface` | `flows` | `evaluation` | `feedback` | `specs` | `executions` | `healer_actions` | `report`.
- Evaluator → Generator handoff fires once, after re-plan and after `pause_after_plan`, immediately before GENERATE.
- Meta is not a DAG node. Re-plan feed shows Evaluator → Planner.
- Do not change `STAGES` order. No new frontend test harness. No commit unless the user asks.

---

### Task 1: Handoff helper + backend tests

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/tests/backend_test.py`

**Interfaces:**
- Produces: `AGENT_DISPLAY` dict; `handoff_message(frm, to, summary) -> str`; `Orchestrator._handoff(run_id, stage, frm, to, artifact, summary, level="info")`

- [ ] **Step 1:** Add unit test for `handoff_message` and integration assertions in `backend_test.py` (required type `handoff`, happy-path subsequence, optional re-plan).
- [ ] **Step 2:** Run unit test; expect fail (helper missing).
- [ ] **Step 3:** Implement `AGENT_DISPLAY`, `handoff_message`, `_handoff`, and all eight call sites.
- [ ] **Step 4:** Re-run unit test; expect pass. Integration tests need a live completed run (existing fixture).

### Task 2: Derive handoffs

**Files:**
- Modify: `frontend/src/lib/derive.js`
- Modify: `frontend/src/api.js`

**Interfaces:**
- Produces: `derived.handoffs` (`{ id, seq, from, to, artifact, summary, message, stage, ts }[]`); `derived.replan`; `AGENT_DISPLAY`; `HANDOFF_TAB`; `EDGE_HANDOFF`

- [ ] Collect `type === "handoff"` in seq order; `replan` iff any artifact is `feedback`.
- [ ] Add maps in `api.js` as specified in the design doc.

### Task 3: DAG + HandoffFeed UI

**Files:**
- Modify: `frontend/src/components/PipelineDAG.js`
- Create: `frontend/src/components/HandoffFeed.js`
- Modify: `frontend/src/components/Dashboard.js`

**Interfaces:**
- Consumes: `derived.handoffs`, `derived.replan`, `HANDOFF_TAB`, `STAGE_META`
- Produces: labeled edges, optional amber re-plan arc, chip feed under DAG

- [ ] Node line 2 = agent name. Edge label = artifact once matching handoff exists.
- [ ] Re-plan arc only when `replan`. Click chip → `HANDOFF_TAB[to]`.
- [ ] Test ids: `handoff-feed`, `handoff-chip-{seq}`.

### Task 4: Verify

- [ ] Backend unit test for `handoff_message`.
- [ ] Browser: empty feed on new run, chips appear live, node shows agent, click chip switches tab.
