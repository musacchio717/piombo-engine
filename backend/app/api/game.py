from fastapi import APIRouter, Depends
from app.schemas.game import ActionRequest, ActionResponse, GameStateResponse
from app.core.database import get_db

router = APIRouter(prefix="/api/game", tags=["game"])

@router.post("/action", response_model=ActionResponse)
def player_action(req: ActionRequest, db=Depends(get_db)):
    """Endpoint principale — azione del giocatore"""
    return {
        "session_id": req.session_id,
        "narrative_response": f"You attempt to: {req.action}. The narrator responds...",
        "stat_updates": {"health": 0, "reputation": 0, "suspicion": 0},
        "location_changed": False,
        "new_location_id": None
    }

@router.get("/state/{session_id}", response_model=GameStateResponse)
def get_game_state(session_id: str, db=Depends(get_db)):
    """Stato completo gioco"""
    return {
        "session_id": session_id,
        "character_name": "Test",
        "health": 100,
        "reputation": 0,
        "suspicion": 0,
        "current_location_id": "starting_location",
        "current_quest_id": None,
        "inventory": []
    }

@router.get("/history/{session_id}")
def get_game_history(session_id: str, db=Depends(get_db)):
    """Log eventi sessione"""
    return {"session_id": session_id, "events": []}