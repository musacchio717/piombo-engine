from pydantic import BaseModel
from uuid import UUID
from typing import Optional

class SessionCreateRequest(BaseModel):
    character_name: str

class SessionCreateResponse(BaseModel):
    session_id: UUID
    character_id: UUID
    character_name: str
    status: str

class SessionResponse(BaseModel):
    session_id: UUID
    character_name: str
    status: str
    created_at: str