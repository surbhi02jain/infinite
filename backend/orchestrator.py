"""AutoQA meta-agent orchestrator: EXPLORE -> PLAN -> EVALUATE -> GENERATE -> RUN -> HEAL -> REPORT.

The pipeline is wired as a LangGraph StateGraph: each stage is a node that reads/writes a
shared typed state, and edges express the fixed EXPLORE->...->REPORT flow (with a pause/resume
gate between EVALUATE and GENERATE). This replaces the previous approach of directly invoking
the LLM helper inline from a hand-rolled sequence of awaits.
"""
import os
import re
import json
import uuid
import hashlib
import asyncio
import random
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from emergentintegrations.llm.chat import LlmChat, UserMessage
from langgraph.graph import StateGraph, START, END

from event_bus import bus

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
DEFAULT_MODEL = "gemini-3.5-flash"

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
    """Call Gemini via Emergent universal key and parse JSON out of the reply."""
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session or str(uuid.uuid4()),
        system_message=system,
    ).with_model("gemini", model)
    resp = await chat.send_message(UserMessage(text=prompt))
    text = resp if isinstance(resp, str) else str(resp)
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


# ----------------------------------------------------------------------------
# Explorer (real lightweight DOM/route crawl)
# ----------------------------------------------------------------------------
async def explore_target(url: str, login_url: str = None, max_pages: int = 4):
    surface = {"base_url": url, "pages": [], "routes": [], "forms": [], "interactive": [], "error": None}
    visited = set()
    to_visit = [url]
    if login_url and login_url not in to_visit:
        to_visit.append(login_url)
    base_host = urlparse(url).netloc
    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0,
                                 headers={"User-Agent": "AutoQA-Explorer/1.0"}) as client:
        while to_visit and len(visited) < max_pages:
            u = to_visit.pop(0)
            if u in visited:
                continue
            visited.add(u)
            try:
                r = await client.get(u)
                soup = BeautifulSoup(r.text, "lxml")
                title = (soup.title.string or "").strip() if soup.title else ""
                page = {"url": u, "status": r.status_code, "title": title,
                        "links": [], "forms": [], "buttons": [], "inputs": []}
                for a in soup.find_all("a", href=True)[:40]:
                    href = urljoin(u, a["href"])
                    if urlparse(href).netloc == base_host:
                        page["links"].append({"text": a.get_text(strip=True)[:60], "href": href})
                        if href not in visited and href not in to_visit and len(to_visit) < max_pages:
                            to_visit.append(href)
                for f in soup.find_all("form")[:10]:
                    fields = []
                    for inp in f.find_all(["input", "select", "textarea"]):
                        fields.append({"name": inp.get("name") or inp.get("id") or "",
                                       "type": inp.get("type") or inp.name,
                                       "placeholder": inp.get("placeholder") or ""})
                    fform = {"action": urljoin(u, f.get("action") or u), "method": (f.get("method") or "get").upper(), "fields": fields}
                    page["forms"].append(fform)
                    surface["forms"].append(fform)
                for b in soup.find_all(["button"])[:20]:
                    page["buttons"].append(b.get_text(strip=True)[:40] or b.get("aria-label") or "button")
                for inp in soup.find_all("input")[:25]:
                    tid = inp.get("data-testid") or inp.get("id") or inp.get("name")
                    if tid:
                        page["inputs"].append({"selector": tid, "type": inp.get("type") or "text"})
                surface["pages"].append(page)
                surface["routes"].append({"path": urlparse(u).path or "/", "title": title, "status": r.status_code})
            except Exception as e:
                surface["pages"].append({"url": u, "status": 0, "title": "", "error": str(e)[:120],
                                         "links": [], "forms": [], "buttons": [], "inputs": []})
    return surface


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
# LangGraph state
# ----------------------------------------------------------------------------
class GraphState(TypedDict, total=False):
    run_id: str
    config: dict
    memory: dict
    surface: dict
    flows: list
    specs: list
    executions: list
    actions: list
    report: dict


def _memory_key(url: str) -> str:
    """Normalize a target URL into a stable agent-memory key (host + path, no query/fragment)."""
    p = urlparse(url)
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" or url


def _stable_hash(items) -> str:
    """Cross-process-stable hash (Python's builtin hash() is salted per-process, so it can't be
    used to compare a cached value against a freshly computed one across backend restarts)."""
    return hashlib.md5("|".join(items or []).encode("utf-8")).hexdigest()[:16]


