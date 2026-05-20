from sqlalchemy import Column, String, Integer, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from .base import UUIDMixin, TimestampedMixin

class Character(Base, UUIDMixin, TimestampedMixin):
    __tablename__ = "characters"
    
    session_id = Column(UUID(as_uuid=True), ForeignKey("game_sessions.id"), nullable=False)
    name = Column(String(255), nullable=False)
    
    # RPG Stats minimali
    health = Column(Integer, default=100)  # 0-100
    reputation = Column(Integer, default=0)  # -100/+100
    suspicion = Column(Integer, default=0)  # 0-100
    
    # JSON per futura espansione
    inventory = Column(JSON, default=list)
    metadata = Column(JSON, default=dict)  # campi futuri non previsti