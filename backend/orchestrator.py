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

AGENT_DISPLAY = {
    "meta": "Meta-agent",
    "explorer": "Explorer",
    "planner": "Planner",
    "evaluator": "Evaluator",
    "generator": "Generator",
    "runner": "Runner",
    "healer": "Healer",
    "reporter": "Reporter",
    "operator": "Operator",
}


def handoff_message(frm: str, to: str, summary: str) -> str:
    return f"{AGENT_DISPLAY.get(frm, frm)} → {AGENT_DISPLAY.get(to, to)}: {summary}"

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
    # not just [:6] — action-chain-discovered pages (cart/checkout-style screens only reachable via
    # a button click, appended after the link crawl) must survive this cap too, or the Planner never
    # even sees the surface evidence for the flows we most want it to write.
    for p in surface["pages"][:10]:
        via = " [reached by clicking a button, not a link]" if p.get("discovered_via") == "action_chain" else ""
        lines.append(f"\nPAGE {p['url']}{via} (status {p.get('status')}) title='{p.get('title','')}'")
        if p.get("links"):
            lines.append("  Links: " + ", ".join(sorted({l['text'] for l in p['links'] if l['text']})[:12]))
        if p.get("buttons"):
            lines.append("  Buttons: " + ", ".join(sorted(set(p['buttons']))[:12]))
        if p.get("button_selectors"):
            # real id / data-test(id) identifiers paired with visible text, e.g. "Add to cart"~add-to-
            # cart-sauce-labs-backpack — without this the Planner/Generator only ever see generic
            # button text and can't write a stable locator for it. Deliberately not prefixed with "#"
            # or "data-testid=" — the identifier may come from either an id or a data-test/data-testid
            # attribute and we can't cheaply tell which here, so GENERATE should resolve it with
            # getByTestId(...) or whichever of #id / [data-test] / [data-testid] actually matches.
            lines.append("  Button selectors (id or data-test/data-testid identifier): " + ", ".join(
                f"\"{b['text']}\"~{b['selector']}" for b in p['button_selectors'][:12] if b.get('text')))
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

    async def _handoff(self, run_id, stage, frm, to, artifact, summary, level="info"):
        return await self.emit(
            run_id, stage, frm, level, "handoff",
            handoff_message(frm, to, summary),
            {"from": frm, "to": to, "artifact": artifact, "summary": summary},
        )

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
                n_prd = len(evaluation.get("prd_gaps") or [])
                await self.emit(run_id, "EVALUATE", "meta", "warn", "decision",
                                f"Meta-agent decision: {len(high_gaps)} high-severity coverage gap(s) and "
                                f"{n_prd} PRD gap(s) found — escalating back to the "
                                "Planner with this feedback before generation, instead of proceeding on an "
                                "incomplete plan.")
                await self._handoff(
                    run_id, "EVALUATE", "evaluator", "planner", "feedback",
                    f"{len(high_gaps)} high-severity coverage gap(s), {n_prd} PRD gap(s)",
                    level="warn")
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

            n_gaps = len(evaluation.get("coverage_gaps") or [])
            n_added = len(evaluation.get("added_flows") or [])
            eval_fb = " (fallback)" if evaluation.get("_fallback") else ""
            await self._handoff(
                run_id, "EVALUATE", "evaluator", "generator", "evaluation",
                f"{n_gaps} gaps, {n_added} flows auto-added{eval_fb}")

            specs = await self._stage_generate(run_id, config, surface, plan, m("generator"))
            executions = await self._stage_run(run_id, config, surface, specs)
            healer = await self._stage_heal(run_id, config, surface, executions, specs, m("healer"))
            await self._stage_report(run_id, config, surface, plan, specs, executions, healer)

            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "completed", "finished_at": now_iso()}})
            await self.emit(run_id, "REPORT", "meta", "success", "run_complete",
                            "Autonomous run complete. Report generated.")
        except asyncio.CancelledError:
            await self.db.runs.update_one({"id": run_id}, {"$set": {
                "status": "aborted", "error": "aborted by operator", "finished_at": now_iso()}})
            try:
                await self.emit(run_id, "REPORT", "meta", "warn", "run_complete",
                                "Run aborted by operator.")
            except Exception:
                pass
            raise
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
            via = " (found by clicking a button, not a link)" if p.get("discovered_via") == "action_chain" else ""
            await self.emit(run_id, "EXPLORE", "explorer", "info", "log",
                            f"Mapped {p['url']}{via} -> status {p.get('status')}, "
                            f"{len(p.get('links', []))} links, {len(p.get('forms', []))} forms, "
                            f"{len(p.get('buttons', []))} buttons",
                            {"page": p})
            await asyncio.sleep(0.1)
        action_pages = [p for p in surface["pages"] if p.get("discovered_via") == "action_chain"]
        if action_pages:
            await self.emit(run_id, "EXPLORE", "explorer", "success", "log",
                            f"Action-chain discovery walked the primary call-to-action buttons "
                            f"(add-to-cart -> cart -> checkout -> continue) and found "
                            f"{len(action_pages)} additional screen(s) a link-only crawl would have "
                            "missed entirely: " + ", ".join(urlparse(p["url"]).path or p["url"] for p in action_pages))
        if config.get("auth_mode") == "authenticated":
            auth = surface.get("auth") or {}
            if auth.get("ok"):
                await self.emit(run_id, "EXPLORE", "explorer", "success", "log",
                                f"Logged in at {auth.get('login_url')} and persisted real Playwright "
                                "storageState.json (cookies/session) for reuse across all tests.",
                                {"storage_state": surface.get("storage_state_path")})
            elif not surface.get("auth"):
                await self.emit(run_id, "EXPLORE", "explorer", "warn", "log",
                                "Credentials were provided but login was skipped (no login URL). "
                                "Set Login URL to the sign-in page, or leave username/password empty "
                                "for public-flow mode.")
            else:
                await self.emit(run_id, "EXPLORE", "explorer", "warn", "log",
                                f"Login attempt at {auth.get('login_url')} failed "
                                f"({auth.get('error') or 'no matching form found'}); continuing unauthenticated.",
                                {})
        await self.db.runs.update_one({"id": run_id}, {"$set": {"surface": surface}})
        await self.emit(run_id, "EXPLORE", "explorer", "success", "stage_complete",
                        f"Exploration complete: {len(surface['pages'])} pages, "
                        f"{len(surface['forms'])} forms discovered.", {"routes": surface["routes"]})
        await self._handoff(
            run_id, "EXPLORE", "explorer", "planner", "surface",
            f"{len(surface['pages'])} pages, {len(surface['forms'])} forms")
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
        used_fallback = False
        if not flows:
            flows = _fallback_flows(surface)
            used_fallback = True
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
        fb = " (fallback)" if used_fallback else ""
        await self._handoff(
            run_id, "PLAN", "planner", "evaluator", "flows",
            f"{len(flows)} flows{fb}")
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
        used_fallback = data is None
        if data is None:
            data = _fallback_evaluation(surface, flows, config.get("prd"))
            await self.emit(run_id, "EVALUATE", "evaluator", "info", "log",
                            "LLM audit unavailable; using heuristic coverage-gap analysis "
                            "(discovered-surface + PRD-keyword matching) so the audit stage never goes silent.")
        # structural guarantee, independent of whether the LLM audit ran or what it noticed: if
        # EXPLORE's action-chain walk found cart/checkout-style screens (only reachable via a button
        # click), the plan MUST exercise them — an "end-to-end" claim can't rest on the LLM
        # remembering to prioritize the business-critical flow among everything else it could pick.
        _ensure_action_chain_coverage(surface, flows, data)
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
        persist_eval = {k: v for k, v in data.items() if k != "_fallback"}
        await self.db.plans.update_one({"run_id": run_id}, {"$set": {"flows": flows, "evaluation": persist_eval}})
        await self.emit(run_id, "EVALUATE", "evaluator", "success", "stage_complete",
                        f"Audit complete: {len(data.get('coverage_gaps', []))} gaps, "
                        f"{len(added)} flows auto-added. Coverage hardened.", {"evaluation": persist_eval})
        if used_fallback:
            data["_fallback"] = True
        await self.set_stage(run_id, "EVALUATE", "done")
        return flows, data

    # ---- GENERATE ----
    async def _stage_generate(self, run_id, config, surface, flows, model):
        await self.set_stage(run_id, "GENERATE", "running")
        await self.emit(run_id, "GENERATE", "generator", "info", "stage_start",
                        f"Generator ({model}) writing Playwright specs with live selector validation...")
        known_selectors = _known_selectors(surface)
        specs = []
        used_fallback = False
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
            if data.get("code"):
                code = data["code"]
            else:
                code = _fallback_spec(f, config["url"])
                used_fallback = True
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
            if f.get("step_selectors"):
                # per-step candidates verified during EXPLORE's action-chain walk (see
                # _action_chain_flow_steps) — takes priority over the flow-wide `selectors` pool
                # above for whichever steps have one, since some targets (an icon-only cart link with
                # no visible text) can only ever be found this way, not by matching step wording.
                spec["step_selectors"] = f["step_selectors"]
            specs.append(spec)
            await self.db.test_specs.insert_one(dict(spec))
            spec.pop("_id", None)
            await self.emit(run_id, "GENERATE", "generator", "success", "spec",
                            f"Generated {filename} ({len([v for v in validated if v['status']=='verified'])}/{len(validated)} selectors verified)",
                            {"spec": spec})
            await asyncio.sleep(0.15)
        await self.emit(run_id, "GENERATE", "generator", "success", "stage_complete",
                        f"Generated {len(specs)} executable Playwright specs.")
        fb = " (fallback)" if used_fallback else ""
        await self._handoff(
            run_id, "GENERATE", "generator", "runner", "specs",
            f"{len(specs)} specs{fb}")
        await self.set_stage(run_id, "GENERATE", "done")
        return specs

    # ---- RUN ----
    async def _run_flows(self, run_id, config, surface, specs, workers=1, on_step=None, on_result=None):
        """Sole owner of the Playwright browser lifecycle for executing flows. Both the main RUN
        stage (batch, parallel, persisted) and HEAL's live-replay verification (a single patched
        spec, not persisted directly) call this instead of each launching their own browser — so
        launch config (headless, slow_mo) can never drift between a flow's first run and its
        healed re-run, which is exactly what happened when HEAL used to manage its own browser."""
        storage_state_path = surface.get("storage_state_path")
        executions = []
        workers = max(1, workers)
        sem = asyncio.Semaphore(workers)
        worker_slots = asyncio.Queue()
        for i in range(1, workers + 1):
            worker_slots.put_nowait(i)

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
                    if on_result:
                        await on_result(ex)
            finally:
                await browser.close()
        return executions

    async def _stage_run(self, run_id, config, surface, specs):
        await self.set_stage(run_id, "RUN", "running")
        workers = max(1, int(config.get("workers", 3)))
        await self.emit(run_id, "RUN", "runner", "info", "stage_start",
                        f"Runner executing {len(specs)} specs on real headless Chromium ({workers} parallel workers)...")

        async def on_step(spec, step_entry, total_steps):
            lvl = "info" if step_entry["ok"] else "warn"
            note = f" — {step_entry['note']}" if step_entry.get("note") else ""
            await self.emit(run_id, "RUN", "runner", lvl, "step",
                            f"[{spec['flow_name']}] step {step_entry['index']}/{total_steps}: "
                            f"{step_entry['description']}{note}",
                            {"flow_id": spec["flow_id"], "step": step_entry})

        async def on_result(ex):
            await self.db.executions.insert_one(dict(ex))
            ex.pop("_id", None)
            lvl = "success" if ex["status"] == "passed" else "error"
            await self.emit(run_id, "RUN", "runner", lvl, "exec_result",
                            f"[worker {ex['worker']}] {ex['flow_name']} -> {ex['status'].upper()} ({ex['duration']}s)",
                            {"execution": ex})

        executions = await self._run_flows(run_id, config, surface, specs, workers=workers,
                                            on_step=on_step, on_result=on_result)

        passed = sum(1 for e in executions if e["status"] == "passed")
        failed = len(executions) - passed
        await self.emit(run_id, "RUN", "runner", "info", "stage_complete",
                        f"Execution complete: {passed}/{len(executions)} passed, {failed} failed.")
        await self._handoff(
            run_id, "RUN", "runner", "healer", "executions",
            f"{len(executions)} executions, {passed} passed, {failed} failed")
        await self.set_stage(run_id, "RUN", "done")
        return executions

    # ---- HEAL ----
    async def _stage_heal(self, run_id, config, surface, executions, specs, model):
        await self.set_stage(run_id, "HEAL", "running")
        failures = [e for e in executions if e["status"] == "failed"]
        known = _known_selectors(surface)
        spec_by_flow = {s["flow_id"]: s for s in specs}
        await self.emit(run_id, "HEAL", "healer", "info", "stage_start",
                        f"Healer analyzing {len(failures)} failures (heuristic rules + {model} classification)...")
        actions = []
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
                # a proposed heal is only provisional until replayed for real, and HEAL is not the
                # one that owns a browser to do that — RUN (via `_run_flows`) is, exactly as it is
                # for every other flow execution in this pipeline. HEAL's job stops at diagnosing
                # and proposing; verifying the proposal is RUN's job, called back into here.
                spec = spec_by_flow.get(e["flow_id"])
                replay_ex, replay_err = None, None
                if spec and heal.get("new_selector"):
                    try:
                        patched_spec = dict(spec)
                        patched_spec["selectors"] = (
                            [{"selector": heal["new_selector"], "status": "healed"}]
                            + list(spec.get("selectors", [])))
                        replay_results = await self._run_flows(run_id, config, surface, [patched_spec], workers=1)
                        replay_ex = replay_results[0] if replay_results else None
                    except Exception as ex:
                        replay_err = str(ex)[:150]

                if replay_ex and replay_ex.get("status") == "passed":
                    action["healed"] = True
                    action["result"] = f"Re-located element, replayed the flow in a live browser: PASSED ({replay_ex['duration']}s)."
                    # keep the ORIGINAL failure evidence (screenshot/error) alongside the new passing
                    # one instead of overwriting it — "broken, then fixed, both provable" is the one
                    # piece of evidence that actually earns trust in a self-healing claim; a healed
                    # card with only a passing screenshot is just an assertion again.
                    await self.db.executions.update_one({"id": e["id"]}, {"$set": {
                        "final_status": "healed", "healed": True,
                        "artifacts": replay_ex.get("artifacts"), "duration": replay_ex.get("duration"),
                        "original_artifacts": e.get("artifacts"), "original_error": e.get("error")}})
                    # the live SSE event is the frontend's only source of truth mid-run (it doesn't
                    # re-poll the DB per execution) — carry the before/after evidence on the event
                    # itself, not just in Mongo, or the UI never sees it until the page is reloaded.
                    action["artifacts"] = replay_ex.get("artifacts")
                    action["original_artifacts"] = e.get("artifacts")
                    await self.emit(run_id, "HEAL", "healer", "success", "healer_action",
                                    f"HEALED {e['flow_name']}: {action['heal'].get('old_selector','selector')} -> "
                                    f"{action['heal'].get('new_selector','stable locator')} | live re-run PASSED "
                                    f"({replay_ex['duration']}s, conf {action['confidence']})", {"action": action})

                    # persist the verified fix into the exported spec itself — a heal that only
                    # lives in this run's in-memory replay would be re-discovered from scratch
                    # (and re-cost an LLM call) every future run, and anyone exporting the .spec.js
                    # today would walk straight into the same stale locator this heal just proved
                    # is broken.
                    old_sel, new_sel = heal.get("old_selector"), heal.get("new_selector")
                    if spec and new_sel:
                        code = spec.get("code") or ""
                        if old_sel and old_sel in code:
                            code = code.replace(old_sel, new_sel)
                        spec["code"] = code
                        spec["selectors"] = (
                            [{"selector": new_sel, "status": "healed"}]
                            + [s for s in spec.get("selectors", []) if s.get("selector") != old_sel])
                        spec.setdefault("healed_selectors", []).append({
                            "old_selector": old_sel, "new_selector": new_sel,
                            "confidence": action["confidence"], "healed_at": now_iso()})
                        await self.db.test_specs.update_one({"id": spec["id"]}, {"$set": {
                            "code": spec["code"], "selectors": spec["selectors"],
                            "healed_selectors": spec["healed_selectors"]}})
                        await self.emit(run_id, "HEAL", "healer", "success", "spec_healed",
                                        f"Spec {spec['filename']} updated in place: "
                                        f"{old_sel or 'stale locator'} -> {new_sel}. Export now reflects the "
                                        "verified fix, not the original guess.", {"spec": spec})
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

        await self.emit(run_id, "HEAL", "healer", "success", "stage_complete",
                        f"Healer done: {sum(1 for a in actions if a['decision']=='script')} healed, "
                        f"{sum(1 for a in actions if a['decision']=='defect')} defects, "
                        f"{sum(1 for a in actions if a['decision']=='review')} need review.")
        await self._handoff(
            run_id, "HEAL", "healer", "reporter", "healer_actions",
            f"{len(actions)} actions")
        await self.set_stage(run_id, "HEAL", "done")
        return actions

    # ---- REPORT ----
    async def _compute_flakiness_trend(self, config):
        """A flow that needs healing run after run against the same target is the 'smell, not a
        success' signal self-healing governance is built around — surface it explicitly rather than
        letting a rising heal rate hide inside a run-by-run green pass rate."""
        url = config.get("url")
        if not url:
            return []
        run_ids = [r["id"] for r in await self.db.runs.find({"url": url}, {"id": 1}).to_list(500)]
        if len(run_ids) < 2:
            return []
        actions = await self.db.healer_actions.find(
            {"run_id": {"$in": run_ids}}, {"_id": 0, "run_id": 1, "flow_name": 1, "healed": 1, "decision": 1}
        ).to_list(5000)
        by_flow = {}
        for a in actions:
            name = a.get("flow_name") or "unknown flow"
            d = by_flow.setdefault(name, {"flow_name": name, "heal_count": 0, "runs_seen": set()})
            d["runs_seen"].add(a["run_id"])
            if a.get("healed") or a.get("decision") == "script":
                d["heal_count"] += 1
        total_runs = len(run_ids)
        trend = [{"flow_name": d["flow_name"], "heal_count": d["heal_count"],
                  "runs_seen": len(d["runs_seen"]), "total_runs": total_runs,
                  "rate": round(100 * d["heal_count"] / total_runs)}
                 for d in by_flow.values() if d["heal_count"] > 0]
        trend.sort(key=lambda x: -x["heal_count"])
        return trend[:10]

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
        flakiness_trend = await self._compute_flakiness_trend(config)
        report = {"id": str(uuid.uuid4()), "run_id": run_id, "created_at": now_iso(),
                  "summary": {"total_flows": len(flows), "total_specs": len(specs), "total_executions": total,
                              "passed": passed, "healed": healed, "defects": len(defects), "needs_review": review,
                              "pass_rate": pass_rate, "untested_risk_index": risk_index,
                              "coverage_gaps": len(gaps), "prd_gaps": len(prd_gaps),
                              "flaky_flows": len(flakiness_trend)},
                  "defects": defects, "coverage_gaps": gaps, "prd_gaps": prd_gaps,
                  "healer_actions": actions, "risk_notes": (plan or {}).get("evaluation", {}).get("risk_notes", []),
                  "flows": flows, "executions": finals, "flakiness_trend": flakiness_trend}
        await self.db.reports.update_one({"run_id": run_id}, {"$set": report}, upsert=True)
        report.pop("_id", None)
        await self.db.runs.update_one({"id": run_id}, {"$set": {"report_summary": report["summary"]}})
        await self.emit(run_id, "REPORT", "reporter", "success", "report",
                        f"Report ready: {pass_rate}% pass, {len(defects)} defects, risk index {risk_index}.",
                        {"report": report})
        await self._handoff(
            run_id, "REPORT", "reporter", "operator", "report",
            f"{pass_rate}% pass, {len(defects)} defects")
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
            # the identifier may actually be a plain `id`, a `data-testid`, or (e.g. saucedemo's
            # cart icon) a `data-test` attribute — offer all three literal forms as candidates so
            # whichever one GENERATE copies verbatim actually resolves against the live DOM instead
            # of only fuzzy-passing validation and then finding zero elements at RUN time.
            sels.append(f"[data-testid=\"{bs['selector']}\"]")
            sels.append(f"[data-test=\"{bs['selector']}\"]")
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


