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
from orchestrator import Orchestrator, _resume_events, STAGES
from report_export import build_html_report
from pw_engine import ARTIFACTS_ROOT

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="QAlchemist")
api_router = APIRouter(prefix="/api")
orch = Orchestrator(db)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


@api_router.post("/runs")
async def create_run(cfg: RunConfig):
    if not cfg.url or not cfg.url.startswith("http"):
        raise HTTPException(400, "A valid target URL (http/https) is required.")
    run_id = str(uuid.uuid4())
    auth_mode = "authenticated" if (cfg.username and cfg.password) else "public"
    config = cfg.model_dump()
    config["auth_mode"] = auth_mode
    safe_config = {**config, "password": "***" if cfg.password else None}
    run_doc = {
        "id": run_id, "url": cfg.url, "status": "queued", "auth_mode": auth_mode,
        "config": safe_config, "created_at": now_iso(), "updated_at": now_iso(),
        "current_stage": "EXPLORE",
        "stages": {s: "pending" for s in STAGES},
    }
    await db.runs.insert_one(dict(run_doc))
    asyncio.create_task(orch.run(run_id, config))
    run_doc.pop("_id", None)
    return run_doc


@api_router.get("/runs")
async def list_runs():
    runs = await db.runs.find({}, {"_id": 0, "surface": 0}).sort("created_at", -1).to_list(100)
    return runs


@api_router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await db.runs.find_one({"id": run_id}, {"_id": 0, "surface": 0})
    if not run:
        raise HTTPException(404, "Run not found")
    events = await db.events.find({"run_id": run_id}, {"_id": 0}).sort("seq", 1).to_list(5000)
    report = await db.reports.find_one({"run_id": run_id}, {"_id": 0})
    return {"run": run, "events": events, "report": report}


@api_router.get("/runs/{run_id}/events")
async def get_events(run_id: str, after_seq: int = 0):
    events = await db.events.find({"run_id": run_id, "seq": {"$gt": after_seq}},
                                  {"_id": 0}).sort("seq", 1).to_list(5000)
    run = await db.runs.find_one({"id": run_id}, {"_id": 0, "surface": 0})
    return {"events": events, "status": run.get("status") if run else "unknown"}


@api_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str):
    ev = _resume_events.get(run_id)
    if ev:
        ev.set()
        return {"resumed": True}
    raise HTTPException(400, "Run is not awaiting approval.")


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
        if run and run.get("status") in ("completed", "failed"):
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
    run = await db.runs.find_one({"id": run_id}, {"_id": 0, "surface": 0})
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
