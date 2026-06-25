import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from ingestion.parser import RawLogInput, ParsedLog, LogParser
from db.client import get_pool
from config import BATCH_SIZE, BATCH_INTERVAL_MS

parser = LogParser()

class LogBatcher:
    def __init__(self):
        self._raw_queue: list[RawLogInput] = []
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self):
        self._running = True
        asyncio.create_task(self._flush_loop())

    async def stop(self):
        self._running = False
        await self._flush()

    async def add(self, log: RawLogInput):
        async with self._lock:
            self._raw_queue.append(log)
            if len(self._raw_queue) >= BATCH_SIZE:
                await self._flush()

    async def _flush_loop(self):
        interval = BATCH_INTERVAL_MS / 1000
        while self._running:
            await asyncio.sleep(interval)
            await self._flush()

    async def _flush(self):
        async with self._lock:
            if not self._raw_queue:
                return
            batch = self._raw_queue.copy()
            self._raw_queue.clear()

        pool = await get_pool()
        async with pool.acquire() as conn:
            await self._insert_raw(conn, batch)
            parsed_batch = [parser.parse(r) for r in batch]
            await self._insert_parsed(conn, parsed_batch)

    async def _insert_raw(self, conn: asyncpg.Connection, batch: list[RawLogInput]):
        rows = [
            (r.source_id, json.dumps(r.payload), r.checksum(), datetime.now(timezone.utc))
            for r in batch
        ]
        # Skip duplicates via ON CONFLICT
        await conn.executemany(
            """
            INSERT INTO raw_logs (source_id, raw_payload, checksum, received_at)
            VALUES ($1, $2::jsonb, $3, $4)
            ON CONFLICT (checksum) DO NOTHING
            """,
            rows
        )

    async def _insert_parsed(self, conn: asyncpg.Connection, batch: list[ParsedLog]):
        rows = [
            (
                p.source_id, p.log_level, p.service_name, p.host,
                p.message, json.dumps(p.metadata), p.trace_id,
                p.span_id, p.duration_ms, p.status_code,
                p.token_count, p.timestamp
            )
            for p in batch
        ]
        await conn.executemany(
            """
            INSERT INTO parsed_logs (
                source_id, log_level, service_name, host,
                message, metadata, trace_id, span_id,
                duration_ms, status_code, token_count, timestamp
            ) VALUES (
                $1, $2, $3, $4, $5, $6::jsonb, $7,
                $8, $9, $10, $11, $12
            )
            """,
            rows
        )

# Singleton
batcher = LogBatcher()