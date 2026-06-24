from apscheduler.schedulers.asyncio import AsyncIOScheduler
from detection.rule_engine import RuleEngine
from detection.anomaly_detector import AnomalyDetector
from incident.manager import IncidentManager
import logging

logger = logging.getLogger(__name__)

rule_engine    = RuleEngine()
anomaly_detector = AnomalyDetector()
incident_manager = IncidentManager()
scheduler = AsyncIOScheduler()


async def run_rule_detection():
    logger.info("⚡ Running rule engine...")
    triggered = await rule_engine.run()
    if triggered:
        await incident_manager.process(triggered)
        logger.info(f"Rule engine → {len(triggered)} incident(s) created/updated")


async def run_anomaly_detection():
    logger.info("🔍 Running anomaly detector...")
    triggered = await anomaly_detector.run()
    if triggered:
        await incident_manager.process(triggered)
        logger.info(f"Anomaly detector → {len(triggered)} anomaly(ies) found")


def start_scheduler():
    scheduler.add_job(
        run_rule_detection,
        "interval",
        seconds=30,          # every 30 seconds
        id="rule_engine",
        replace_existing=True
    )
    scheduler.add_job(
        run_anomaly_detection,
        "interval",
        minutes=5,           # every 5 minutes
        id="anomaly_detector",
        replace_existing=True
    )
    scheduler.start()
    logger.info("✅ Scheduler started")