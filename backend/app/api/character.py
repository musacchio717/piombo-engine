from fastapi import APIRouter, Depends
from app.schemas.character import CharacterResponse, CharacterStatsUpdate
from app.core.database import get_db

router = APIRouter(prefix="/api/character", tags=["character"])

@router.get("/{character_id}", response_model=CharacterResponse)
def get_character(character_id: str, db=Depends(get_db)):
    """Stats personaggio"""
    return {
        "id": character_id,
        "name": "Test Character",
        "health": 100,
        "reputation": 0,
        "suspicion": 0
    }

@router.patch("/{character_id}/stats")
def update_character_stats(character_id: str, stats: CharacterStatsUpdate, db=Depends(get_db)):
    """Update stats (interno)"""
    return {"character_id": character_id, "updated": stats.dict(exclude_unset=True)}