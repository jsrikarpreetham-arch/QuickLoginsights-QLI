from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Any
from ingestion.parser import RawLogInput
from ingestion.batcher import batcher
from db.client import get_pool

app = FastAPI(title="AI Log Monitor - Ingestion API")


# ── Single log ingestion ──────────────────────────────
class LogRequest(BaseModel):
    source_id: str
    payload: dict[str, Any]

@app.post("/ingest/log", status_code=202)
async def ingest_single_log(req: LogRequest):
    raw = RawLogInput(source_id=req.source_id, payload=req.payload)
    await batcher.add(raw)
    return {"status": "queued"}


# ── Batch log ingestion ───────────────────────────────
class BatchLogRequest(BaseModel):
    source_id: str
    logs: list[dict[str, Any]]

@app.post("/ingest/batch", status_code=202)
async def ingest_batch(req: BatchLogRequest):
    if len(req.logs) > 1000:
        raise HTTPException(400, "Max 1000 logs per batch request")
    for payload in req.logs:
        raw = RawLogInput(source_id=req.source_id, payload=payload)
        await batcher.add(raw)
    return {"status": "queued", "count": len(req.logs)}


# ── Source management ─────────────────────────────────
class SourceRequest(BaseModel):
    name: str
    type: str   # application | server | api | ai_model
    config: dict = {}

@app.post("/sources", status_code=201)
def create_source(req: SourceRequest):
    result = get_pool.table("log_sources").insert({
        "name": req.name,
        "type": req.type,
        "config": req.config
    }).execute()
    return result.data[0]

@app.get("/sources")
def list_sources():
    result = get_pool.table("log_sources").select("*").execute()
    return result.data


# ── Health check ──────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}