"""
`ai_analysis` and `chat_history` ORM models.

`ai_analysis` stores the OpenAI-generated root-cause analysis for a given
incident. `chat_history` stores the AI assistant's chat turns per user
(used both to render conversation history and as additional RAG context).
"""

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.mixins import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.incidents.models import Incident
    from app.auth.models import User


class AIAnalysis(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ai_analysis"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    solution: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    incident: Mapped["Incident"] = relationship("Incident", back_populates="ai_analyses")

    def __repr__(self) -> str:
        return f"<AIAnalysis id={self.id} incident_id={self.incident_id} confidence={self.confidence}>"


class ChatHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "chat_history"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="chat_history")

    def __repr__(self) -> str:
        return f"<ChatHistory id={self.id} user_id={self.user_id}>"