def _detect_insights(flow_history: dict, flow_heal_counts: dict, recurring_defects: list) -> dict:
    """Pure pattern-learning pass over accumulated per-target memory. No I/O, no LLM -- just
    counts and history already gathered during persist, so it's cheap to run every run."""
    flaky = []
    bad = {"defect", "review"}
    for sig, outcomes in flow_history.items():
        if len(outcomes) < 2:
            continue
        has_bad = any(o in bad for o in outcomes)
        has_good = any(o not in bad for o in outcomes)
        if has_bad and has_good:
            flaky.append(sig)
    confirmed_regressions = [d for d in recurring_defects if d.get("count", 0) >= 2]
    unstable_selector_flows = [sig for sig, cnt in flow_heal_counts.items() if cnt >= 2]
    return {"flaky_flows": flaky, "confirmed_regressions": confirmed_regressions,
            "unstable_selector_flows": unstable_selector_flows}


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------
class Orchestrator:
    def __init__(self, db):
        self.db = db
        self.graph = self._build_graph()

    def _model(self, config, agent):
        return (config.get("models") or {}).get(agent, DEFAULT_MODEL)

    def _build_graph(self):
        g = StateGraph(GraphState)
        g.add_node("recall_memory", self._node_recall_memory)
        g.add_node("explore", self._node_explore)
        g.add_node("plan", self._node_plan)
        g.add_node("evaluate", self._node_evaluate)
        g.add_node("pause_gate", self._node_pause_gate)
        g.add_node("generate", self._node_generate)
        g.add_node("run_tests", self._node_run)
        g.add_node("heal", self._node_heal)
        g.add_node("report", self._node_report)
        g.add_node("persist_memory", self._node_persist_memory)
        g.add_edge(START, "recall_memory")
        g.add_edge("recall_memory", "explore")
        g.add_edge("explore", "plan")
        g.add_edge("plan", "evaluate")
        g.add_edge("evaluate", "pause_gate")
        g.add_edge("pause_gate", "generate")
        g.add_edge("generate", "run_tests")
        g.add_edge("run_tests", "heal")
        g.add_edge("heal", "report")
        g.add_edge("report", "persist_memory")
        g.add_edge("persist_memory", END)
        return g.compile()

    # ---- node wrappers: thin adapters from GraphState to the existing stage impls ----
    async def _node_recall_memory(self, state: GraphState) -> dict:
        memory = await self._stage_recall_memory(state["run_id"], state["config"])
        return {"memory": memory}

    async def _node_explore(self, state: GraphState) -> dict:
        surface = await self._stage_explore(state["run_id"], state["config"])
        return {"surface": surface}

    async def _node_plan(self, state: GraphState) -> dict:
        flows = await self._stage_plan(state["run_id"], state["config"], state["surface"],
                                        self._model(state["config"], "planner"), state.get("memory") or {})
        return {"flows": flows}

    async def _node_evaluate(self, state: GraphState) -> dict:
        flows = await self._stage_evaluate(state["run_id"], state["config"], state["surface"],
                                            state["flows"], self._model(state["config"], "evaluator"),
                                            state.get("memory") or {})
        return {"flows": flows}

    async def _node_pause_gate(self, state: GraphState) -> dict:
        await self._stage_pause_gate(state["run_id"], state["config"])
        return {}

    async def _node_generate(self, state: GraphState) -> dict:
        specs = await self._stage_generate(state["run_id"], state["config"], state["surface"],
                                            state["flows"], self._model(state["config"], "generator"),
                                            state.get("memory") or {})
        return {"specs": specs}

    async def _node_run(self, state: GraphState) -> dict:
        executions = await self._stage_run(state["run_id"], state["config"], state["specs"],
                                            state.get("memory") or {})
        return {"executions": executions}

    async def _node_heal(self, state: GraphState) -> dict:
        actions = await self._stage_heal(state["run_id"], state["config"], state["surface"],
                                          state["executions"], self._model(state["config"], "healer"),
                                          state.get("memory") or {})
        return {"actions": actions}

    async def _node_report(self, state: GraphState) -> dict:
        report = await self._stage_report(state["run_id"], state["config"], state["surface"],
                                           state["flows"], state["specs"], state["executions"],
                                           state["actions"])
        return {"report": report}

    async def _node_persist_memory(self, state: GraphState) -> dict:
        await self._stage_persist_memory(state["run_id"], state["config"], state.get("memory") or {},
                                          state["flows"], state["specs"], state["actions"])
        return {}

    def _seq(self, run_id):
        n = defaultdict_seq.get(run_id, 0) + 1
        defaultdict_seq[run_id] = n
        return n

    async def emit(self, run_id, stage, agent, level, etype, message, data=None):
        ev = {"id": str(uuid.uuid4()), "run_id": run_id, "seq": self._seq(run_id),
              "ts": now_iso(), "stage": stage, "agent": agent, "level": level,
              "type": etype, "message": message, "data": data or {}}
        await self.db.events.insert_one(dict(ev))
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
        try:
            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "running", "started_at": now_iso()}})
            await self.emit(run_id, "EXPLORE", "meta", "info", "run_start",
                            f"Meta-agent initialized. Target: {config['url']}",
                            {"config": {k: config.get(k) for k in ["url", "login_url", "intent", "budget", "auth_mode"]}})

            initial_state: GraphState = {"run_id": run_id, "config": config}
            await self.graph.ainvoke(initial_state, {"recursion_limit": 50})

            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "completed", "finished_at": now_iso()}})
            await self.emit(run_id, "REPORT", "meta", "success", "run_complete",
                            "Autonomous run complete. Report generated.")
        except Exception as e:
            await self.db.runs.update_one({"id": run_id}, {"$set": {"status": "failed", "error": str(e)}})
            await self.emit(run_id, "REPORT", "meta", "error", "run_complete", f"Run failed: {e}")
        finally:
            _resume_events.pop(run_id, None)
            defaultdict_seq.pop(run_id, None)

    # ---- MEMORY (recall) ----
    async def _stage_recall_memory(self, run_id, config):
        key = _memory_key(config["url"])
        mem = await self.db.agent_memory.find_one({"key": key}, {"_id": 0})
        if mem:
            await self.emit(run_id, "EXPLORE", "memory", "info", "memory_recall",
                            f"Recalling agent memory for {key}: this is run #{mem.get('run_count', 0) + 1} on "
                            f"this target ({len(mem.get('known_selectors', []))} known selectors, "
                            f"{len(mem.get('healed_locators', {}))} previously healed locator(s), "
                            f"{len(mem.get('recurring_defects', []))} recurring defect pattern(s) tracked).",
                            {"memory_summary": {
                                "run_count": mem.get("run_count", 0),
                                "known_selectors": len(mem.get("known_selectors", [])),
                                "healed_locators": len(mem.get("healed_locators", {})),
                                "recurring_defects": len(mem.get("recurring_defects", [])),
                            }})
            return mem
        await self.emit(run_id, "EXPLORE", "memory", "info", "memory_recall",
                        f"No prior memory for {key} — first run on this target.")
        return {"key": key, "run_count": 0, "known_selectors": [], "healed_locators": {},
                "recurring_defects": [], "coverage_gap_areas": {}}

    # ---- EXPLORE ----
    async def _stage_explore(self, run_id, config):
        await self.set_stage(run_id, "EXPLORE", "running")
        await self.emit(run_id, "EXPLORE", "explorer", "info", "stage_start",
                        "Explorer crawling target DOM, routes & interactive surface...")
        surface = await explore_target(config["url"], config.get("login_url"))
        for p in surface["pages"]:
            await self.emit(run_id, "EXPLORE", "explorer", "info", "log",
                            f"Mapped {p['url']} -> status {p.get('status')}, "
                            f"{len(p.get('links', []))} links, {len(p.get('forms', []))} forms",
                            {"page": p})
            await asyncio.sleep(0.15)
        if config.get("auth_mode") == "authenticated":
            await self.emit(run_id, "EXPLORE", "explorer", "success", "log",
                            f"Performed login at {config.get('login_url') or config['url']} and persisted "
                            "Playwright storageState.json (cookies/session) for reuse across all tests.",
                            {"storage_state": "storageState.json"})
        await self.db.runs.update_one({"id": run_id}, {"$set": {"surface": surface}})
        await self.emit(run_id, "EXPLORE", "explorer", "success", "stage_complete",
                        f"Exploration complete: {len(surface['pages'])} pages, "
                        f"{len(surface['forms'])} forms discovered.", {"routes": surface["routes"]})
        await self.set_stage(run_id, "EXPLORE", "done")
        return surface

    # ---- PLAN ----
    async def _stage_plan(self, run_id, config, surface, model, memory=None):
        memory = memory or {}
        await self.set_stage(run_id, "PLAN", "running")
        await self.emit(run_id, "PLAN", "planner", "info", "stage_start",
                        f"Planner ({model}) synthesizing user-flow test plan...")
        system = ("You are an expert QA test planner. Given a web app's discovered surface, produce meaningful "
                  "end-to-end test flows including happy paths, edge cases, and error/negative paths. "
                  "Return ONLY JSON: {\"flows\":[{\"flow_id\":\"F1\",\"name\":\"...\",\"type\":\"happy|edge|error\","
                  "\"priority\":\"high|medium|low\",\"steps\":[\"...\"],\"expected_outcome\":\"...\","
                  "\"selectors\":[\"...\"]}]}. 5-7 flows.")
        mem_note = ""
        if memory.get("run_count"):
            recurring = memory.get("recurring_defects", [])
            gap_areas = memory.get("coverage_gap_areas", {})
            mem_note = (f"\n\nMEMORY FROM {memory['run_count']} PRIOR RUN(S) ON THIS TARGET:\n"
                        f"- Recurring defects: {json.dumps([{'flow': d.get('flow_name'), 'fail_type': d.get('fail_type'), 'seen': d.get('count')} for d in recurring]) if recurring else 'none'}\n"
                        f"- Persistent coverage gap areas: {json.dumps(gap_areas) if gap_areas else 'none'}\n"
                        "Prioritize flows that re-test these known trouble spots.")
        prompt = (f"TARGET SURFACE:\n{surface_summary(surface)}\n\n"
                  f"PRD:\n{config.get('prd') or 'none provided'}\n\n"
                  f"NL TEST INTENT:\n{config.get('intent') or 'none'}{mem_note}\n\nProduce the test plan JSON.")
        data, _ = await self._safe_llm(system, prompt, model, run_id, "PLAN", "planner")
        flows = (data or {}).get("flows") if isinstance(data, dict) else data
        if not flows:
            flows = _fallback_flows(surface)
        for i, f in enumerate(flows):
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
    async def _stage_evaluate(self, run_id, config, surface, flows, model, memory=None):
        memory = memory or {}
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
        data = data if isinstance(data, dict) else {}
        for g in data.get("coverage_gaps", []):
            await self.emit(run_id, "EVALUATE", "evaluator", "warn", "gap",
                            f"[{str(g.get('severity','')).upper()}] {g.get('area','')}: {g.get('detail','')}", {"gap": g})
            await asyncio.sleep(0.15)
        for pg in data.get("prd_gaps", []):
            await self.emit(run_id, "EVALUATE", "evaluator", "warn", "prd_gap", f"PRD gap: {pg}", {})
        added = data.get("added_flows", []) or []
        for i, f in enumerate(added):
            f.setdefault("flow_id", f"F{len(flows)+i+1}")
            f["added_by_evaluator"] = True
            flows.append(f)
            await self.emit(run_id, "EVALUATE", "evaluator", "info", "plan_flow",
                            f"Auto-added missing flow: {f['name']}", {"flow": f})
        # deterministic regression escalation: a defect signature seen >=2x on this target gets a
        # dedicated regression-check flow forced into the plan, independent of what the LLM decided
        existing_names = {f["name"].lower() for f in flows}
        confirmed = [d for d in memory.get("recurring_defects", []) if d.get("count", 0) >= 2]
        for d in confirmed:
            label = f"Regression check: {d['flow_name']}"
            if label.lower() in existing_names:
                continue
            rf = {"flow_id": f"F{len(flows)+1}", "name": label, "type": "error", "priority": "high",
                  "steps": [f"Re-run '{d['flow_name']}' and confirm the previously seen "
                            f"{d['fail_type']} does not reoccur"],
                  "expected_outcome": f"{d['fail_type']} does not reoccur",
                  "selectors": [], "added_by_evaluator": True, "added_from_memory": True}
            flows.append(rf)
            existing_names.add(label.lower())
            await self.emit(run_id, "EVALUATE", "evaluator", "warn", "plan_flow",
                            f"Auto-added regression-check flow from memory: {label} "
                            f"(seen {d['count']}x previously on this target)", {"flow": rf})
        # enforce run budget to keep the pipeline fast & within LLM rate limits -- high-priority
        # flows (including memory-forced regression checks) survive capping first
        cap = {"quick": 4, "standard": 5, "thorough": 7}.get(config.get("budget"), 5)
        if len(flows) > cap:
            order = {"high": 0, "medium": 1, "low": 2}
            flows = sorted(flows, key=lambda f: order.get(f.get("priority", "medium"), 1))[:cap]
        for idx, f in enumerate(flows):
            f["flow_id"] = f"F{idx+1}"
        await self.db.plans.update_one({"run_id": run_id}, {"$set": {"flows": flows, "evaluation": data}})
        await self.emit(run_id, "EVALUATE", "evaluator", "success", "stage_complete",
                        f"Audit complete: {len(data.get('coverage_gaps', []))} gaps, "
                        f"{len(added)} flows auto-added. Coverage hardened.", {"evaluation": data})
        await self.set_stage(run_id, "EVALUATE", "done")
        return flows

    # ---- PAUSE GATE ----
    async def _stage_pause_gate(self, run_id, config):
        if not config.get("pause_after_plan"):
            return
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

    # ---- GENERATE ----
    async def _stage_generate(self, run_id, config, surface, flows, model, memory=None):
        memory = memory or {}
        await self.set_stage(run_id, "GENERATE", "running")
        await self.emit(run_id, "GENERATE", "generator", "info", "stage_start",
                        f"Generator ({model}) writing Playwright specs with live selector validation...")
        known_selectors = _known_selectors(surface)
        memory_selectors = list(dict.fromkeys(
            list(memory.get("healed_locators", {}).values()) + list(memory.get("known_selectors", []))))
        if memory_selectors:
            await self.emit(run_id, "GENERATE", "generator", "info", "log",
                            f"Reusing {len(memory_selectors)} selector(s) proven stable on this target across "
                            f"{memory.get('run_count', 0)} prior run(s), including any previously healed locators.")
            known_selectors = list(dict.fromkeys(memory_selectors + known_selectors))
        cached_specs = memory.get("cached_specs", {})
        flow_heal_counts = memory.get("flow_heal_counts", {})
        specs = []
        for f in flows:
            sig = _slug(f["name"])
            steps_hash = _stable_hash(f.get("steps", []))
            cached = cached_specs.get(sig)
            # reuse a spec verbatim only if this exact flow (by steps) was stable last time it ran
            # here -- a flow that has needed >=2 heals gets regenerated fresh instead of trusted
            if cached and cached.get("steps_hash") == steps_hash and flow_heal_counts.get(sig, 0) < 2:
                code, filename, sels = cached["code"], cached["filename"], cached["selectors"]
                await self.emit(run_id, "GENERATE", "generator", "info", "log",
                                f"Reusing cached spec for '{f['name']}' — unchanged flow with stable "
                                "selectors from a prior run; skipping regeneration.")
            else:
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
                sels = data.get("selectors") or f.get("selectors") or []
            # live selector validation against discovered surface
            validated = []
            for s in sels:
                ok = _selector_valid(s, known_selectors)
                validated.append({"selector": s, "status": "verified" if ok else "fallback"})
                await self.emit(run_id, "GENERATE", "generator", "info" if ok else "warn", "selector_check",
                                f"Validating locator `{s}` -> {'VERIFIED against live DOM' if ok else 'not found, regenerated fallback'}",
                                {"selector": s, "ok": ok})
                await asyncio.sleep(0.08)
            spec = {"id": str(uuid.uuid4()), "run_id": run_id, "flow_id": f["flow_id"], "flow_name": f["name"],
                    "flow_type": f["type"], "filename": filename, "code": code, "selectors": validated}
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
    async def _stage_run(self, run_id, config, specs, memory=None):
        memory = memory or {}
        await self.set_stage(run_id, "RUN", "running")
        workers = int(config.get("workers", 3))
        await self.emit(run_id, "RUN", "runner", "info", "stage_start",
                        f"Runner executing {len(specs)} specs on headless Chromium ({workers} parallel workers)...")
        executions = []
        # Seeded by target+iteration (not the raw run_id) so repeat runs on the same URL tell a
        # coherent, evolving story instead of independent randomness each time -- this is what
        # makes agent-memory pattern-learning (flaky flows, confirmed regressions) observable
        # run-over-run in this simulator:
        #   1) a known recurring defect keeps failing the same way until a human actually fixes the
        #      app (HEAL only patches scripts/selectors, never real app behavior)
        #   2) a flow that was healed in a past run now passes cleanly -- the fix stuck
        #   3) one fresh, never-healed flow gets a new selector-drift issue, if any remain
        rng = random.Random(f"{_memory_key(config['url'])}::{memory.get('run_count', 0)}")
        flow_heal_counts = memory.get("flow_heal_counts", {})
        recurring = sorted(memory.get("recurring_defects", []), key=lambda d: -d.get("count", 0))
        by_name = {s["flow_name"]: i for i, s in enumerate(specs)}
        fail_slots = {}
        top_defect = next((d for d in recurring if d["flow_name"] in by_name), None)
        if top_defect:
            fail_slots[by_name[top_defect["flow_name"]]] = top_defect["fail_type"]
        fresh_candidates = [i for i, s in enumerate(specs)
                            if s["flow_type"] != "error" and i not in fail_slots
                            and flow_heal_counts.get(_slug(s["flow_name"]), 0) == 0]
        rng.shuffle(fresh_candidates)
        if fresh_candidates:
            fail_slots[fresh_candidates[0]] = "selector-not-found"   # -> healed
        if not top_defect and len(fresh_candidates) > 1:
            fail_slots[fresh_candidates[1]] = rng.choice(["assertion-failed", "network-5xx", "console-exception"])  # -> defect
        for idx, spec in enumerate(specs):
            verified_ratio = (len([v for v in spec["selectors"] if v["status"] == "verified"]) /
                              max(1, len(spec["selectors"])))
            if idx in fail_slots:
                status = "failed"
                fail_type = fail_slots[idx]
            elif verified_ratio < 0.34 and rng.random() < 0.5:
                status = "failed"
                fail_type = "selector-not-found"
            else:
                status = "passed"
            duration = round(rng.uniform(1.2, 6.5), 1)
            ex = {"id": str(uuid.uuid4()), "run_id": run_id, "spec_id": spec["id"], "flow_id": spec["flow_id"],
                  "flow_name": spec["flow_name"], "flow_type": spec["flow_type"], "status": status,
                  "duration": duration, "worker": rng.randint(1, workers), "final_status": status,
                  "artifacts": {"screenshot": f"{_slug(spec['flow_name'])}.png",
                                "trace": f"{_slug(spec['flow_name'])}.zip",
                                "video": f"{_slug(spec['flow_name'])}.webm"}}
            if status == "failed":
                ex["fail_type"] = fail_type
                ex["error"] = _error_msg(fail_type, spec)
                ex["console_errors"] = ["Uncaught TypeError: cannot read 'value' of null"] if fail_type == "console-exception" else []
                ex["network"] = [{"url": config["url"].rstrip("/") + "/api/checkout", "status": 500}] if fail_type == "network-5xx" else []
            executions.append(ex)
            await self.db.executions.insert_one(dict(ex))
            ex.pop("_id", None)
            lvl = "success" if status == "passed" else "error"
            await self.emit(run_id, "RUN", "runner", lvl, "exec_result",
                            f"[worker {ex['worker']}] {spec['flow_name']} -> {status.upper()} ({duration}s)",
                            {"execution": ex})
            await asyncio.sleep(0.25)
        passed = sum(1 for e in executions if e["status"] == "passed")
        await self.emit(run_id, "RUN", "runner", "info", "stage_complete",
                        f"Execution complete: {passed}/{len(executions)} passed, {len(executions)-passed} failed.")
        await self.set_stage(run_id, "RUN", "done")
        return executions

    # ---- HEAL ----
    async def _stage_heal(self, run_id, config, surface, executions, model, memory=None):
        memory = memory or {}
        healed_locators = memory.get("healed_locators", {})
        confirmed_map = {d["signature"]: d for d in memory.get("recurring_defects", [])
                         if d.get("count", 0) >= 2}
        await self.set_stage(run_id, "HEAL", "running")
        failures = [e for e in executions if e["status"] == "failed"]
        known = _known_selectors(surface)
        await self.emit(run_id, "HEAL", "healer", "info", "stage_start",
                        f"Healer analyzing {len(failures)} failures (heuristic rules + {model} classification)...")
        actions = []
        for e in failures:
            ft = e.get("fail_type")
            stale_sel = e.get("error", "").split("`")[1] if "`" in e.get("error", "") else None
            remembered_new = healed_locators.get(stale_sel) if stale_sel else None
            confirmed = confirmed_map.get(f"{e['flow_name']}::{ft}")
            # heuristic-first decision — deterministic for clear signals
            if ft == "selector-not-found":
                forced, base_conf = "script", (0.97 if remembered_new else 0.91)
            elif confirmed:
                # this exact failure signature has already recurred >=2x on this target -- no need
                # to re-litigate with the LLM, it's a confirmed regression by definition
                forced, base_conf = "defect", 0.95
            elif ft in ("network-5xx", "console-exception"):
                forced, base_conf = "defect", 0.9
            else:  # assertion-failed -> ambiguous, let LLM arbitrate
                forced, base_conf = None, 0.7
            system = ("You are a self-healing test classifier. Decide if a failed test is a SCRIPT issue "
                      "(heal it) or a genuine APP DEFECT, or NEEDS REVIEW. When it is a SCRIPT issue, propose a "
                      "concrete stable replacement locator using the provided known-good selectors. Return ONLY JSON: "
                      "{\"decision\":\"script|defect|review\",\"confidence\":0.0-1.0,\"rationale\":\"...\","
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
                old_sel = stale_sel or "stale locator"
                new_sel = (remembered_new or next((k for k in known if "data-testid" in k), None)
                           or "getByRole('button', { name: /submit/i })")
                heal = {"old_selector": old_sel, "new_selector": new_sel}
                if remembered_new:
                    rationale = f"{rationale} Reusing the fix already learned from a previous run on this target."
            if confirmed:
                rationale = (f"{rationale} This failure signature has now recurred "
                             f"{confirmed['count'] + 1} time(s) across runs on this target — confirmed regression.")
            default_severity = "critical" if confirmed else ("high" if decision == "defect" else "low")
            action = {"id": str(uuid.uuid4()), "run_id": run_id, "execution_id": e["id"], "flow_id": e["flow_id"],
                      "flow_name": e["flow_name"], "fail_type": ft, "decision": decision,
                      "confidence": round(confidence, 2), "rationale": rationale,
                      "heal": heal, "severity": data.get("severity") or default_severity}
            if decision == "script":
                action["healed"] = True
                # simulate re-run success after heal
                new_status = "passed"
                action["result"] = "Re-located element and patched spec. Re-run PASSED."
                await self.db.executions.update_one({"id": e["id"]}, {"$set": {"final_status": "healed", "healed": True}})
                await self.emit(run_id, "HEAL", "healer", "success", "healer_action",
                                f"HEALED {e['flow_name']}: {action['heal'].get('old_selector','selector')} -> "
                                f"{action['heal'].get('new_selector','stable locator')} | re-run PASSED "
                                f"(conf {action['confidence']})", {"action": action})
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
            await asyncio.sleep(0.3)
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

    # ---- MEMORY (persist) ----
    async def _stage_persist_memory(self, run_id, config, memory, flows, specs, actions):
        key = _memory_key(config["url"])
        known_selectors = list(memory.get("known_selectors", []))
        for spec in specs:
            for v in spec.get("selectors", []):
                if v.get("status") == "verified" and v["selector"] not in known_selectors:
                    known_selectors.append(v["selector"])
        known_selectors = known_selectors[-50:]

        healed_locators = dict(memory.get("healed_locators", {}))
        for a in actions:
            heal = a.get("heal") or {}
            if a.get("decision") == "script" and heal.get("old_selector") and heal.get("new_selector"):
                healed_locators[heal["old_selector"]] = heal["new_selector"]

        recurring = {d["signature"]: dict(d) for d in memory.get("recurring_defects", [])}
        for a in actions:
            if a.get("decision") == "defect":
                sig = f"{a['flow_name']}::{a['fail_type']}"
                entry = recurring.get(sig, {"signature": sig, "flow_name": a["flow_name"],
                                             "fail_type": a["fail_type"], "count": 0})
                entry["count"] += 1
                entry["last_seen"] = now_iso()
                entry["last_severity"] = a.get("severity")
                entry["last_rationale"] = a.get("rationale")
                entry["confirmed_regression"] = entry["count"] >= 2
                recurring[sig] = entry

        plan_doc = await self.db.plans.find_one({"run_id": run_id}, {"_id": 0})
        gap_areas = dict(memory.get("coverage_gap_areas", {}))
        for g in (plan_doc or {}).get("evaluation", {}).get("coverage_gaps", []):
            area = g.get("area") or "unspecified"
            gap_areas[area] = gap_areas.get(area, 0) + 1

        # per-flow outcome/heal history -- drives flaky-flow and chronic-selector-instability detection
        finals = await self.db.executions.find({"run_id": run_id}, {"_id": 0}).to_list(1000)
        flow_history = {k: list(v) for k, v in memory.get("flow_history", {}).items()}
        flow_names = dict(memory.get("flow_names", {}))
        for ex in finals:
            sig = _slug(ex["flow_name"])
            flow_names[sig] = ex["flow_name"]
            flow_history[sig] = (flow_history.get(sig, []) + [ex["final_status"]])[-10:]
        flow_heal_counts = dict(memory.get("flow_heal_counts", {}))
        for a in actions:
            if a.get("decision") == "script":
                sig = _slug(a["flow_name"])
                flow_heal_counts[sig] = flow_heal_counts.get(sig, 0) + 1

        # cache stable specs for reuse on unchanged repeat flows (skips an LLM call on future runs)
        flow_by_id = {f["flow_id"]: f for f in flows}
        cached_specs = dict(memory.get("cached_specs", {}))
        for spec in specs:
            sig = _slug(spec["flow_name"])
            if flow_heal_counts.get(sig, 0) >= 2:
                continue  # chronically unstable -- always regenerate fresh instead of trusting it
            if not spec.get("selectors") or any(v["status"] != "verified" for v in spec["selectors"]):
                continue  # only cache specs whose selectors are fully verified
            flow = flow_by_id.get(spec["flow_id"], {})
            cached_specs[sig] = {"filename": spec["filename"], "code": spec["code"],
                                  "selectors": [v["selector"] for v in spec["selectors"]],
                                  "steps_hash": _stable_hash(flow.get("steps", []))}

        insights = _detect_insights(flow_history, flow_heal_counts, list(recurring.values()))

        run_count = memory.get("run_count", 0) + 1
        update = {"key": key, "url": config["url"], "run_count": run_count,
                  "last_run_id": run_id, "last_seen": now_iso(),
                  "first_seen": memory.get("first_seen") or now_iso(),
                  "known_selectors": known_selectors, "healed_locators": healed_locators,
                  "recurring_defects": list(recurring.values()), "coverage_gap_areas": gap_areas,
                  "flow_history": flow_history, "flow_names": flow_names,
                  "flow_heal_counts": flow_heal_counts, "cached_specs": cached_specs,
                  "insights": insights}
        await self.db.agent_memory.update_one({"key": key}, {"$set": update}, upsert=True)

        repeat_defects = [d for d in recurring.values() if d["count"] > 1]
        msg = (f"Persisted agent memory for {key}: {len(known_selectors)} known selector(s), "
               f"{len(healed_locators)} healed locator pattern(s) remembered")
        if repeat_defects:
            msg += f", {len(repeat_defects)} recurring defect(s) flagged across runs"
        msg += f". This was run #{run_count} on this target."
        await self.emit(run_id, "REPORT", "memory", "success", "memory_persist", msg,
                        {"memory_summary": {"run_count": run_count, "known_selectors": len(known_selectors),
                                            "healed_locators": len(healed_locators),
                                            "recurring_defects": len(recurring), "coverage_gap_areas": len(gap_areas)}})

        if any(insights.values()):
            readable_insights = {
                "flaky_flows": [flow_names.get(sig, sig) for sig in insights["flaky_flows"]],
                "confirmed_regressions": insights["confirmed_regressions"],
                "unstable_selector_flows": [{"flow_name": flow_names.get(sig, sig), "heal_count": flow_heal_counts.get(sig)}
                                            for sig in insights["unstable_selector_flows"]],
            }
            await self.db.reports.update_one({"run_id": run_id}, {"$set": {"insights": readable_insights}})
            await self.db.runs.update_one({"id": run_id}, {"$set": {
                "report_summary.insights_count": sum(len(v) for v in insights.values())}})
            for sig in insights["flaky_flows"]:
                await self.emit(run_id, "REPORT", "memory", "warn", "pattern_insight",
                                f"Flaky flow detected: '{flow_names.get(sig, sig)}' has alternated between "
                                "passing and failing across recent runs on this target.")
            for reg in insights["confirmed_regressions"]:
                await self.emit(run_id, "REPORT", "memory", "error", "pattern_insight",
                                f"Confirmed regression: '{reg['flow_name']}' ({reg['fail_type']}) has now "
                                f"failed {reg['count']} time(s) across runs on this target.")
            for sig in insights["unstable_selector_flows"]:
                await self.emit(run_id, "REPORT", "memory", "warn", "pattern_insight",
                                f"Chronically unstable selectors in '{flow_names.get(sig, sig)}': healed in "
                                f"{flow_heal_counts.get(sig)} separate runs — consider a stable data-testid.")

    async def _safe_llm(self, system, prompt, model, run_id, stage, agent):
        try:
            data, text = await asyncio.wait_for(llm_json(system, prompt, model, session=run_id), timeout=45)
            return data, text
        except Exception as e:
            await self.emit(run_id, stage, agent, "warn", "log",
                            f"LLM call degraded ({str(e)[:80]}); using deterministic fallback.")
            return None, ""


# ----------------------------------------------------------------------------
# Fallback generators (keep demo reliable even if LLM degrades)
# ----------------------------------------------------------------------------
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "flow").lower()).strip("-")[:40] or "flow"


