import os
import sys
import json
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')
sys.path.insert(0, str(ROOT_DIR))

from event_bus import bus
from orchestrator import Orchestrator, _resume_events, STAGES, defaultdict_seq
from report_export import build_html_report
from pw_engine import ARTIFACTS_ROOT

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="QAlchemist")
api_router = APIRouter(prefix="/api")
orch = Orchestrator(db)
_run_tasks: Dict[str, asyncio.Task] = {}

RUN_PUBLIC = {"_id": 0, "surface": 0, "config_secrets": 0}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class HealerResolution(BaseModel):
    resolution: str  # "defect" | "dismissed"
    note: Optional[str] = None


class RunConfig(BaseModel):
    url: str
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    prd: Optional[str] = None
    intent: Optional[str] = None
    budget: Optional[str] = "standard"
    workers: int = 3
    pause_after_plan: bool = False
    models: Dict[str, str] = Field(default_factory=dict)
    preset: Optional[str] = None


@api_router.get("/")
async def root():
    return {"message": "QAlchemist orchestration API", "stages": STAGES}


def _launch_run(run_id: str, config: dict):
    task = asyncio.create_task(orch.run(run_id, config))
    _run_tasks[run_id] = task

    def _clear(t, rid=run_id):
        _run_tasks.pop(rid, None)
    task.add_done_callback(_clear)
    return task


@api_router.post("/runs")
async def create_run(cfg: RunConfig):
    if not cfg.url or not cfg.url.startswith("http"):
        raise HTTPException(400, "A valid target URL (http/https) is required.")
    run_id = str(uuid.uuid4())
    auth_mode = "authenticated" if (cfg.username and cfg.password) else "public"
    config = cfg.model_dump()
    config["auth_mode"] = auth_mode
    # Sauce Demo and similar apps put the login form on the target URL. If creds
    # are present but Login URL was left blank, try the target page itself.
    if auth_mode == "authenticated" and not config.get("login_url"):
        config["login_url"] = cfg.url
    safe_config = {**config, "password": "***" if cfg.password else None}
    run_doc = {
        "id": run_id, "url": cfg.url, "status": "queued", "auth_mode": auth_mode,
        "config": safe_config, "created_at": now_iso(), "updated_at": now_iso(),
        "current_stage": "EXPLORE",
        "stages": {s: "pending" for s in STAGES},
    }
    if cfg.password:
        run_doc["config_secrets"] = {"password": cfg.password}
    await db.runs.insert_one(dict(run_doc))
    _launch_run(run_id, config)
    run_doc.pop("_id", None)
    run_doc.pop("config_secrets", None)
    return run_doc


@api_router.get("/runs")
async def list_runs():
    runs = await db.runs.find({}, RUN_PUBLIC).sort("created_at", -1).to_list(100)
    return runs


@api_router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await db.runs.find_one({"id": run_id}, RUN_PUBLIC)
    if not run:
        raise HTTPException(404, "Run not found")
    events = await db.events.find({"run_id": run_id}, {"_id": 0}).sort("seq", 1).to_list(5000)
    report = await db.reports.find_one({"run_id": run_id}, {"_id": 0})
    return {"run": run, "events": events, "report": report}


@api_router.get("/runs/{run_id}/events")
async def get_events(run_id: str, after_seq: int = 0):
    events = await db.events.find({"run_id": run_id, "seq": {"$gt": after_seq}},
                                  {"_id": 0}).sort("seq", 1).to_list(5000)
    run = await db.runs.find_one({"id": run_id}, RUN_PUBLIC)
    return {"events": events, "status": run.get("status") if run else "unknown"}


@api_router.post("/runs/{run_id}/abort")
async def abort_run(run_id: str):
    run = await db.runs.find_one({"id": run_id}, RUN_PUBLIC)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.get("status") not in ("queued", "running", "paused"):
        raise HTTPException(400, "Only queued, running, or paused runs can be aborted.")
    task = _run_tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        ev = _resume_events.get(run_id)
        if ev:
            ev.set()
    else:
        # process restart lost the in-memory task — mark terminal so the UI can recover
        await db.runs.update_one({"id": run_id}, {"$set": {
            "status": "aborted", "error": "aborted by operator", "finished_at": now_iso()}})
    return {"aborted": True}


