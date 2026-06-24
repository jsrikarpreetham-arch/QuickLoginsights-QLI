import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from ingestion.api import app as ingestion_app
from ingestion.batcher import batcher
from db.client import close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await batcher.start()
    print("✅ Batcher started")
    yield
    # Shutdown
    await batcher.stop()
    await close_pool()
    print("✅ Shutdown complete")

ingestion_app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(
        "ingestion.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )