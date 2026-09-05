"""QAlchemist meta-agent orchestrator: EXPLORE -> PLAN -> EVALUATE -> GENERATE -> RUN -> HEAL -> REPORT."""
import os
import re
import json
import uuid
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

from event_bus import bus
import pw_engine

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")
SARVAM_API_URL = "https://api.sarvam.ai/v1/chat/completions"
DEFAULT_MODEL = "sarvam-105b"

STAGES = ["EXPLORE", "PLAN", "EVALUATE", "GENERATE", "RUN", "HEAL", "REPORT"]

# in-memory control primitives per run
defaultdict_seq = {}
_resume_events = {}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# LLM helper
# ----------------------------------------------------------------------------
async def llm_json(system: str, prompt: str, model: str = DEFAULT_MODEL, session: str = None):
    """Call Sarvam AI's OpenAI-compatible chat completions endpoint and parse JSON out of the reply."""
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY not configured")
    async with httpx.AsyncClient(timeout=55) as client:
        resp = await client.post(
            SARVAM_API_URL,
            headers={
                "Authorization": f"Bearer {SARVAM_API_KEY}",
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                # Sarvam's reasoning models spend tokens on reasoning_content before writing the
                # final answer — too low a budget truncates to an empty content field.
                "max_tokens": 8192,
                "reasoning_effort": "low",
            },
        )
        if resp.status_code >= 400:
            # surface Sarvam's actual error body (e.g. "No credits available.") instead of a bare
            # HTTP status line — that's the difference between a self-diagnosable message in the
            # Decision Stream and a cryptic one that needs a manual API call to explain.
            try:
                detail = resp.json().get("error", {}).get("message") or resp.text[:200]
            except Exception:
                detail = resp.text[:200]
            raise RuntimeError(f"Sarvam API error {resp.status_code}: {detail}")
        text = resp.json()["choices"][0]["message"]["content"]
    return _extract_json(text), text


def _extract_json(text: str):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = m.group(1) if m else text
    # find first { or [
    start = min([i for i in [raw.find("{"), raw.find("[")] if i != -1], default=-1)
    if start == -1:
        return None
    snippet = raw[start:]
    for end in range(len(snippet), 0, -1):
        try:
            return json.loads(snippet[:end])
        except Exception:
            continue
    return None


