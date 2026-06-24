import json
from datetime import datetime, timezone
from db.client import supabase
from detection.llm_analyzer import LLMAnalyzer
import logging

logger = logging.getLogger(__name__)

llm_analyzer = LLMAnalyzer()


class IncidentManager:
    """
    Creates, deduplicates, and enriches incidents.
    Orchestrates LLM analysis and links logs to incidents.
    """

    async def process(self, triggered: list[dict]):
        """
        Process a list of triggered detection results.
        Creates or updates incidents, runs LLM analysis.
        """
        for result in triggered:
            incident = await self._upsert_incident(result)
            if incident:
                await self._link_logs(incident["id"], result.get("log_ids", []))
                await self._run_llm_analysis(incident, result)

    async def _upsert_incident(self, result: dict) -> dict | None:
        """
        Dedup: if open incident with same rule_name exists,
        increment count + update last_seen_at.
        Otherwise create new incident.
        """
        rule_name = result.get("rule_name", "unknown")

        # Check for existing open incident
        existing = supabase.table("incidents") \
            .select("id, occurrence_count") \
            .eq("status", "open") \
            .ilike("title", f"%{rule_name}%") \
            .execute()

        if existing.data:
            # Update existing
            inc = existing.data[0]
            updated = supabase.table("incidents").update({
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "occurrence_count": inc["occurrence_count"] + 1
            }).eq("id", inc["id"]).execute()
            logger.info(f"Deduped incident {inc['id']} (count: {inc['occurrence_count'] + 1})")
            return updated.data[0] if updated.data else None

        # Create new incident
        title = self._build_title(result)
        new_inc = supabase.table("incidents").insert({
            "title": title,
            "description": result.get("sample_message", ""),
            "severity": result.get("severity", "medium"),
            "status": "open",
            "detection_method": result.get("detection_method", "rule"),
            "rule_id": result.get("rule_id"),
            "source_id": result.get("source_id"),
            "metadata": result.get("metadata", {})
        }).execute()

        if new_inc.data:
            logger.info(f"Created incident: {title}")
            return new_inc.data[0]
        return None

    async def _link_logs(self, incident_id: str, log_ids: list[str]):
        if not log_ids:
            return
        rows = [{"incident_id": incident_id, "log_id": lid} for lid in log_ids]
        supabase.table("incident_logs").upsert(rows).execute()

    async def _run_llm_analysis(self, incident: dict, result: dict):
        """
        Run Claude analysis only for high/critical incidents
        that don't already have root cause analysis.
        """
        if incident.get("root_cause_analysis"):
            return  # already analyzed

        if result.get("severity") not in ("high", "critical"):
            return  # skip low-severity for cost savings

        analysis = await llm_analyzer.analyze(result, result.get("log_ids", []))

        if analysis["parsed"]:
            # Store LLM analysis
            supabase.table("llm_analyses").insert({
                "incident_id": incident["id"],
                "prompt": analysis["prompt"][:5000],  # truncate for storage
                "response": analysis["response"],
                "model_used": analysis["model_used"],
                "tokens_used": analysis["tokens_used"]
            }).execute()

            # Update incident with root cause
            parsed = analysis["parsed"]
            supabase.table("incidents").update({
                "root_cause_analysis": parsed.get("root_cause", ""),
                "severity": parsed.get("severity_assessment", incident["severity"]),
                "metadata": {
                    **incident.get("metadata", {}),
                    "llm_analysis": parsed
                }
            }).eq("id", incident["id"]).execute()

            logger.info(f"LLM analysis complete for incident {incident['id']}")

    def _build_title(self, result: dict) -> str:
        method = result.get("detection_method", "")
        rule = result.get("rule_name", "unknown")
        svc = result.get("metadata", {}).get("service_name", "")
        base = f"[{method.upper()}] {rule.replace('_', ' ').title()}"
        return f"{base} - {svc}" if svc else base