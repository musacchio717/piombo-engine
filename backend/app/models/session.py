from sqlalchemy import Column, String, JSON, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from enum import Enum as PyEnum
from app.core.database import Base
from .base import UUIDMixin, TimestampedMixin

class SessionStatus(str, PyEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"

class GameSession(Base, UUIDMixin, TimestampedMixin):
    __tablename__ = "game_sessions"
    
    character_id = Column(UUID(as_uuid=True), ForeignKey("characters.id"), nullable=False)
    current_location_id = Column(String(255), default="starting_location")  # ID nodo Neo4j
    current_quest_id = Column(String(255), nullable=True)
    
    # Contesto narrativo (ultimi N eventi)
    narrative_context = Column(JSON, default=list)
    
    status = Column(Enum(SessionStatus), default=SessionStatus.ACTIVE)
    metadata = Column(JSON, default=dict)