import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.client import get_pool, close_pool

async def test_connection():
    print("⏳ Testing database connection...")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Test 1: Basic connectivity
            result = await conn.fetchval("SELECT 1")
            print(f"✅ Basic connection works! (SELECT 1 = {result})")

            # Test 2: Check your tables exist
            tables = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            print(f"✅ Tables found in DB:")
            for t in tables:
                print(f"   - {t['table_name']}")

            # Test 3: Check parsed_logs table specifically
            count = await conn.fetchval("SELECT COUNT(*) FROM parsed_logs")
            print(f"✅ parsed_logs has {count} rows")

    except Exception as e:
        print(f"❌ Connection failed: {e}")
    finally:
        await close_pool()
        print("🔒 Pool closed.")

asyncio.run(test_connection())