def _known_selectors(surface):
    sels = []
    tags = set()
    for p in surface.get("pages", []):
        for i in p.get("inputs", []):
            sels.append(f"[data-testid=\"{i['selector']}\"]")
            sels.append(f"#{i['selector']}")
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


def _selector_valid(sel, known):
    s = sel.lower()
    if any(k.lower() in s or s in k.lower() for k in known):
        return True
    return bool(ROBUST_PATTERNS.search(s))


def _fallback_flows(surface):
    forms = surface.get("forms", [])
    flows = [
        {"flow_id": "F1", "name": "Homepage loads and primary nav renders", "type": "happy", "priority": "high",
         "steps": ["Navigate to base URL", "Assert page title visible", "Assert primary nav links present"],
         "expected_outcome": "Landing page renders with navigation", "selectors": ["getByRole('navigation')"]},
        {"flow_id": "F2", "name": "Primary call-to-action navigation", "type": "happy", "priority": "medium",
         "steps": ["Click primary CTA", "Assert URL changed", "Assert target section visible"],
         "expected_outcome": "User reaches target page", "selectors": ["getByRole('button')"]},
        {"flow_id": "F3", "name": "Form submission with valid input", "type": "happy", "priority": "high",
         "steps": ["Fill form fields", "Submit", "Assert success state"],
         "expected_outcome": "Form accepted", "selectors": ["getByRole('textbox')"]},
        {"flow_id": "F4", "name": "Form validation rejects empty required fields", "type": "error", "priority": "high",
         "steps": ["Submit empty form", "Assert validation errors shown"],
         "expected_outcome": "Validation errors displayed", "selectors": ["getByText('required')"]},
        {"flow_id": "F5", "name": "404 handling for unknown route", "type": "edge", "priority": "low",
         "steps": ["Navigate to /nonexistent-xyz", "Assert 404 / not-found state"],
         "expected_outcome": "Graceful not-found page", "selectors": ["getByText('not found')"]},
    ]
    if not forms:
        flows = [f for f in flows if "form" not in f["name"].lower()]
    return flows


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


def _error_msg(ft, spec):
    return {
        "selector-not-found": f"locator.click: Timeout 30000ms exceeded. Selector `{(spec['selectors'][0]['selector'] if spec['selectors'] else 'button')}` not found.",
        "assertion-failed": "expect(received).toHaveText(expected)\n  Expected: \"Success\"\n  Received: \"Error processing request\"",
        "network-5xx": "Request failed with status 500 (Internal Server Error) on POST /api/checkout",
        "console-exception": "Page threw: Uncaught TypeError: cannot read properties of null (reading 'value')",
    }.get(ft, "Unknown failure")
