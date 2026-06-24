import asyncpg
import json
from datetime import datetime, timezone, timedelta
from db.client import get_pool, supabase
import logging

logger = logging.getLogger(__name__)


class RuleEngine:
    """
    Evaluates detection rules against recent parsed_logs.
    Runs every 30 seconds via scheduler.
    """

    async def load_rules(self) -> list[dict]:
        result = supabase.table("detection_rules") \
            .select("*") \
            .eq("enabled", True) \
            .execute()
        return result.data

    async def run(self) -> list[dict]:
        """
        Main entry: evaluate all active rules.
        Returns list of triggered rule results.
        """
        rules = await self.load_rules()
        triggered = []

        pool = await get_pool()
        async with pool.acquire() as conn:
            for rule in rules:
                result = await self._evaluate(conn, rule)
                if result:
                    triggered.append(result)
                    logger.info(f"Rule triggered: {rule['name']}")

        return triggered

    async def _evaluate(self, conn: asyncpg.Connection, rule: dict) -> dict | None:
        rule_type = rule["rule_type"]
        condition = rule["condition"]

        if rule_type == "threshold":
            return await self._check_threshold(conn, rule, condition)
        elif rule_type == "rate":
            return await self._check_rate(conn, rule, condition)
        elif rule_type == "pattern":
            return await self._check_pattern(conn, rule, condition)
        return None

    # ── THRESHOLD: e.g. any CRITICAL log, or latency > Xms ──────────
    async def _check_threshold(
        self, conn: asyncpg.Connection, rule: dict, condition: dict
    ) -> dict | None:

        # Case 1: log level threshold
        if "log_level" in condition:
            since = datetime.now(timezone.utc) - timedelta(minutes=5)
            rows = await conn.fetch(
                """
                SELECT id, message, service_name, host, timestamp, source_id
                FROM parsed_logs
                WHERE log_level = $1 AND timestamp >= $2
                ORDER BY timestamp DESC
                LIMIT 10
                """,
                condition["log_level"], since
            )
            if len(rows) >= condition.get("count", 1):
                return self._build_result(rule, rows, {
                    "matched_count": len(rows),
                    "log_level": condition["log_level"]
                })

        # Case 2: metric threshold (duration_ms, status_code etc.)
        elif "metric" in condition and "source_type" not in condition:
            metric = condition["metric"]
            threshold = condition["threshold"]
            since = datetime.now(timezone.utc) - timedelta(minutes=5)
            rows = await conn.fetch(
                f"""
                SELECT id, message, service_name, host, timestamp,
                       source_id, {metric}
                FROM parsed_logs
                WHERE {metric} > $1 AND timestamp >= $2
                ORDER BY timestamp DESC
                LIMIT 10
                """,
                threshold, since
            )
            if rows:
                return self._build_result(rule, rows, {
                    "metric": metric,
                    "threshold": threshold,
                    "matched_count": len(rows)
                })

        # Case 3: API-specific metric threshold
        elif "metric" in condition and condition.get("source_type") == "api":
            metric = condition["metric"]
            threshold = condition["threshold"]
            since = datetime.now(timezone.utc) - timedelta(minutes=5)
            rows = await conn.fetch(
                f"""
                SELECT pl.id, pl.message, pl.service_name, pl.host,
                       pl.timestamp, pl.source_id, pl.{metric}
                FROM parsed_logs pl
                JOIN log_sources ls ON ls.id = pl.source_id
                WHERE ls.type = 'api'
                  AND pl.{metric} > $1
                  AND pl.timestamp >= $2
                ORDER BY pl.timestamp DESC
                LIMIT 10
                """,
                threshold, since
            )
            if rows:
                return self._build_result(rule, rows, {
                    "metric": metric,
                    "threshold": threshold,
                    "source_type": "api"
                })

        return None

    # ── RATE: e.g. error_rate > 5% in 5 min window ──────────────────
    async def _check_rate(
        self, conn: asyncpg.Connection, rule: dict, condition: dict
    ) -> dict | None:
        window = condition.get("window_minutes", 5)
        since = datetime.now(timezone.utc) - timedelta(minutes=window)

        # Error rate check
        if condition.get("metric") == "error_rate":
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE log_level IN ('ERROR','CRITICAL')) AS error_count,
                    COUNT(*) AS total_count
                FROM parsed_logs
                WHERE timestamp >= $1
                """,
                since
            )
            if row["total_count"] == 0:
                return None
            rate = row["error_count"] / row["total_count"]
            if rate >= condition["threshold"]:
                recent_errors = await conn.fetch(
                    """
                    SELECT id, message, service_name, host, timestamp, source_id
                    FROM parsed_logs
                    WHERE log_level IN ('ERROR','CRITICAL')
                      AND timestamp >= $1
                    ORDER BY timestamp DESC LIMIT 10
                    """,
                    since
                )
                return self._build_result(rule, recent_errors, {
                    "error_rate": round(rate, 4),
                    "error_count": row["error_count"],
                    "total_count": row["total_count"],
                    "window_minutes": window
                })

        # Token spike check (AI model logs)
        elif condition.get("metric") == "token_count":
            multiplier = condition.get("multiplier", 3.0)
            row = await conn.fetchrow(
                """
                SELECT
                    AVG(token_count) AS avg_tokens,
                    MAX(token_count) AS max_tokens
                FROM parsed_logs
                WHERE token_count IS NOT NULL
                  AND timestamp >= $1
                """,
                since
            )
            if row["avg_tokens"] and row["max_tokens"]:
                if row["max_tokens"] >= row["avg_tokens"] * multiplier:
                    spike_logs = await conn.fetch(
                        """
                        SELECT id, message, service_name, host,
                               timestamp, source_id, token_count
                        FROM parsed_logs
                        WHERE token_count >= $1 AND timestamp >= $2
                        ORDER BY token_count DESC LIMIT 10
                        """,
                        row["avg_tokens"] * multiplier, since
                    )
                    return self._build_result(rule, spike_logs, {
                        "avg_tokens": row["avg_tokens"],
                        "max_tokens": row["max_tokens"],
                        "spike_multiplier": multiplier
                    })

        return None

    # ── PATTERN: message text match ──────────────────────────────────
    async def _check_pattern(
        self, conn: asyncpg.Connection, rule: dict, condition: dict
    ) -> dict | None:
        pattern = condition.get("pattern", "")
        since = datetime.now(timezone.utc) - timedelta(minutes=5)
        rows = await conn.fetch(
            """
            SELECT id, message, service_name, host, timestamp, source_id
            FROM parsed_logs
            WHERE message ILIKE $1 AND timestamp >= $2
            ORDER BY timestamp DESC LIMIT 10
            """,
            f"%{pattern}%", since
        )
        if rows:
            return self._build_result(rule, rows, {"pattern": pattern})
        return None

    def _build_result(self, rule: dict, rows, extra: dict) -> dict:
        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "severity": rule["severity"],
            "detection_method": "rule",
            "log_ids": [str(r["id"]) for r in rows],
            "source_id": str(rows[0]["source_id"]) if rows else None,
            "sample_message": rows[0]["message"] if rows else "",
            "metadata": extra,
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }