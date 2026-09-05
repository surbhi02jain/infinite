# Agent handoff workflow UI

Date: 2026-09-05  
Status: approved for implementation after user review of this spec

## Problem

QAlchemist already streams every stage decision, but the run screen does not show **who handed what to whom**. The top bar is a linear state machine of stage names. The Evaluator → Planner re-plan loop is invisible except as a line in the Decision Stream. Operators (and demo viewers) cannot see the agent conversation.

## Goal

Make inter-agent communication first-class:

1. The existing DAG shows **agents**, **artifact labels on edges**, and the **re-plan back-edge** when it happens.
2. A compact **Handoff feed** under the DAG lists each transfer as it fires, and is replayable on completed runs.
3. The source of truth is a new SSE event type, `handoff`, emitted by the orchestrator at each real transfer.

Out of scope: new graph library, new workspace tab, Meta node on the DAG, operator-pause / human-review as handoffs, migrating historical runs.

## Agents and stages (unchanged)

Stages: `EXPLORE → PLAN → EVALUATE → GENERATE → RUN → HEAL → REPORT`

| Agent id | Display name | Stage |
|---|---|---|
| `meta` | Meta-agent | coordinator (not a DAG node) |
| `explorer` | Explorer | EXPLORE |
| `planner` | Planner | PLAN |
| `evaluator` | Evaluator | EVALUATE |
| `generator` | Generator | GENERATE |
| `runner` | Runner | RUN |
| `healer` | Healer | HEAL |
| `reporter` | Reporter | REPORT |
| `operator` | Operator | not a DAG node; only the `to` of the final report handoff |

Meta stays the messenger. The feed shows **Evaluator → Planner**, never Meta → Planner.

## Event contract

Reuse the existing `emit(...)` envelope. No new API, no new Mongo collection.

```text
type:    "handoff"
stage:   the sender's stage (EVALUATE for the re-plan feedback handoff)
agent:   sender agent id
level:   "info" (re-plan feedback uses "warn" so it matches today's decision emphasis)
message: "{FromDisplay} → {ToDisplay}: {summary}"
data: {
  from:     "<sender agent id>",
  to:       "<receiver agent id>",
  artifact: "surface" | "flows" | "evaluation" | "feedback" | "specs" | "executions" | "healer_actions" | "report",
  summary:  "<short human summary>"
}
```

Emit **after** the artifact is persisted, only if that stage produced something. Failed or aborted stages emit no outgoing handoff. Do not invent a `failed_handoff` type.

### Transfers

| # | from | to | artifact | When | `summary` |
|---|---|---|---|---|---|
| 1 | explorer | planner | `surface` | EXPLORE persisted | `{n} pages, {m} forms` |
| 2 | planner | evaluator | `flows` | PLAN persisted (every pass, including re-plan) | `{n} flows` or `{n} flows (fallback)` |
| 3 | evaluator | planner | `feedback` | Meta decides to re-plan (high-severity coverage gaps or any PRD gaps) | `{n} high-severity coverage gap(s), {m} PRD gap(s)` |
| 4 | evaluator | generator | `evaluation` | EVALUATE persisted and pipeline proceeds | `{n} gaps, {m} flows auto-added` |
| 5 | generator | runner | `specs` | GENERATE persisted | `{n} specs` |
| 6 | runner | healer | `executions` | RUN persisted | `{n} executions, {p} passed, {f} failed` |
| 7 | healer | reporter | `healer_actions` | HEAL persisted | `{n} actions` |
| 8 | reporter | operator | `report` | REPORT persisted | `{pass_rate}% pass, {d} defects` |

A clean run emits 7 handoffs (1, 2, 4–8). A re-plan run emits 8+: (3) plus a second (2) after the Planner runs again. EVALUATE still emits (4) once, after the last evaluate pass, when generation is allowed to start.

Operator pause and healer-to-human escalation are **not** handoffs.

### LLM fallback

If a stage used `_fallback_*`, still emit the handoff. The artifact is real. Append ` (fallback)` to `summary` when the planner (or other LLM stage) used its deterministic fallback.

## Backend

Add `Orchestrator._handoff(run_id, stage, frm, to, artifact, summary, level="info")` that wraps `emit` with `type="handoff"` and the `data` object above.