@api_router.post("/runs/{run_id}/rerun")
async def rerun_run(run_id: str):
    """Clone a finished run's configuration into a new run (completed, failed, or aborted)."""
    source = await db.runs.find_one({"id": run_id}, {"_id": 0, "surface": 0})
    if not source:
        raise HTTPException(404, "Run not found")
    if source.get("status") in ("queued", "running", "paused"):
        raise HTTPException(400, "Abort or wait for the current run before rerunning.")
    cfg = dict(source.get("config") or {})
    secrets = source.get("config_secrets") or {}
    password = secrets.get("password")
    if cfg.get("password") == "***":
        cfg["password"] = password
    cfg.pop("auth_mode", None)
    try:
        parsed = RunConfig(**{k: v for k, v in ((k, cfg.get(k)) for k in RunConfig.model_fields) if v is not None})
    except Exception as e:
        raise HTTPException(400, f"Stored config cannot be rerun: {e}")
    return await create_run(parsed)


@api_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str):
    ev = _resume_events.get(run_id)
    if ev:
        ev.set()
        return {"resumed": True}
    raise HTTPException(400, "Run is not awaiting approval.")


@api_router.post("/runs/{run_id}/healer-actions/{action_id}/resolve")
async def resolve_healer_action(run_id: str, action_id: str, body: HealerResolution):
    """Human-in-the-loop resolution for a healer_action the pipeline left as NEEDS REVIEW — the
    Healer only auto-decides script-fix (verified by live replay) or genuine defect; anything
    ambiguous (an unclear assertion failure, or a proposed fix that didn't verify) is left for a
    person to call, and this is that call: escalate it to a real defect, or dismiss it as fine."""
    if body.resolution not in ("defect", "dismissed"):
        raise HTTPException(400, "resolution must be 'defect' or 'dismissed'")
    action = await db.healer_actions.find_one({"id": action_id, "run_id": run_id}, {"_id": 0})
    if not action:
        raise HTTPException(404, "Healer action not found")
    if action.get("decision") != "review":
        raise HTTPException(400, "Only actions still marked NEEDS REVIEW can be resolved")

    new_decision = "defect" if body.resolution == "defect" else "dismissed"
    await db.healer_actions.update_one({"id": action_id}, {"$set": {
        "decision": new_decision, "resolved_by": "operator", "resolved_note": body.note,
        "resolved_at": now_iso()}})

    final_status = "defect" if body.resolution == "defect" else "resolved"
    await db.executions.update_one({"id": action["execution_id"]}, {"$set": {"final_status": final_status}})

    if body.resolution == "defect":
        defect = {"id": str(uuid.uuid4()), "run_id": run_id, "flow_id": action["flow_id"],
                  "flow_name": action["flow_name"], "fail_type": action.get("fail_type"),
                  "confidence": 1.0, "severity": action.get("severity") or "medium",
                  "rationale": "Manually escalated by operator" + (f": {body.note}" if body.note else "")}
        await db.defects.insert_one(dict(defect))

    # recompute the persisted report's summary so the Report tab and HTML/JSON export stay
    # consistent with this decision, using the same formula _stage_report uses
    report = await db.reports.find_one({"run_id": run_id}, {"_id": 0})
    summary_patch, defects_list = {}, []
    if report:
        finals = await db.executions.find({"run_id": run_id}, {"_id": 0}).to_list(1000)
        total = len(finals)
        passed = sum(1 for e in finals if e["final_status"] == "passed")
        healed = sum(1 for e in finals if e["final_status"] == "healed")
        review = sum(1 for e in finals if e["final_status"] == "review")
        defects_list = await db.defects.find({"run_id": run_id}, {"_id": 0}).to_list(1000)
        pass_rate = round(100 * (passed + healed) / max(1, total))
        gaps = report.get("coverage_gaps", [])
        high_gaps = sum(1 for g in gaps if str(g.get("severity")).lower() == "high")
        risk_index = min(100, len(gaps) * 8 + high_gaps * 10 + len(defects_list) * 6 + review * 5)
        summary_patch = {"passed": passed, "healed": healed, "defects": len(defects_list),
                         "needs_review": review, "pass_rate": pass_rate, "untested_risk_index": risk_index}
        healer_actions = await db.healer_actions.find({"run_id": run_id}, {"_id": 0}).to_list(1000)
        await db.reports.update_one({"run_id": run_id}, {"$set": {
            **{f"summary.{k}": v for k, v in summary_patch.items()},
            "defects": defects_list, "healer_actions": healer_actions}})

    # continue the run's own event sequence (it was released when the run finished) so this new
    # event doesn't collide with the run's historical seq numbers
    last = await db.events.find({"run_id": run_id}, {"_id": 0, "seq": 1}).sort("seq", -1).limit(1).to_list(1)
    defaultdict_seq[run_id] = last[0]["seq"] if last else 0
    label = "APP DEFECT" if body.resolution == "defect" else "dismissed as a false positive"
    ev = await orch.emit(run_id, "HEAL", "healer", "error" if body.resolution == "defect" else "info",
                         "healer_action_resolved",
                         f"{action['flow_name']}: operator resolved NEEDS REVIEW -> {label}"
                         + (f" ({body.note})" if body.note else ""),
                         {"action_id": action_id, "flow_id": action["flow_id"], "execution_id": action["execution_id"],
                          "resolution": body.resolution, "summary_patch": summary_patch, "defects": defects_list})
    defaultdict_seq.pop(run_id, None)
    return ev


@api_router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, after_seq: int = 0):
    async def gen():
        existing = await db.events.find({"run_id": run_id, "seq": {"$gt": after_seq}},
                                        {"_id": 0}).sort("seq", 1).to_list(5000)
        last_seq = after_seq
        for e in existing:
            last_seq = e["seq"]
            yield f"data: {json.dumps(e)}\n\n"
        run = await db.runs.find_one({"id": run_id}, {"_id": 0})
        if run and run.get("status") in ("completed", "failed", "aborted"):
            yield "event: end\ndata: {}\n\n"
            return
        q = bus.subscribe(run_id)
        try:
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield f": heartbeat {last_seq}\n\n"
                    continue
                if e["seq"] <= last_seq:
                    continue
                last_seq = e["seq"]
                yield f"data: {json.dumps(e)}\n\n"
                if e["type"] == "run_complete":
                    yield "event: end\ndata: {}\n\n"
                    break
        finally:
            bus.unsubscribe(run_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@api_router.get("/runs/{run_id}/report")
async def get_report(run_id: str):
    report = await db.reports.find_one({"run_id": run_id}, {"_id": 0})
    if not report:
        raise HTTPException(404, "Report not ready")
    return report


@api_router.get("/runs/{run_id}/export")
async def export_report(run_id: str, request: Request, fmt: str = "json"):
    report = await db.reports.find_one({"run_id": run_id}, {"_id": 0})
    run = await db.runs.find_one({"id": run_id}, RUN_PUBLIC)
    if not report:
        raise HTTPException(404, "Report not ready")
    if fmt == "html":
        html = build_html_report(run, report, origin=str(request.base_url).rstrip("/"))
        return Response(content=html, media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="qalchemist-{run_id[:8]}.html"'})
    return Response(content=json.dumps(report, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="qalchemist-{run_id[:8]}.json"'})


app.include_router(api_router)
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_ROOT)), name="artifacts")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def reconcile_orphans():
    # A run mid-flight when ITS OWN process restarts can never resume (in-memory control lost)
    # -> mark failed. But `updated_at` is refreshed on every stage transition, so a run still being
    # actively driven by a different, still-alive process (e.g. another backend instance sharing
    # this DB) looks recent and must not be swept up just because *this* process is starting.
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    res = await db.runs.update_many(
        {"status": {"$in": ["running", "queued", "paused"]}, "updated_at": {"$lt": stale_cutoff}},
        {"$set": {"status": "failed", "error": "interrupted by backend restart"}})
    if res.modified_count:
        logger.info(f"Reconciled {res.modified_count} orphaned run(s) on startup.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
