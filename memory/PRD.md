# AutoQA — Autonomous Test Orchestration Agent

## Original Problem Statement
A web-based app where a user pastes a target web-app URL (optionally login credentials and/or a PRD),
and an autonomous meta-agent plans, generates, runs, and self-heals end-to-end browser tests, then
produces a test-quality report. Built for the Bessemer Tech Catalyst challenge. Meta-agent orchestrates
sub-agents in a state machine: EXPLORE → PLAN → EVALUATE → GENERATE → RUN → HEAL → REPORT, streaming
every decision live for explainability.

## User Choices
- LLM: Gemini 3.5 Flash via **Emergent Universal Key** (per-agent swappable in the run form).
- Execution: **real HTTP exploration + real LLM generation**, **simulated/lightweight runner** (by design, for reliable demo).
- Artifacts: local metadata only (no cloud object storage).
- Report export: **HTML + JSON**.
- Plan approval: **autonomous with an optional pause toggle**.

## Architecture
- **Frontend**: React + Tailwind + shadcn/ui, obsidian tactical dark theme. SSE live stream with
  auto-reconnect (`?after_seq=`) + 5s polling backstop. State derived from the event stream (`lib/derive.js`).
  Components: Dashboard, RunForm (+presets & per-agent model config), PipelineDAG, EventConsole,
  WorkspaceTabs (Plan/Audit/Code/Runner/Healer/Report), RunsSidebar. Custom syntax-highlighted code viewer.
- **Backend**: FastAPI async. `orchestrator.py` = state-machine meta-agent + sub-agents + explorer
  (httpx + BeautifulSoup) + `emergentintegrations` Gemini calls with deterministic fallbacks.
  `event_bus.py` = in-memory pub/sub for SSE. `report_export.py` = standalone HTML report.
  Startup reconciliation clears orphaned runs.
- **DB**: MongoDB collections — runs, events, plans, test_specs, executions, healer_actions, defects, reports.
  All ids are uuid strings; `_id` excluded from responses.

## Personas
- QA engineers / SDETs wanting autonomous regression coverage.
- Dev teams shipping without dedicated QA.
- Hackathon evaluators judging autonomy, intelligence, explainability, demo clarity.

## Core Requirements (static)
- Single input (URL + optional login/PRD/NL intent), fully autonomous pipeline.
- Planner (DOM/route exploration → human-readable plan of happy + non-happy flows).
- Plan Evaluator (coverage-gap + PRD-gap analysis, auto-adds missing flows).
- Generator (Playwright specs + live selector validation vs discovered DOM).
- Runner + Healer (heuristic-first, confidence-scored script-vs-defect classification; self-heals selectors).
- Final report: scenarios, pass/fail, healer actions, defects (confidence), coverage gaps, untested-flow risk,
  PRD-gap analysis, exportable HTML/JSON.

## Implemented (2026-06)
- Full 7-stage pipeline, live SSE streaming (resilient to ~60s ingress cap), pause/resume gate.
- Real target exploration, real LLM plan/eval/generate, deterministic+LLM healer, simulated runner.
- Per-agent model config, budget-based flow cap (quick=4/standard=5/thorough=7).
- Presets (E-commerce, SaaS auth, AI chat, Fintech), runs history, HTML/JSON export (blob download).
- Verified: backend 26/26 pytest; frontend live-run to REPORT, all tabs/exports/copy/pause-resume/history.

## Backlog (P1/P2)
- P1: Retry/backoff on Gemini RateLimitError before deterministic fallback (heavy back-to-back usage).
- P2: Real headless Chromium runner behind a queue (currently simulated by design).
- P2: Cloud object storage for real traces/videos/screenshots.
- P2: Run-to-run comparison / regression diff in the sidebar.
- P2: Editable plan approval (accept/reject individual flows during the pause gate).

## Next Tasks
- Optional LLM backoff; real Playwright execution mode; artifact object storage.
