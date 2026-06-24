from datetime import datetime, timezone, timedelta
from db.client import get_pool
import os
import uuid
import openai
import json
import logging

logger = logging.getLogger(__name__)

# ✅ FIX 1: Safely load API key from environment instead of importing from config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer) 
and AI systems analyst. Analyze log data and incidents concisely.
Always respond in this exact JSON structure:
{
  "root_cause": "...",
  "severity_assessment": "low|medium|high|critical",
  "confidence": 0.0-1.0,
  "affected_components": ["..."],
  "recommended_actions": ["...", "..."],
  "summary": "One sentence summary"
}"""

MODEL = "gpt-4o-2024-08-06"


class LLMAnalyzer:
    """
    Uses GPT-4o to perform deep root cause analysis
    on triggered incidents.
    """

    # ✅ FIX 2: Move client creation inside __init__ to avoid module-level async issues
    def __init__(self):
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        self.client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

    async def analyze(self, incident: dict, log_ids: list[str]) -> dict:
        """
        Fetch context logs and ask GPT-4o to analyze the incident.
        Returns analysis dict to be stored in llm_analyses.
        """
        logs = await self._fetch_log_context(log_ids)
        prompt = self._build_prompt(incident, logs)

        try:
            response = await self.client.chat.completions.create(  # ✅ use self.client
                model=MODEL,
                max_tokens=1000,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
            )

            raw_text = response.choices[0].message.content
            clean    = raw_text.replace("```json", "").replace("```", "").strip()

            # Parse separately so a bad JSON response doesn't mask API errors
            try:
                analysis = json.loads(clean)
            except json.JSONDecodeError as je:
                # ✅ FIX 3: Use %s style logging instead of f-strings (lazy eval, better performance)
                logger.warning("LLM returned non-JSON response: %s", je)
                analysis = None

            return {
                "prompt":      prompt,
                "response":    raw_text,
                "model_used":  MODEL,
                # ✅ FIX 4: Use getattr to safely handle None usage or None total_tokens
                "tokens_used": getattr(response.usage, "total_tokens", 0) or 0,
                "parsed":      analysis,
            }

        except openai.OpenAIError as e:
            # ✅ FIX 3: Use %s style logging instead of f-strings
            logger.error("LLM API call failed: %s", e)
            return {
                "prompt":      prompt,
                "response":    f"Analysis failed: {str(e)}",
                "model_used":  MODEL,
                "tokens_used": 0,
                "parsed":      None,
            }

    async def _fetch_log_context(self, log_ids: list[str]) -> list[dict]:
        if not log_ids:
            return []

        # ✅ FIX 5: Explicitly convert string IDs to UUID objects for safe asyncpg casting
        try:
            uuid_ids = [uuid.UUID(i) for i in log_ids]
        except ValueError as e:
            logger.error("Invalid UUID in log_ids: %s", e)
            return []

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT log_level, service_name, host, message,
                       duration_ms, status_code, token_count,
                       timestamp, metadata
                FROM parsed_logs
                WHERE id = ANY($1::uuid[])
                ORDER BY timestamp DESC
                """,
                uuid_ids,  # ✅ Pass converted UUID list
            )
        return [dict(r) for r in rows]

    def _build_prompt(self, incident: dict, logs: list[dict]) -> str:
        log_text = "\n".join([
            f"[{entry.get('timestamp', '')}] "
            f"{entry.get('log_level', 'INFO')} | "
            f"{entry.get('service_name', 'unknown')} | "
            f"{entry.get('host', 'unknown')} | "
            f"{entry.get('message', '')} | "
            f"duration_ms={entry.get('duration_ms')} "
            f"status={entry.get('status_code')} "
            f"tokens={entry.get('token_count')}"
            for entry in logs[:20]
        ])

        return f"""
INCIDENT DETECTED
=================
Name:     {incident.get('rule_name', 'unknown')}
Method:   {incident.get('detection_method', 'unknown')}
Severity: {incident.get('severity', 'unknown')}
Metadata: {incident.get('metadata', {})}

RELATED LOG ENTRIES (most recent first)
========================================
{log_text}

Analyze this incident. Identify the root cause, affected components,
and provide concrete recommended actions for the on-call engineer.
"""