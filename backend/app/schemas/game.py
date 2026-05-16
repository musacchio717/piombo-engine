from pydantic import BaseModel
from uuid import UUID
from typing import Optional, List, Dict, Any

class ActionRequest(BaseModel):
    session_id: UUID
    action: str  # testo libero del giocatore

class ActionResponse(BaseModel):
    session_id: UUID
    narrative_response: str
    stat_updates: Dict[str, int]  # health, reputation, suspicion
    location_changed: bool
    new_location_id: Optional[str] = None

class GameStateResponse(BaseModel):
    session_id: UUID
    character_name: str
    health: int
    reputation: int
    suspicion: int
    current_location_id: str
    current_quest_id: Optional[str]
    inventory: List[str]