_ID_RE = re.compile(r"#([\w\-]+)")
_TESTID_RE = re.compile(r"data-testid=[\"']?([\w\-]+)")


def _selector_valid(sel, known):
    s = sel.lower()
    # an #id or [data-testid=...] is a specific, falsifiable claim about the DOM, so it must actually
    # match a real discovered identifier of the SAME kind — not just get waved through because the
    # selector also contains a generic tag-name word (e.g. "button#add_element_button" starts with
    # the literal word "button", which used to satisfy ROBUST_PATTERNS below regardless of whether
    # the id was ever seen on the real page), and not by matching a known id against a *different*
    # kind of claim (e.g. a hallucinated [data-testid="user-name"] "verifying" only because a real
    # #user-name id happens to share that identifier text — different attribute, still fabricated).
    testid_m = _TESTID_RE.search(sel)
    if testid_m:
        ident = testid_m.group(1).lower()
        return any(ident in k.lower() for k in known if "data-testid=" in k.lower())
    id_m = _ID_RE.search(sel)
    if id_m:
        ident = id_m.group(1).lower()
        return any(ident in k.lower() for k in known if k.lower().lstrip().startswith("#"))
    if any(k.lower() in s or s in k.lower() for k in known):
        return True
    return bool(ROBUST_PATTERNS.search(s))


