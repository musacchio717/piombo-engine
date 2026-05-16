from fastapi import APIRouter, Depends
from uuid import uuid4
from app.schemas.session import SessionCreateRequest, SessionCreateResponse, SessionResponse
from app.core.database import get_db

router = APIRouter(prefix="/api/session", tags=["session"])

@router.post("/new", response_model=SessionCreateResponse)
def create_session(req: SessionCreateRequest, db=Depends(get_db)):
    """Crea una nuova sessione + personaggio"""
    session_id = uuid4()
    character_id = uuid4()
    return {
        "session_id": session_id,
        "character_id": character_id,
        "character_name": req.character_name,
        "status": "active"
    }

@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, db=Depends(get_db)):
    """Stato sessione corrente"""
    return {
        "session_id": session_id,
        "character_name": "Test Character",
        "status": "active",
        "created_at": "2026-05-15T16:00:00"
    }

@router.delete("/{session_id}")
def delete_session(session_id: str, db=Depends(get_db)):
    """Termina sessione"""
    return {"message": "Session deleted", "session_id": session_id}