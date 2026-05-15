from sqlalchemy import Column, String, JSON, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from enum import Enum as PyEnum
from app.core.database import Base
from .base import UUIDMixin, TimestampedMixin

class QuestStatus(str, PyEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"

class Quest(Base, UUIDMixin, TimestampedMixin):
    __tablename__ = "quests"
    
    session_id = Column(UUID(as_uuid=True), ForeignKey("game_sessions.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(String(2048), nullable=False)
    status = Column(Enum(QuestStatus), default=QuestStatus.ACTIVE)
    
    objectives = Column(JSON, default=list)
    extra_metadata = Column(JSON, default=dict)
