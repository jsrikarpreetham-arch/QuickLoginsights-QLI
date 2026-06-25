from pydantic import BaseModel, field_validator
from typing import Optional, Any
from datetime import datetime, timezone
import hashlib, json


class RawLogInput(BaseModel):
    source_id: str
    payload: dict[str, Any]

    def checksum(self) -> str:
        raw = json.dumps(self.payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


class ParsedLog(BaseModel):
    source_id: str
    raw_log_id: Optional[str] = None
    log_level: str = "INFO"
    service_name: Optional[str] = None
    host: Optional[str] = None
    message: str
    metadata: dict = {}
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    duration_ms: Optional[float] = None
    status_code: Optional[int] = None
    token_count: Optional[int] = None
    timestamp: datetime


class LogParser:
    LOG_LEVEL_MAP = {
        "debug": "DEBUG", "info": "INFO",
        "warn": "WARNING", "warning": "WARNING",
        "error": "ERROR", "critical": "CRITICAL",
        "fatal": "CRITICAL"
    }

    def parse(self, raw: RawLogInput) -> ParsedLog:
        p = raw.payload

        # Normalize log level
        level_raw = str(p.get("level", p.get("severity", "info"))).lower()
        log_level = self.LOG_LEVEL_MAP.get(level_raw, "INFO")

        # Normalize timestamp
        ts = p.get("timestamp", p.get("time", p.get("@timestamp")))
        try:
            timestamp = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            timestamp = datetime.now(timezone.utc)

        # Extract message
        message = p.get("message", p.get("msg", p.get("body", str(p))))

        # Build metadata (everything else)
        known_keys = {"level", "severity", "timestamp", "time", "@timestamp",
                      "message", "msg", "body", "service", "host",
                      "trace_id", "span_id", "duration_ms", "status_code",
                      "token_count"}
        metadata = {k: v for k, v in p.items() if k not in known_keys}

        return ParsedLog(
            source_id=raw.source_id,
            log_level=log_level,
            service_name=p.get("service"),
            host=p.get("host"),
            message=str(message),
            metadata=metadata,
            trace_id=p.get("trace_id"),
            span_id=p.get("span_id"),
            duration_ms=p.get("duration_ms"),
            status_code=p.get("status_code"),
            token_count=p.get("token_count"),
            timestamp=timestamp
        )