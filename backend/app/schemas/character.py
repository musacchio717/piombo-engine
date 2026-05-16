from pydantic import BaseModel
from uuid import UUID
from typing import Optional

class CharacterResponse(BaseModel):
    id: UUID
    name: str
    health: int
    reputation: int
    suspicion: int

class CharacterStatsUpdate(BaseModel):
    health: Optional[int] = None
    reputation: Optional[int] = None
    suspicion: Optional[int] = None
