from db.client import supabase


class LogRepository:

    def save_log(self, data):
        return (
            supabase.table("logs")
            .insert(data)
            .execute()
        )