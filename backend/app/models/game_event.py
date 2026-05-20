from sqlalchemy import Column, String, JSON, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from enum import Enum as PyEnum
from app.core.database import Base
from .base import UUIDMixin, TimestampedMixin

class EventType(str, PyEnum):
    PLAYER_ACTION = "player_action"
    NARRATOR_RESPONSE = "narrator_response"
    QUEST_UPDATE = "quest_update"
    STAT_CHANGE = "stat_change"

class GameEvent(Base, UUIDMixin, TimestampedMixin):
    __tablename__ = "game_events"
    
    session_id = Column(UUID(as_uuid=True), ForeignKey("game_sessions.id"), nullable=False)
    event_type = Column(Enum(EventType), nullable=False)
    content = Column(Text, nullable=False)
    
    # Metadata osservabilità
    metadata = Column(JSON, default=dict)  # token_count, retrieval_source, latency_ms, etc.