Call sites (next to the existing persist + `stage_complete`, or next to the existing re-plan `decision` for #3):

- `_stage_explore` after `surface` is written
- `_stage_plan` after `plans` upsert (both first pass and feedback pass)
- `run()` when the re-plan `decision` is emitted
- `_stage_evaluate` after plan+evaluation upsert, only on the pass that will proceed to GENERATE (not on the first pass if a re-plan is about to happen). The orchestrator already knows this: emit #4 at the same moment today's code would continue to GENERATE, not inside `_stage_evaluate` before the re-plan check.
- `_stage_generate` after specs are stored
- `_stage_run` after executions are stored
- `_stage_heal` after healer actions are stored
- `_stage_report` after the report is stored

Placement for #4 is important: if `_stage_evaluate` always emitted `evaluator → generator`, a re-plan run would lie. Emit #4 from `run()` once, immediately before `_stage_generate` — after the re-plan branch **and** after the optional `pause_after_plan` gate — so the EVALUATE → GENERATE edge does not light while the run is still paused.

## Frontend

### `derive.js`

- `derived.handoffs`: events with `type === "handoff"`, in `seq` order, each entry `{ id, from, to, artifact, summary, message, stage, ts }`.
- `derived.replan`: `true` iff any handoff has `artifact === "feedback"`.

### `api.js`

Keep `STAGES` and `STAGE_META` as they are (already has display `agent` per stage). Add:

- `AGENT_DISPLAY`: agent id → "Explorer" / "Planner" / …
- `HANDOFF_TAB`: receiver agent id → workspace tab value  
  `planner→plan`, `evaluator→eval`, `generator→code`, `runner→exec`, `healer→heal`, `reporter→report`, `operator→report`

### `PipelineDAG.js`

- Each node: stage name on line 1, agent display name on line 2 (from `STAGE_META[stage].agent`).
- Connector between node `i` and `i+1` shows the artifact label once a matching happy-path `from → to` handoff exists (the pair for that edge). Pending edges stay unlabeled and dim.
- When `derived.replan` is true, draw a curved amber **re-plan** arc above the PLAN ↔ EVALUATE pair. When false, omit the arc so a clean run stays linear.
- Click behavior unchanged (`onStageClick` → existing tab map).

Happy-path edge pairs (for labels):

| Edge | from → to | artifact |
|---|---|---|
| EXPLORE → PLAN | explorer → planner | surface |
| PLAN → EVALUATE | planner → evaluator | flows |
| EVALUATE → GENERATE | evaluator → generator | evaluation |
| GENERATE → RUN | generator → runner | specs |
| RUN → HEAL | runner → healer | executions |
| HEAL → REPORT | healer → reporter | healer_actions |

The report handoff has no following DAG node; it only appears in the feed.

### `HandoffFeed.js` (new)

- Rendered in `Dashboard.js` directly under `PipelineDAG`, above `ReviewCallout`.
- Horizontal, newest on the right, overflow-x scroll.
- Chip text: `{From} → {To} · {artifact} · {summary}`
- Live: last chip pulses. Idle/complete: static.
- Empty (no handoffs, including historical runs): `Awaiting first handoff…`
- Click chip → `onSelect(HANDOFF_TAB[to])` → existing `setActiveTab`.
- Test ids: container `handoff-feed`; each chip `handoff-chip-{seq}` (unique even when Planner → Evaluator fires twice).

### Decision Stream

No special case. Handoff events appear as normal rows (sender badge + `message`). Stage filters still work.

### What does not change

Workspace tabs, EventConsole layout, SSE client, run form, report export.

## Error and edge cases

| Case | Behavior |
|---|---|
| Stage throws | No outgoing handoff from that stage. Feed stops at the last success. DAG edge stays unlabeled. Existing `run_complete` error path unchanged. |
| No re-plan | No `feedback` event, no amber arc, 7 feed chips. |
| Re-plan | `feedback` chip + second `planner → evaluator` chip; amber arc on. |
| LLM fallback | Handoff still emitted; summary may include ` (fallback)`. |
| Historical runs without `handoff` | Empty feed; DAG looks as it does today (no labels, no arc). No migration. |

## Tests

Extend `backend/tests/backend_test.py` only. No new frontend test harness.

1. Add `"handoff"` to the required types in `test_event_types_persisted`.
2. New `test_happy_path_handoffs(completed_run)`: extract `type=="handoff"` events. The seven happy-path `(from, to, artifact)` tuples must appear **in order** as a subsequence. Extra handoffs are allowed (re-plan). Each required event has `data.from`, `data.to`, `data.artifact`, `data.summary`, and a `message` containing `→`.
3. New `test_replan_handoff_when_present(completed_run)`: if any handoff has `artifact=="feedback"`, assert `from=="evaluator"`, `to=="planner"`, and a later `planner → evaluator` / `flows` handoff exists. If the run did not re-plan, the test passes without requiring feedback. Do not add a new full-pipeline fixture solely to force a re-plan (that is data-dependent on the live audit).
4. Failed-run path is a **call-site invariant**, not a new integration fixture: `_handoff` is only invoked after a successful persist, never inside `run()`'s `except`. No new long-running failed-run test.

## Implementation notes

- Keep summaries short (one line). Do not put the full surface/plan JSON in `data`.
- Reuse existing stage colors from `STAGE_META` for chip accents.
- Do not change `STAGES` order or add a META stage.
