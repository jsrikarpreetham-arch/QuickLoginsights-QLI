from ingestion.parser import parse_log
from ingestion.repository import LogRepository

repo = LogRepository()


class IngestionService:

    async def ingest(self, raw_log):

        parsed = parse_log(raw_log)

        repo.save_log(parsed)

        return parsed