_ACTION_CHAIN_STEP_LABELS = ["Add an item to the cart", "Open the cart", "Proceed to checkout",
                             "Continue past checkout details", "Finish and place the order"]


def _action_chain_flow_steps(surface):
    """Builds (steps, step_selectors) that exactly mirror what EXPLORE's action-chain walk actually
    clicked through (pw_engine._discover_action_chain's `hops`) — not generic natural-language steps
    like "Open the cart". Several of these controls (e.g. a cart icon identified only by a data-test
    attribute, with no visible text at all) can never be resolved by matching a step's own wording,
    and a flow-wide selector pool doesn't work either: a persistent nav element like the cart icon is
    visible on every subsequent page and would win — wrongly — for every later step too. step_selectors
    is aligned 1:1 with steps; an empty list at an index means "no click here" (a fill/assert step)."""
    hops = surface.get("action_chain_hops") or []
    steps, step_selectors = [], []
    for i, hop in enumerate(hops):
        label = (_ACTION_CHAIN_STEP_LABELS[i] if i < len(_ACTION_CHAIN_STEP_LABELS)
                 else f"Continue to the next step ({i + 1})")
        steps.append(label)
        step_selectors.append([hop["selector"]])
        if hop.get("has_form"):
            steps.append("Fill required checkout details")
            step_selectors.append([])
    steps.append("Assert order confirmation is shown")
    step_selectors.append([])
    return steps, step_selectors