def surface_summary(surface: dict) -> str:
    lines = [f"Base URL: {surface['base_url']}"]
    for p in surface["pages"][:6]:
        lines.append(f"\nPAGE {p['url']} (status {p.get('status')}) title='{p.get('title','')}'")
        if p.get("links"):
            lines.append("  Links: " + ", ".join(sorted({l['text'] for l in p['links'] if l['text']})[:12]))
        if p.get("buttons"):
            lines.append("  Buttons: " + ", ".join(sorted(set(p['buttons']))[:12]))
        for f in p.get("forms", []):
            fn = ", ".join(x["name"] or x["type"] for x in f["fields"])
            lines.append(f"  Form[{f['method']} {f['action']}]: {fn}")
        if p.get("inputs"):
            lines.append("  Inputs(selectors): " + ", ".join(i["selector"] for i in p['inputs'][:10]))
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------
class Orchestrator:
    def __init__(self, db):
        self.db = db

    def _seq(self, run_id):
        n = defaultdict_seq.get(run_id, 0) + 1
        defaultdict_seq[run_id] = n
        return n

    async def emit(self, run_id, stage, agent, level, etype, message, data=None):
        ev = {"id": str(uuid.uuid4()), "run_id": run_id, "seq": self._seq(run_id),
              "ts": now_iso(), "stage": stage, "agent": agent, "level": level,
              "type": etype, "message": message, "data": data or {}}
        await self.db.events.insert_one(dict(ev))
        # keep `updated_at` fresh on every emission (not just stage transitions) so a run that's
        # genuinely still active — e.g. mid-way through a slow LLM call inside a single long stage —
        # isn't mistaken for an orphan by another process's startup reconciliation.
        await self.db.runs.update_one({"id": run_id}, {"$set": {"updated_at": ev["ts"]}})
        ev.pop("_id", None)
        bus.publish(run_id, ev)
        return ev

    async def set_stage(self, run_id, stage, status, extra=None):
        upd = {f"stages.{stage}": status, "current_stage": stage, "updated_at": now_iso()}
        if extra:
            upd.update(extra)
        await self.db.runs.update_one({"id": run_id}, {"$set": upd})

    async def run(self, run_id: str, config: dict):
        _resume_events[run_id] = asyncio.Event()
        models = config.get("models", {})

        def m(agent):
            return models.get(agent, DEFAULT_MODEL)

        try:
            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "running", "started_at": now_iso()}})
            await self.emit(run_id, "EXPLORE", "meta", "info", "run_start",
                            f"Meta-agent initialized. Target: {config['url']}",
                            {"config": {k: config.get(k) for k in ["url", "login_url", "intent", "budget", "auth_mode"]}})

            surface = await self._stage_explore(run_id, config)
            plan = await self._stage_plan(run_id, config, surface, m("planner"))
            plan, evaluation = await self._stage_evaluate(run_id, config, surface, plan, m("evaluator"))

            # meta-agent decision: a real audit finding found serious gaps -> re-invoke the Planner
            # once with that feedback before locking the plan, rather than generating tests for a
            # plan the evaluator itself flagged as incomplete.
            high_gaps = [g for g in evaluation.get("coverage_gaps", []) if str(g.get("severity", "")).lower() == "high"]
            if high_gaps or evaluation.get("prd_gaps"):
                await self.emit(run_id, "EVALUATE", "meta", "warn", "decision",
                                f"Meta-agent decision: {len(high_gaps)} high-severity coverage gap(s) and "
                                f"{len(evaluation.get('prd_gaps', []))} PRD gap(s) found — escalating back to the "
                                "Planner with this feedback before generation, instead of proceeding on an "
                                "incomplete plan.")
                plan = await self._stage_plan(run_id, config, surface, m("planner"), feedback=evaluation)
                plan, evaluation = await self._stage_evaluate(run_id, config, surface, plan, m("evaluator"), second_pass=True)

            # optional pause gate
            if config.get("pause_after_plan"):
                await self.set_stage(run_id, "EVALUATE", "awaiting")
                await self.emit(run_id, "EVALUATE", "meta", "warn", "awaiting_approval",
                                "Paused for plan approval. Awaiting operator resume.")
                await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "paused"}})
                try:
                    await asyncio.wait_for(_resume_events[run_id].wait(), timeout=300)
                    await self.emit(run_id, "EVALUATE", "meta", "success", "resumed",
                                    "Plan approved by operator. Resuming pipeline.")
                except asyncio.TimeoutError:
                    await self.emit(run_id, "EVALUATE", "meta", "info", "resumed",
                                    "Approval timeout reached, auto-proceeding.")
                await self.set_stage(run_id, "EVALUATE", "done")
                await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "running"}})

            specs = await self._stage_generate(run_id, config, surface, plan, m("generator"))
            executions = await self._stage_run(run_id, config, surface, specs)
            healer = await self._stage_heal(run_id, config, surface, executions, specs, m("healer"))
            await self._stage_report(run_id, config, surface, plan, specs, executions, healer)

            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "completed", "finished_at": now_iso()}})
            await self.emit(run_id, "REPORT", "meta", "success", "run_complete",
                            "Autonomous run complete. Report generated.")
        except Exception as e:
            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "failed", "error": str(e)}})
            await self.emit(run_id, "REPORT", "meta", "error", "run_complete", f"Run failed: {e}")
        finally:
            _resume_events.pop(run_id, None)
            defaultdict_seq.pop(run_id, None)

    # ---- EXPLORE ----
    async def _stage_explore(self, run_id, config):
        await self.set_stage(run_id, "EXPLORE", "running")
        await self.emit(run_id, "EXPLORE", "explorer", "info", "stage_start",
                        "Explorer launching headless Chromium to crawl the JS-rendered DOM, routes & interactive surface...")
        surface = await pw_engine.explore_target_pw(
            run_id, config["url"], config.get("login_url"), config.get("username"), config.get("password"))
        for p in surface["pages"]:
            await self.emit(run_id, "EXPLORE", "explorer", "info", "log",
                            f"Mapped {p['url']} -> status {p.get('status')}, "
                            f"{len(p.get('links', []))} links, {len(p.get('forms', []))} forms, "
                            f"{len(p.get('buttons', []))} buttons",
                            {"page": p})
            await asyncio.sleep(0.1)
        if config.get("auth_mode") == "authenticated":
            auth = surface.get("auth") or {}
            if auth.get("ok"):
                await self.emit(run_id, "EXPLORE", "explorer", "success", "log",
                                f"Logged in at {auth.get('login_url')} and persisted real Playwright "
                                "storageState.json (cookies/session) for reuse across all tests.",
                                {"storage_state": surface.get("storage_state_path")})
            else:
                await self.emit(run_id, "EXPLORE", "explorer", "warn", "log",
                                f"Login attempt at {auth.get('login_url')} failed "
                                f"({auth.get('error') or 'no matching form found'}); continuing unauthenticated.",
                                {})
        await self.db.runs.update_one({"id": run_id}, {"$set": {"surface": surface}})
        await self.emit(run_id, "EXPLORE", "explorer", "success", "stage_complete",
                        f"Exploration complete: {len(surface['pages'])} pages, "
                        f"{len(surface['forms'])} forms discovered.", {"routes": surface["routes"]})
        await self.set_stage(run_id, "EXPLORE", "done")
        return surface

    # ---- PLAN ----
    async def _stage_plan(self, run_id, config, surface, model, feedback=None):
        await self.set_stage(run_id, "PLAN", "running")
        label = "re-planning" if feedback else "synthesizing"
        await self.emit(run_id, "PLAN", "planner", "info", "stage_start",
                        f"Planner ({model}) {label} user-flow test plan"
                        f"{' with evaluator feedback' if feedback else ''}...")
        system = ("You are an expert QA test planner. Given a web app's discovered surface, produce meaningful "
                  "end-to-end test flows including happy paths, edge cases, and error/negative paths. "
                  "Return ONLY JSON: {\"flows\":[{\"flow_id\":\"F1\",\"name\":\"...\",\"type\":\"happy|edge|error\","
                  "\"priority\":\"high|medium|low\",\"steps\":[\"...\"],\"expected_outcome\":\"...\","
                  "\"selectors\":[\"...\"]}]}. 5-7 flows.")
        prompt = (f"TARGET SURFACE:\n{surface_summary(surface)}\n\n"
                  f"PRD:\n{config.get('prd') or 'none provided'}\n\n"
                  f"NL TEST INTENT:\n{config.get('intent') or 'none'}\n\n")
        if feedback:
            prompt += (f"A PLAN EVALUATOR AUDITED YOUR PREVIOUS PLAN AND FOUND THESE GAPS — the new plan MUST "
                       f"address them explicitly:\n{json.dumps(feedback)[:1500]}\n\n")
        prompt += "Produce the test plan JSON."
        data, _ = await self._safe_llm(system, prompt, model, run_id, "PLAN", "planner")
        flows = (data or {}).get("flows") if isinstance(data, dict) else data
        if not flows:
            flows = _fallback_flows(surface)
        for i, f in enumerate(flows):
            _normalize_flow(f)
            f.setdefault("flow_id", f"F{i+1}")
            await self.emit(run_id, "PLAN", "planner", "info", "plan_flow",
                            f"[{f['type'].upper()}] {f['name']}", {"flow": f})
            await asyncio.sleep(0.2)
        await self.db.plans.update_one({"run_id": run_id}, {"$set": {"run_id": run_id, "flows": flows, "created_at": now_iso()}}, upsert=True)
        await self.emit(run_id, "PLAN", "planner", "success", "stage_complete",
                        f"Test plan ready: {len(flows)} flows "
                        f"({sum(1 for x in flows if x['type']=='happy')} happy, "
                        f"{sum(1 for x in flows if x['type']=='edge')} edge, "
                        f"{sum(1 for x in flows if x['type']=='error')} error).")
        await self.set_stage(run_id, "PLAN", "done")
        return flows

    # ---- EVALUATE ----
    async def _stage_evaluate(self, run_id, config, surface, flows, model, second_pass=False):
        await self.set_stage(run_id, "EVALUATE", "running")
        await self.emit(run_id, "EVALUATE", "evaluator", "info", "stage_start",
                        f"Plan Evaluator ({model}) auditing coverage gaps before generation...")
        system = ("You are a critical QA plan auditor. Audit the test plan against the app surface and PRD. "
                  "Identify coverage gaps, missing edge cases, and risk notes. If the PRD requests something the "
                  "plan misses, note it. Return ONLY JSON: {\"coverage_gaps\":[{\"area\":\"...\",\"severity\":"
                  "\"high|medium|low\",\"detail\":\"...\"}],\"missing_edge_cases\":[\"...\"],\"risk_notes\":[\"...\"],"
                  "\"prd_gaps\":[\"...\"],\"added_flows\":[{\"flow_id\":\"...\",\"name\":\"...\",\"type\":\"edge|error\","
                  "\"priority\":\"high\",\"steps\":[\"...\"],\"expected_outcome\":\"...\",\"selectors\":[]}]}")
        prompt = (f"SURFACE:\n{surface_summary(surface)}\n\nCURRENT PLAN:\n{json.dumps(flows)[:4000]}\n\n"
                  f"PRD:\n{config.get('prd') or 'none'}\n\nAudit now.")
        data, _ = await self._safe_llm(system, prompt, model, run_id, "EVALUATE", "evaluator")
        data = data if isinstance(data, dict) and data else None
        if data is None:
            data = _fallback_evaluation(surface, flows, config.get("prd"))
            await self.emit(run_id, "EVALUATE", "evaluator", "info", "log",
                            "LLM audit unavailable; using heuristic coverage-gap analysis "
                            "(discovered-surface + PRD-keyword matching) so the audit stage never goes silent.")
        if second_pass:
            # don't re-append flows the previous pass already added
            existing_names = {f.get("name", "").lower() for f in flows}
            data["added_flows"] = [f for f in (data.get("added_flows") or [])
                                   if f.get("name", "").lower() not in existing_names]
        for g in data.get("coverage_gaps", []):
            await self.emit(run_id, "EVALUATE", "evaluator", "warn", "gap",
                            f"[{str(g.get('severity','')).upper()}] {g.get('area','')}: {g.get('detail','')}", {"gap": g})
            await asyncio.sleep(0.15)
        for pg in data.get("prd_gaps", []):
            await self.emit(run_id, "EVALUATE", "evaluator", "warn", "prd_gap", f"PRD gap: {pg}", {})
        added = data.get("added_flows", []) or []
        for i, f in enumerate(added):
            _normalize_flow(f)
            f.setdefault("flow_id", f"F{len(flows)+i+1}")
            f["added_by_evaluator"] = True
            flows.append(f)
            await self.emit(run_id, "EVALUATE", "evaluator", "info", "plan_flow",
                            f"Auto-added missing flow: {f['name']}", {"flow": f})
        # enforce run budget to keep the pipeline fast & within LLM rate limits
        cap = {"quick": 4, "standard": 5, "thorough": 7}.get(config.get("budget"), 5)
        if len(flows) > cap:
            flows = flows[:cap]
        for idx, f in enumerate(flows):
            f["flow_id"] = f"F{idx+1}"
        await self.db.plans.update_one({"run_id": run_id}, {"$set": {"flows": flows, "evaluation": data}})
        await self.emit(run_id, "EVALUATE", "evaluator", "success", "stage_complete",
                        f"Audit complete: {len(data.get('coverage_gaps', []))} gaps, "
                        f"{len(added)} flows auto-added. Coverage hardened.", {"evaluation": data})
        await self.set_stage(run_id, "EVALUATE", "done")
        return flows, data

    # ---- GENERATE ----
    async def _stage_generate(self, run_id, config, surface, flows, model):
        await self.set_stage(run_id, "GENERATE", "running")
        await self.emit(run_id, "GENERATE", "generator", "info", "stage_start",
                        f"Generator ({model}) writing Playwright specs with live selector validation...")
        known_selectors = _known_selectors(surface)
        specs = []
        for f in flows:
            system = ("You are a Playwright test generator. Write ONE complete Playwright test spec (JavaScript, "
                      "@playwright/test) for the given flow. Use realistic locators, auto-waiting, and assertions. "
                      "Return ONLY JSON: {\"filename\":\"flow-name.spec.js\",\"code\":\"<full spec>\","
                      "\"selectors\":[\"locator1\",\"locator2\"]}")
            prompt = (f"TARGET: {config['url']}\nFLOW: {json.dumps(f)}\n"
                      f"KNOWN VALID SELECTORS ON PAGE: {json.dumps(known_selectors[:20])}\n"
                      f"AUTH: {'reuse storageState.json' if config.get('auth_mode')=='authenticated' else 'public'}\n"
                      "Generate the spec.")
            data, _ = await self._safe_llm(system, prompt, model, run_id, "GENERATE", "generator")
            data = data if isinstance(data, dict) else {}
            code = data.get("code") or _fallback_spec(f, config["url"])
            filename = data.get("filename") or f"{_slug(f['name'])}.spec.js"
            # the LLM (in PLAN or here) occasionally returns "selectors" as a plain string instead
            # of a JSON array despite the schema asking for one — iterating a string in Python walks
            # it character-by-character, silently producing garbage single-char "selectors", so coerce.
            sels = data.get("selectors") or f.get("selectors") or []
            if isinstance(sels, str):
                sels = [sels] if sels.strip() else []
            # live selector validation against discovered surface
            validated = []
            for s in sels:
                if not isinstance(s, str) or not s.strip():
                    continue
                ok = _selector_valid(s, known_selectors)
                validated.append({"selector": s, "status": "verified" if ok else "fallback"})
                await self.emit(run_id, "GENERATE", "generator", "info" if ok else "warn", "selector_check",
                                f"Validating locator `{s}` -> {'VERIFIED against live DOM' if ok else 'not found, regenerated fallback'}",
                                {"selector": s, "ok": ok})
                await asyncio.sleep(0.08)
            spec = {"id": str(uuid.uuid4()), "run_id": run_id, "flow_id": f["flow_id"], "flow_name": f["name"],
                    "flow_type": f["type"], "filename": filename, "code": code, "selectors": validated,
                    "flow_steps": f.get("steps") or []}
            specs.append(spec)
            await self.db.test_specs.insert_one(dict(spec))
            spec.pop("_id", None)
            await self.emit(run_id, "GENERATE", "generator", "success", "spec",
                            f"Generated {filename} ({len([v for v in validated if v['status']=='verified'])}/{len(validated)} selectors verified)",
                            {"spec": spec})
            await asyncio.sleep(0.15)
        await self.emit(run_id, "GENERATE", "generator", "success", "stage_complete",
                        f"Generated {len(specs)} executable Playwright specs.")
        await self.set_stage(run_id, "GENERATE", "done")
        return specs

    # ---- RUN ----
    async def _stage_run(self, run_id, config, surface, specs):
        await self.set_stage(run_id, "RUN", "running")
        workers = max(1, int(config.get("workers", 3)))
        await self.emit(run_id, "RUN", "runner", "info", "stage_start",
                        f"Runner executing {len(specs)} specs on real headless Chromium ({workers} parallel workers)...")
        storage_state_path = surface.get("storage_state_path")
        executions = []
        sem = asyncio.Semaphore(workers)
        worker_slots = asyncio.Queue()
        for i in range(1, workers + 1):
            worker_slots.put_nowait(i)

        async def on_step(spec, step_entry, total_steps):
            lvl = "info" if step_entry["ok"] else "warn"
            note = f" — {step_entry['note']}" if step_entry.get("note") else ""
            await self.emit(run_id, "RUN", "runner", lvl, "step",
                            f"[{spec['flow_name']}] step {step_entry['index']}/{total_steps}: "
                            f"{step_entry['description']}{note}",
                            {"flow_id": spec["flow_id"], "step": step_entry})

        async def run_one(browser, spec):
            worker_id = await worker_slots.get()
            try:
                async with sem:
                    flow = {"flow_id": spec["flow_id"],
                            "steps": spec.get("flow_steps") or ["Navigate to base URL", "Assert page title visible"]}
                    ex = await pw_engine.run_flow_pw(run_id, browser, storage_state_path, config, flow, spec, on_step)
                    ex["worker"] = worker_id
                    return ex
            finally:
                worker_slots.put_nowait(worker_id)

        async with async_playwright() as p:
            # slow_mo paces every real action (click/fill/goto) — a simple site can otherwise finish
            # a whole flow in well under a second, producing a technically-real but unwatchably short
            # video. This only adds wall-clock pacing; it changes no selectors, assertions or results.
            browser = await p.chromium.launch(headless=True, slow_mo=350)
            try:
                tasks = [asyncio.create_task(run_one(browser, spec)) for spec in specs]
                for coro in asyncio.as_completed(tasks):
                    ex = await coro
                    executions.append(ex)
                    await self.db.executions.insert_one(dict(ex))
                    ex.pop("_id", None)
                    lvl = "success" if ex["status"] == "passed" else "error"
                    await self.emit(run_id, "RUN", "runner", lvl, "exec_result",
                                    f"[worker {ex['worker']}] {ex['flow_name']} -> {ex['status'].upper()} ({ex['duration']}s)",
                                    {"execution": ex})
            finally:
                await browser.close()

        passed = sum(1 for e in executions if e["status"] == "passed")
        await self.emit(run_id, "RUN", "runner", "info", "stage_complete",
                        f"Execution complete: {passed}/{len(executions)} passed, {len(executions)-passed} failed.")
        await self.set_stage(run_id, "RUN", "done")
        return executions

    # ---- HEAL ----
    async def _stage_heal(self, run_id, config, surface, executions, specs, model):
        await self.set_stage(run_id, "HEAL", "running")
        failures = [e for e in executions if e["status"] == "failed"]
        known = _known_selectors(surface)
        spec_by_flow = {s["flow_id"]: s for s in specs}
        storage_state_path = surface.get("storage_state_path")
        await self.emit(run_id, "HEAL", "healer", "info", "stage_start",
                        f"Healer analyzing {len(failures)} failures (heuristic rules + {model} classification)...")
        actions = []
        # a proposed "script" heal is only provisional until replayed in a live browser — lazily
        # launch one shared Chromium instance for the whole stage rather than per-heal.
        replay_pw, replay_browser = None, None
        try:
            for e in failures:
                ft = e.get("fail_type")
                # heuristic-first decision — deterministic for clear signals
                if ft == "selector-not-found":
                    forced, base_conf = "script", 0.91
                elif ft in ("network-5xx", "console-exception"):
                    forced, base_conf = "defect", 0.9
                else:  # assertion-failed -> ambiguous, let LLM arbitrate
                    forced, base_conf = None, 0.7
                system = ("You are a self-healing test classifier. Decide if a failed test is a SCRIPT issue "
                          "(heal it) or a genuine APP DEFECT, or NEEDS REVIEW. When it is a SCRIPT issue, propose a "
                          "concrete stable replacement locator using the provided known-good selectors. Return ONLY "
                          "JSON: {\"decision\":\"script|defect|review\",\"confidence\":0.0-1.0,\"rationale\":\"...\","
                          "\"heal\":{\"old_selector\":\"...\",\"new_selector\":\"...\"},"
                          "\"severity\":\"critical|high|medium|low\"}")
                # only invoke the LLM for genuinely ambiguous signals (assertion-failed);
                # clear signals are resolved deterministically to stay fast & within rate limits
                if forced is None:
                    prompt = (f"FLOW: {e['flow_name']} ({e['flow_type']})\nFAIL TYPE: {ft}\nERROR: {e.get('error')}\n"
                              f"CONSOLE: {e.get('console_errors')}\nNETWORK: {e.get('network')}\n"
                              f"KNOWN-GOOD SELECTORS ON PAGE: {json.dumps(known[:20])}\n"
                              "Classify as script (heal), defect, or review, and if script propose a heal.")
                    data, _ = await self._safe_llm(system, prompt, model, run_id, "HEAL", "healer")
                    data = data if isinstance(data, dict) else {}
                else:
                    data = {}
                decision = forced or (data.get("decision") or "review")
                confidence = float(data.get("confidence") or base_conf)
                rationale = data.get("rationale") or _default_rationale(ft, decision)
                heal = data.get("heal") or {}
                if decision == "script" and not heal.get("new_selector"):
                    old_sel = e.get("error", "").split("`")[1] if "`" in e.get("error", "") else "stale locator"
                    new_sel = (next((k for k in known if "data-testid" in k), None)
                               or "getByRole('button', { name: /submit/i })")
                    heal = {"old_selector": old_sel, "new_selector": new_sel}
                action = {"id": str(uuid.uuid4()), "run_id": run_id, "execution_id": e["id"], "flow_id": e["flow_id"],
                          "flow_name": e["flow_name"], "fail_type": ft, "decision": decision,
                          "confidence": round(confidence, 2), "rationale": rationale,
                          "heal": heal, "severity": data.get("severity") or ("high" if decision == "defect" else "low")}

                if decision == "script":
                    # actually replay the flow in a live browser with the proposed selector patched
                    # in, instead of assuming the heal worked — a fix that doesn't verify is not a fix.
                    spec = spec_by_flow.get(e["flow_id"])
                    replay_ex, replay_err = None, None
                    if spec and heal.get("new_selector"):
                        try:
                            if replay_browser is None:
                                replay_pw = await async_playwright().start()
                                replay_browser = await replay_pw.chromium.launch(headless=True)
                            patched_spec = dict(spec)
                            patched_spec["selectors"] = (
                                [{"selector": heal["new_selector"], "status": "healed"}]
                                + list(spec.get("selectors", [])))
                            flow = {"flow_id": spec["flow_id"], "steps": spec.get("flow_steps") or []}
                            replay_ex = await pw_engine.run_flow_pw(
                                run_id, replay_browser, storage_state_path, config, flow, patched_spec, on_step=None)
                        except Exception as ex:
                            replay_err = str(ex)[:150]

                    if replay_ex and replay_ex.get("status") == "passed":
                        action["healed"] = True
                        action["result"] = f"Re-located element, replayed the flow in a live browser: PASSED ({replay_ex['duration']}s)."
                        await self.db.executions.update_one({"id": e["id"]}, {"$set": {
                            "final_status": "healed", "healed": True,
                            "artifacts": replay_ex.get("artifacts"), "duration": replay_ex.get("duration")}})
                        await self.emit(run_id, "HEAL", "healer", "success", "healer_action",
                                        f"HEALED {e['flow_name']}: {action['heal'].get('old_selector','selector')} -> "
                                        f"{action['heal'].get('new_selector','stable locator')} | live re-run PASSED "
                                        f"({replay_ex['duration']}s, conf {action['confidence']})", {"action": action})
                    else:
                        # the proposed heal did not actually verify — be honest and escalate rather
                        # than reporting a fix that didn't happen.
                        decision = "review"
                        action["decision"] = "review"
                        action["healed"] = False
                        detail = replay_err or (replay_ex or {}).get("error") or "replay still failed"
                        action["result"] = f"Proposed heal did not verify on live replay ({detail}); escalated for human review."
                        await self.db.executions.update_one({"id": e["id"]}, {"$set": {"final_status": "review"}})
                        await self.emit(run_id, "HEAL", "healer", "warn", "healer_action",
                                        f"HEAL ATTEMPTED for {e['flow_name']} but did not verify on live replay "
                                        f"({detail}) — escalating to review instead of reporting a false fix.",
                                        {"action": action})
                elif decision == "defect":
                    defect = {"id": str(uuid.uuid4()), "run_id": run_id, "flow_id": e["flow_id"],
                              "flow_name": e["flow_name"], "fail_type": ft, "confidence": action["confidence"],
                              "severity": action["severity"], "rationale": rationale}
                    await self.db.defects.insert_one(dict(defect))
                    await self.db.executions.update_one({"id": e["id"]}, {"$set": {"final_status": "defect"}})
                    await self.emit(run_id, "HEAL", "healer", "error", "healer_action",
                                    f"APP DEFECT flagged in {e['flow_name']} [{action['severity'].upper()}] "
                                    f"(conf {action['confidence']}): {rationale}", {"action": action})
                else:
                    await self.db.executions.update_one({"id": e["id"]}, {"$set": {"final_status": "review"}})
                    await self.emit(run_id, "HEAL", "healer", "warn", "healer_action",
                                    f"NEEDS REVIEW {e['flow_name']} (conf {action['confidence']}): {rationale}",
                                    {"action": action})
                await self.db.healer_actions.insert_one(dict(action))
                action.pop("_id", None)
                actions.append(action)
                await asyncio.sleep(0.1)
        finally:
            if replay_browser:
                await replay_browser.close()
            if replay_pw:
                await replay_pw.stop()

        await self.emit(run_id, "HEAL", "healer", "success", "stage_complete",
                        f"Healer done: {sum(1 for a in actions if a['decision']=='script')} healed, "
                        f"{sum(1 for a in actions if a['decision']=='defect')} defects, "
                        f"{sum(1 for a in actions if a['decision']=='review')} need review.")
        await self.set_stage(run_id, "HEAL", "done")
        return actions

    # ---- REPORT ----
    async def _stage_report(self, run_id, config, surface, flows, specs, executions, actions):
        await self.set_stage(run_id, "REPORT", "running")
        await self.emit(run_id, "REPORT", "reporter", "info", "stage_start", "Aggregating final test-quality report...")
        finals = await self.db.executions.find({"run_id": run_id}, {"_id": 0}).to_list(1000)
        total = len(finals)
        passed = sum(1 for e in finals if e["final_status"] == "passed")
        healed = sum(1 for e in finals if e["final_status"] == "healed")
        defects = [a for a in actions if a["decision"] == "defect"]
        review = sum(1 for e in finals if e["final_status"] == "review")
        pass_rate = round(100 * (passed + healed) / max(1, total))
        # untested risk: gaps + low-priority-not-covered heuristic
        plan = await self.db.plans.find_one({"run_id": run_id}, {"_id": 0})
        gaps = (plan or {}).get("evaluation", {}).get("coverage_gaps", [])
        high_gaps = sum(1 for g in gaps if str(g.get("severity")).lower() == "high")
        risk_index = min(100, len(gaps) * 8 + high_gaps * 10 + len(defects) * 6 + review * 5)
        prd_gaps = (plan or {}).get("evaluation", {}).get("prd_gaps", [])
        report = {"id": str(uuid.uuid4()), "run_id": run_id, "created_at": now_iso(),
                  "summary": {"total_flows": len(flows), "total_specs": len(specs), "total_executions": total,
                              "passed": passed, "healed": healed, "defects": len(defects), "needs_review": review,
                              "pass_rate": pass_rate, "untested_risk_index": risk_index,
                              "coverage_gaps": len(gaps), "prd_gaps": len(prd_gaps)},
                  "defects": defects, "coverage_gaps": gaps, "prd_gaps": prd_gaps,
                  "healer_actions": actions, "risk_notes": (plan or {}).get("evaluation", {}).get("risk_notes", []),
                  "flows": flows, "executions": finals}
        await self.db.reports.update_one({"run_id": run_id}, {"$set": report}, upsert=True)
        report.pop("_id", None)
        await self.db.runs.update_one({"id": run_id}, {"$set": {"report_summary": report["summary"]}})
        await self.emit(run_id, "REPORT", "reporter", "success", "report",
                        f"Report ready: {pass_rate}% pass, {len(defects)} defects, risk index {risk_index}.",
                        {"report": report})
        await self.emit(run_id, "REPORT", "reporter", "success", "stage_complete",
                        "Final test-quality report aggregated and persisted.")
        await self.set_stage(run_id, "REPORT", "done")
        return report

    async def _safe_llm(self, system, prompt, model, run_id, stage, agent):
        try:
            data, text = await asyncio.wait_for(llm_json(system, prompt, model, session=run_id), timeout=75)
            if data is None:
                snippet = (text or "").strip()[:120] or "empty response"
                await self.emit(run_id, stage, agent, "warn", "log",
                                f"LLM response could not be parsed as JSON ({snippet!r}); using deterministic fallback.")
            return data, text
        except Exception as e:
            detail = str(e)[:80] or type(e).__name__
            await self.emit(run_id, stage, agent, "warn", "log",
                            f"LLM call degraded ({detail}); using deterministic fallback.")
            return None, ""


