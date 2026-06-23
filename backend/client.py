import asyncpg
from supabase import create_client, Client
from config import DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY

# Supabase client (for simple queries)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# asyncpg connection pool (for high-throughput ingestion)
_pool = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None