_NAV_LIKE_TEXT_RE = re.compile(
    r"^(home|about|contact|blog|privacy|terms|sign ?in|log ?in|sign ?up|register|cart|search|menu)$", re.I)


def _primary_cta_candidate(pages):
    """Finds a real, clickable primary call-to-action on the homepage — actual text plus (where
    available) a real id/data-testid selector — instead of inventing a placeholder like "primary
    CTA" that can never resolve against a real DOM. Returns (text, selector), or None if EXPLORE
    didn't discover anything usable, so the caller can skip the flow rather than emit an
    unresolvable step."""
    home = pages[0] if pages else None
    if not home:
        return None
    for bs in home.get("button_selectors", []):
        text = (bs.get("text") or "").strip()
        if text and not _NAV_LIKE_TEXT_RE.match(text):
            sel = bs["selector"]
            return text, f'[data-testid="{sel}"], [data-test="{sel}"], #{sel}'
    for b in home.get("buttons", []):
        b = (b or "").strip()
        if b and not _NAV_LIKE_TEXT_RE.match(b):
            return b, f"getByRole('button', {{ name: /{re.escape(b)}/i }})"
    for l in home.get("links", []):
        text = (l.get("text") or "").strip()
        if text and not _NAV_LIKE_TEXT_RE.match(text):
            return text, f"getByRole('link', {{ name: /{re.escape(text)}/i }})"
    return None


def _fallback_flows(surface):
    """Deterministic plan used when the LLM is unavailable. Derived from what EXPLORE actually
    found (discovered pages/forms) rather than a fixed generic template, so an offline demo still
    visibly reflects the real target instead of always producing the same three canned flows."""
    pages = surface.get("pages", [])
    forms = surface.get("forms", [])
    action_pages = [p for p in pages if p.get("discovered_via") == "action_chain"]
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

    if action_pages:
        n = len(flows) + 1
        chain_paths = [urlparse(p["url"]).path or p["url"] for p in action_pages]
        steps, step_selectors = _action_chain_flow_steps(surface)
        flows.append({
            "flow_id": f"F{n}", "name": "Add item to cart and complete checkout", "type": "happy",
            "priority": "high", "steps": steps, "step_selectors": step_selectors,
            "expected_outcome": f"User can purchase an item end-to-end through {' -> '.join(chain_paths)}",
            "selectors": [],
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
        # no discovered forms — cover a primary interactive element as a happy path, but only if
        # EXPLORE actually found a real one. A vague, unresolvable step like "Click primary CTA"
        # is guaranteed to fail against any real page, so ground it in real discovered text/selector
        # or skip the flow entirely rather than emit a step that can never succeed.
        cta = _primary_cta_candidate(pages)
        if cta:
            text, selector = cta
            n = len(flows) + 1
            flows.append({
                "flow_id": f"F{n}", "name": f"Primary call-to-action: {text}", "type": "happy",
                "priority": "medium", "steps": [f"Click '{text}'", "Assert URL changed"],
                "expected_outcome": f"Clicking '{text}' navigates the user to the target page",
                "selectors": [selector],
            })

    n = len(flows) + 1
    flows.append({
        "flow_id": f"F{n}", "name": "404 handling for unknown route", "type": "edge", "priority": "low",
        "steps": ["Navigate to /nonexistent-xyz", "Assert 404 / not-found state"],
        "expected_outcome": "Graceful not-found page", "selectors": ["getByText('not found')"],
    })
    return flows


def _ensure_action_chain_coverage(surface, flows, data):
    """Mutates `data` in place: if EXPLORE's action-chain walk (pw_engine._discover_action_chain)
    found screens only reachable via a button click — the hallmark of a cart/checkout funnel — and
    no flow (existing or already queued by the LLM evaluator) exercises them, force one in. Runs
    unconditionally after either the LLM or heuristic audit, so "end-to-end" coverage never depends
    on the LLM happening to prioritize it among everything else it could plan."""
    action_pages = [p for p in surface.get("pages", []) if p.get("discovered_via") == "action_chain"]
    if not action_pages:
        return
    already_added = data.setdefault("added_flows", [])
    flow_text = " ".join((f.get("name", "") + " " + " ".join(f.get("steps", []) or []))
                         for f in list(flows) + list(already_added)).lower()
    if any(k in flow_text for k in ("cart", "checkout")):
        return
    chain_paths = [urlparse(p["url"]).path or p["url"] for p in action_pages]
    steps, step_selectors = _action_chain_flow_steps(surface)
    data.setdefault("coverage_gaps", []).append({
        "area": "Cart & checkout", "severity": "high",
        "detail": f"Exploration found {len(action_pages)} screen(s) only reachable by clicking a "
                  f"button (not a link) — {', '.join(chain_paths)} — but no flow in the plan "
                  "exercises the cart/checkout funnel."})
    already_added.append({
        "name": "Add item to cart and complete checkout", "type": "happy", "priority": "high",
        "steps": steps, "step_selectors": step_selectors,
        "expected_outcome": f"User can purchase an item end-to-end through {' -> '.join(chain_paths)}",
        "selectors": [],
    })


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


_SPEC_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_SPEC_STOPWORDS_RE = re.compile(
    r"^(click|select|choose|tap|press|enter|fill|type|assert|verify|check|navigate to|go to|visit|open|"
    r"the|a|an|and|then|on|to|into)\s+", re.I)


def _spec_keywords(step: str) -> str:
    """Extracts a short UI-label-shaped phrase for a getByRole/getByText name regex — e.g. "Click
    the 'Add to Cart' button for the item" -> "Add to Cart". Drops any parenthetical first (usually
    an example/aside like "(e.g., 'Sauce Labs Backpack')", not the real label) before even looking
    for a quoted literal, then cuts at the first comma and caps length — the raw remainder of a
    verbose step sentence makes an unmatchable regex rather than a real button/link name."""
    step = re.sub(r"\([^)]*\)", "", step)  # drop parentheticals before anything else
    m = _SPEC_QUOTED_RE.search(step)
    if m:
        lit = m.group(1) if m.group(1) is not None else m.group(2)
        if lit and lit.strip():
            return re.escape(lit.strip()[:40])
    t = step.split(",")[0]  # drop trailing aside
    t = re.sub(r"\b(button|link|field|element|page)\b", "", t, flags=re.I).strip()
    prev = None
    while prev != t:
        prev = t
        t = _SPEC_STOPWORDS_RE.sub("", t.strip())
    words = (t.strip() or step.strip()).split()[:4]  # keep it label-shaped, not a full sentence
    phrase = " ".join(words).strip(" -_.:;,")  # e.g. "login-" (hyphen left by stripping "button"
    return re.escape(phrase)[:40] or "submit"  # off "login-button") would fail to match a real "Login"


def _step_to_pw_line(step: str) -> str:
    """Best-effort translation of one plain-English plan step into a real, executable Playwright
    action — mirroring the same category rules pw_engine._execute_step uses live, so a fallback spec
    (used when the LLM's own code-gen call degrades) is a genuine starting point instead of a bare
    comment. The live Runner still drives the browser via its own interpreter (see the Runner tab for
    the actual resolved locators and results) — this is a portable, standalone approximation of that,
    not a byte-for-byte replay."""
    t = step.lower()
    m = _SPEC_QUOTED_RE.search(step)
    literal = (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))
    literal_js = literal.replace("\\", "\\\\").replace("'", "\\'") if literal else None
    kw = _spec_keywords(step)
    is_click_verb = t.strip().startswith(("click", "select", "choose", "tap", "press"))

    if not is_click_verb and any(k in t for k in ("navigate", "go to", "visit", "open")):
        path_m = re.search(r"(https?://\S+|/[\w\-/.]+)", step)
        target = path_m.group(1) if path_m else "/"
        return f"  await page.goto('{target}');"
    if not is_click_verb and "password" in t:
        val = literal_js or "Test-Password-1!"
        return f"  await page.locator('input[type=\"password\"]').first.fill('{val}');"
    if not is_click_verb and any(k in t for k in ("username", "user name", "email", "login")):
        val = literal_js or "testuser"
        return f"  await page.locator('input[type=\"text\"], input[type=\"email\"]').first.fill('{val}');"
    if not is_click_verb and any(k in t for k in ("fill", "enter", "type", "input")):
        val = literal_js or "test value"
        return f"  await page.locator('input:not([type=\"password\"]), textarea').first.fill('{val}');"
    if t.strip().startswith(("assert", "verify")) or (not is_click_verb and "assert" in t):
        if literal_js:
            return f"  await expect(page.getByText('{literal_js}', {{ exact: false }})).toBeVisible();"
        return f"  await expect(page.getByText(/{kw}/i).first).toBeVisible();"
    if is_click_verb or any(k in t for k in ("submit", "proceed", "continue", "checkout", "confirm", "add")):
        return (f"  await page.getByRole('button', {{ name: /{kw}/i }})"
                f".or(page.getByRole('link', {{ name: /{kw}/i }})).first.click();")
    return f"  // (informational step, no direct action)"


def _fallback_spec(flow, url):
    steps = flow.get("steps") or []
    lines = []
    goto_emitted = True  # the test body below always opens with an unconditional goto(url) already
    for s in steps:
        line = _step_to_pw_line(s)
        # skip a redundant repeat of the same initial navigation the test already opens with
        if line.strip() == f"await page.goto('{url}');" and goto_emitted:
            continue
        if line.strip().startswith("await page.goto("):
            goto_emitted = True
        lines.append(f"  // {s}\n{line}")
    body = "\n".join(lines)
    return (f"""import {{ test, expect }} from '@playwright/test';

// Auto-translated from the plan's natural-language steps (LLM code generation was unavailable for
// this flow). The live Runner drives the browser via its own step interpreter — see the Runner tab
// for the actual resolved locators, screenshots and pass/fail from the real run. This file is a
// portable, standalone approximation for your own suite, not a byte-for-byte replay of that run.
test('{flow['name']}', async ({{ page }}) => {{
  await page.goto('{url}');
{body}
}});
""")


def _default_rationale(ft, decision):
    if decision == "script":
        return f"{ft}: element still present in DOM but locator drifted; re-located against live DOM and patched spec."
    if decision == "defect":
        return f"{ft}: valid locator resolved but server/app behavior was wrong — strong genuine-defect signal."
    return f"{ft}: ambiguous signal; flagged for human review rather than asserting a defect."