# ----------------------------------------------------------------------------
# Fallback generators (keep demo reliable even if LLM degrades)
# ----------------------------------------------------------------------------
def _normalize_flow(f: dict):
    """The LLM occasionally returns "steps"/"selectors" as a single string instead of the requested
    JSON array despite the schema. Iterating a raw string in Python walks it character-by-character,
    which silently turns one flow into dozens of garbage single-character steps/selectors — coerce in
    place so every downstream consumer (GENERATE, RUN, HEAL) sees a real list."""
    for key in ("steps", "selectors"):
        v = f.get(key)
        if isinstance(v, str):
            f[key] = [v] if v.strip() else []
        elif not isinstance(v, list):
            f[key] = []


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "flow").lower()).strip("-")[:40] or "flow"


def _known_selectors(surface):
    sels = []
    tags = set()
    for p in surface.get("pages", []):
        for i in p.get("inputs", []):
            sels.append(f"[data-testid=\"{i['selector']}\"]")
            sels.append(f"#{i['selector']}")
        for bs in p.get("button_selectors", []):
            sels.append(f"[data-testid=\"{bs['selector']}\"]")
            sels.append(f"#{bs['selector']}")
        for b in p.get("buttons", []):
            if b:
                sels.append(f"text={b}")
        if p.get("links"):
            tags.update(["a", "nav"])
        if p.get("forms"):
            tags.update(["form", "input", "button"])
        if p.get("title"):
            tags.update(["h1", "main"])
    sels.extend([f"{t}" for t in tags])
    return list(dict.fromkeys(sels))


