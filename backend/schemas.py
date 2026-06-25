from pydantic import BaseModel
from typing import Optional


class LogIn(BaseModel):
    level: str
    message: str
    service_name: str
    timestamp: Optional[str] = None