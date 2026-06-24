import asyncpg
import numpy as np  # type: ignore
from sklearn.ensemble import IsolationForest  # type: ignore
from sklearn.preprocessing import StandardScaler  # type: ignore
from datetime import datetime, timezone, timedelta
import logging

from db.client import get_pool

logger = logging.getLogger(__name__)

# Whitelist guard against SQL injection for dynamic metric columns
_ALLOWED_METRICS = {"duration_ms", "token_count", "status_code"}


class AnomalyDetector:
    """
    Two-layer anomaly detection, run independently per log source:
    1. Z-score            - fast, per-metric statistical check
    2. Isolation Forest    - multivariate ML anomaly detection

    Everything is scoped to a single source_id per check. Mixing
    sources together would let one app's traffic pattern (e.g. a
    naturally slow AI-model endpoint) skew the baseline for an
    unrelated app and produce false anomalies for both.
    """

    WINDOW_MINUTES = 30   # look-back window for anomaly check
    TRAIN_HOURS = 24      # historical window to train baseline
    MIN_BASELINE_SAMPLES = 30
    MIN_IFOREST_SAMPLES = 50

    async def _active_source_ids(self, conn: asyncpg.Connection) -> list[str]:
        rows = await conn.fetch(
            "SELECT id FROM log_sources WHERE is_active = TRUE"
        )
        return [str(r["id"]) for r in rows]

    async def run(self) -> list[dict]:
        """Run both detectors for every active source, return combined results."""
        results: list[dict] = []
        pool = await get_pool()
        async with pool.acquire() as conn:
            source_ids = await self._active_source_ids(conn)

        for source_id in source_ids:
            try:
                results += await self.run_zscore(source_id)
            except Exception as e:
                logger.error(f"Z-score detection failed for source {source_id}: {e}")
            try:
                results += await self.run_isolation_forest(source_id)
            except Exception as e:
                logger.error(f"Isolation forest failed for source {source_id}: {e}")

        return results

    # ── Z-SCORE CHECK ────────────────────────────────────────────────
    async def run_zscore(self, source_id: str) -> list[dict]:
        triggered = []
        pool = await get_pool()
        async with pool.acquire() as conn:
            for metric in _ALLOWED_METRICS:
                result = await self._zscore_check(conn, metric, source_id)
                if result:
                    triggered.append(result)
        return triggered

    async def _zscore_check(
        self, conn: asyncpg.Connection, metric: str, source_id: str
    ) -> dict | None:
        if metric not in _ALLOWED_METRICS:
            logger.warning(f"Rejected disallowed metric column: {metric!r}")
            return None

        train_since = datetime.now(timezone.utc) - timedelta(hours=self.TRAIN_HOURS)
        check_since = datetime.now(timezone.utc) - timedelta(minutes=self.WINDOW_MINUTES)

        # metric is whitelisted above so interpolation is safe here
        baseline_rows = await conn.fetch(
            f"""
            SELECT {metric}::float AS val
            FROM parsed_logs
            WHERE source_id = $1
              AND {metric} IS NOT NULL
              AND timestamp >= $2
              AND timestamp < $3
            """,
            source_id, train_since, check_since,
        )
        if len(baseline_rows) < self.MIN_BASELINE_SAMPLES:
            return None

        baseline_vals = np.array([r["val"] for r in baseline_rows])
        mean = float(np.mean(baseline_vals))
        std = float(np.std(baseline_vals, ddof=1))  # sample std, not population std

        if std == 0:
            return None

        recent_rows = await conn.fetch(
            f"""
            SELECT id, {metric}::float AS val, message,
                   service_name, host, timestamp, source_id
            FROM parsed_logs
            WHERE source_id = $1 AND {metric} IS NOT NULL AND timestamp >= $2
            ORDER BY timestamp DESC
            """,
            source_id, check_since,
        )
        if not recent_rows:
            return None

        recent_vals = np.array([r["val"] for r in recent_rows])
        recent_mean = float(np.mean(recent_vals))
        # comparing a sample mean against a population baseline - use
        # the standard error of the mean, not the raw std, so this
        # doesn't get more sensitive purely because more logs came in
        sem = std / (len(recent_vals) ** 0.5)
        if sem == 0:
            return None
        z_score = abs((recent_mean - mean) / sem)

        if z_score > 3.0:
            return {
                "rule_id": None,
                "rule_name": f"zscore_anomaly_{metric}",
                "severity": self._severity_from_zscore(z_score),
                "detection_method": "anomaly",
                "log_ids": [str(r["id"]) for r in recent_rows[:10]],
                "source_id": str(recent_rows[0]["source_id"]),
                "sample_message": recent_rows[0]["message"],
                "metadata": {
                    "metric": metric,
                    "z_score": round(z_score, 2),
                    "baseline_mean": round(mean, 2),
                    "baseline_std": round(std, 2),
                    "recent_mean": round(recent_mean, 2),
                    "sample_count": len(recent_rows),
                },
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            }

        return None

    def _severity_from_zscore(self, z: float) -> str:
        if z >= 6: return "critical"
        if z >= 5: return "high"
        if z >= 4: return "medium"
        return "low"

    # ── ISOLATION FOREST ─────────────────────────────────────────────
    async def run_isolation_forest(self, source_id: str) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            since = datetime.now(timezone.utc) - timedelta(hours=self.TRAIN_HOURS)
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    COALESCE(duration_ms, 0)   AS duration_ms,
                    COALESCE(token_count, 0)   AS token_count,
                    COALESCE(status_code, 200) AS status_code,
                    CASE log_level
                        WHEN 'DEBUG'    THEN 0
                        WHEN 'INFO'     THEN 1
                        WHEN 'WARNING'  THEN 2
                        WHEN 'ERROR'    THEN 3
                        WHEN 'CRITICAL' THEN 4
                        ELSE 1
                    END AS level_score,
                    message, service_name, host, timestamp, source_id
                FROM parsed_logs
                WHERE source_id = $1 AND timestamp >= $2
                ORDER BY timestamp DESC
                LIMIT 5000
                """,
                source_id, since,
            )

        if len(rows) < self.MIN_IFOREST_SAMPLES:
            logger.info(
                f"Not enough data for Isolation Forest on source {source_id} "
                f"(< {self.MIN_IFOREST_SAMPLES} samples)"
            )
            return []

        features = np.array([
            [r["duration_ms"], r["token_count"], r["status_code"], r["level_score"]]
            for r in rows
        ])

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(features)

        model = IsolationForest(
            contamination=0.02,
            n_estimators=100,
            random_state=42,
        )
        predictions = model.fit_predict(X_scaled)
        scores = model.decision_function(X_scaled)

        anomalies = [
            (row, score)
            for row, score, pred in zip(rows, scores, predictions)
            if pred == -1
        ]

        if not anomalies:
            return []

        logger.info(f"Isolation Forest detected {len(anomalies)} anomalies on source {source_id}")

        return [
            {
                "rule_id": None,
                "rule_name": "isolation_forest_anomaly",
                "severity": "high",
                "detection_method": "anomaly",
                "log_ids": [str(row["id"])],
                "source_id": str(row["source_id"]),
                "sample_message": row["message"],
                "metadata": {
                    "anomaly_score": round(float(score), 4),
                    "total_analyzed": len(rows),
                    "anomaly_count": len(anomalies),
                    "anomaly_rate": round(len(anomalies) / len(rows), 4),
                    "service_name": row["service_name"],
                    "host": row["host"],
                },
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            }
            for row, score in anomalies
        ]