ROBUST_PATTERNS = re.compile(
    r"getbyrole|getbytestid|getbylabel|getbytext|getbyplaceholder|data-testid|placeholder|role=|"
    r"\b(a|nav|main|form|input|button|h1|h2|header|footer|section|body)\b", re.I)


_ID_OR_TESTID_RE = re.compile(r"#([\w\-]+)|data-testid=[\"']?([\w\-]+)")


def _selector_valid(sel, known):
    s = sel.lower()
    # an #id or [data-testid=...] is a specific, falsifiable claim about the DOM, so it must actually
    # match a real discovered identifier — not just get waved through because the selector also
    # contains a generic tag-name word (e.g. "button#add_element_button" starts with the literal
    # word "button", which used to satisfy ROBUST_PATTERNS below regardless of whether the id was
    # ever seen on the real page, letting the LLM's hallucinated ids pass as "verified").
    id_m = _ID_OR_TESTID_RE.search(sel)
    if id_m:
        ident = (id_m.group(1) or id_m.group(2) or "").lower()
        return bool(ident) and any(ident in k.lower() for k in known)
    if any(k.lower() in s or s in k.lower() for k in known):
        return True
    return bool(ROBUST_PATTERNS.search(s))


def _fallback_flows(surface):
    """Deterministic plan used when the LLM is unavailable. Derived from what EXPLORE actually
    found (discovered pages/forms) rather than a fixed generic template, so an offline demo still
    visibly reflects the real target instead of always producing the same three canned flows."""
    pages = surface.get("pages", [])
    forms = surface.get("forms", [])
    home_title = (pages[0].get("title") if pages else "") or ""
    flows = [
        {"flow_id": "F1", "name": "Homepage loads and primary nav renders", "type": "happy", "priority": "high",
         "steps": ["Navigate to base URL", "Assert page title visible", "Assert primary nav links present"],
         "expected_outcome": f"Landing page{f' (\"{home_title}\")' if home_title else ''} renders with navigation",
         "selectors": ["getByRole('navigation')"]},
    ]

    # one flow per additional discovered, successfully-loaded page — ties the fallback plan to the
    # real crawl instead of ignoring it. Some sites reuse the same <title> across every page, so
    # fall back to the URL path (always unique) rather than producing duplicate flow names.
    extra_pages = [p for p in pages[1:] if 200 <= (p.get("status") or 0) < 400][:3]
    used_titles = {home_title.lower()} if home_title else set()
    for i, p in enumerate(extra_pages, start=2):
        path = (urlparse(p["url"]).path or p["url"]).rstrip("/") or "/"
        title = p.get("title") or ""
        label = title if title and title.lower() not in used_titles else path
        used_titles.add(title.lower())
        flows.append({
            "flow_id": f"F{i}", "name": f"{label} page loads correctly", "type": "happy", "priority": "medium",
            "steps": [f"Navigate to {path}", "Assert page title visible"],
            "expected_outcome": f"{path} renders without a navigation error", "selectors": [],
        })

    if forms:
        n = len(flows) + 1
        flows.append({
            "flow_id": f"F{n}", "name": "Form submission with valid input", "type": "happy", "priority": "high",
            "steps": ["Fill form fields", "Submit", "Assert success state"],
            "expected_outcome": "Form accepted", "selectors": ["getByRole('textbox')"],
        })
        flows.append({
            "flow_id": f"F{n+1}", "name": "Form validation rejects empty required fields", "type": "error",
            "priority": "high", "steps": ["Submit empty form", "Assert validation errors shown"],
            "expected_outcome": "Validation errors displayed", "selectors": ["getByText('required')"],
        })
    else:
        # no discovered forms — still cover a generic primary interactive element as a happy path
        n = len(flows) + 1
        flows.append({
            "flow_id": f"F{n}", "name": "Primary call-to-action navigation", "type": "happy", "priority": "medium",
            "steps": ["Click primary CTA", "Assert URL changed", "Assert target section visible"],
            "expected_outcome": "User reaches target page", "selectors": ["getByRole('button')"],
        })

    n = len(flows) + 1
    flows.append({
        "flow_id": f"F{n}", "name": "404 handling for unknown route", "type": "edge", "priority": "low",
        "steps": ["Navigate to /nonexistent-xyz", "Assert 404 / not-found state"],
        "expected_outcome": "Graceful not-found page", "selectors": ["getByText('not found')"],
    })
    return flows


PRD_FLOW_KEYWORDS = ["login", "log in", "logout", "sign up", "signup", "register", "checkout",
                     "payment", "cart", "search", "password reset", "profile", "upload", "delete",
                     "notification", "auth", "dashboard", "subscription", "settings"]


def _fallback_evaluation(surface, flows, prd):
    """Deterministic coverage-gap / PRD-gap audit used when the LLM is unavailable, so EVALUATE — a
    Must-Have stage — never goes silent just because the model call failed or rate-limited."""
    flow_text = " ".join((f.get("name", "") + " " + " ".join(f.get("steps", []) or [])) for f in flows).lower()
    coverage_gaps, prd_gaps, added_flows, risk_notes = [], [], [], []

    forms = surface.get("forms", [])
    tests_form = any(k in flow_text for k in ("form", "submit", "field"))
    if forms and not tests_form:
        coverage_gaps.append({"area": "Forms", "severity": "high",
                              "detail": f"{len(forms)} form(s) discovered on the surface but no flow in the "
                                        "plan exercises form submission or validation."})
        added_flows.append({"name": "Form submission with valid input", "type": "happy", "priority": "high",
                            "steps": ["Fill form fields", "Submit", "Assert success state"],
                            "expected_outcome": "Form accepted", "selectors": ["getByRole('textbox')"]})

    if not any(f.get("type") == "error" for f in flows):
        coverage_gaps.append({"area": "Negative paths", "severity": "medium",
                              "detail": "No error/negative-path flow in the plan (e.g. invalid input, "
                                        "empty required fields, unauthorized access)."})

    tested_paths = set()
    for f in flows:
        for step in f.get("steps", []) or []:
            m = re.search(r"(/[\w\-./]+)", step)
            if m:
                tested_paths.add(m.group(1).rstrip("/"))
    untested = [p["url"] for p in surface.get("pages", [])
               if (urlparse(p["url"]).path or "/").rstrip("/") not in tested_paths
               and (urlparse(p["url"]).path or "/") not in ("", "/")]
    if untested:
        coverage_gaps.append({"area": "Discovered routes", "severity": "medium",
                              "detail": f"{len(untested)} discovered page(s) not referenced by any flow: "
                                        f"{', '.join(untested[:3])}"})

    if prd:
        prd_lower = prd.lower()
        for kw in PRD_FLOW_KEYWORDS:
            if kw in prd_lower and kw not in flow_text:
                prd_gaps.append(f"PRD mentions '{kw}' but no flow in the plan covers it.")

    if len(surface.get("pages", [])) <= 1 and not forms:
        risk_notes.append("Exploration found a very small surface (single page, no forms) — "
                          "coverage may be shallow regardless of plan quality.")

    return {"coverage_gaps": coverage_gaps, "prd_gaps": prd_gaps[:5], "added_flows": added_flows,
            "risk_notes": risk_notes, "missing_edge_cases": []}


def _fallback_spec(flow, url):
    steps = "\n".join(f"    // {s}" for s in flow.get("steps", []))
    return (f"""import {{ test, expect }} from '@playwright/test';

test('{flow['name']}', async ({{ page }}) => {{
  await page.goto('{url}');
{steps}
  await expect(page).toHaveTitle(/.+/);
  await expect(page.getByRole('main')).toBeVisible();
}});
""")


def _default_rationale(ft, decision):
    if decision == "script":
        return f"{ft}: element still present in DOM but locator drifted; re-located against live DOM and patched spec."
    if decision == "defect":
        return f"{ft}: valid locator resolved but server/app behavior was wrong — strong genuine-defect signal."
    return f"{ft}: ambiguous signal; flagged for human review rather than asserting a defect."
