from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.schemas.game import ActionRequest, ActionResponse, GameStateResponse
from app.core.database import get_db
from app.models.session import GameSession
from app.models.character import Character
from app.models.game_event import GameEvent
from app.ai.llm_client import MockLLM
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.kcore import KCoreAnalyzer
from app.ai.graph.pagerank import PersonalizedPageRank
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.agents.narrator import build_narrator_graph
from app.services.game_service import GameService
from app.core.config import settings

router = APIRouter(prefix="/api/game", tags=["game"])


def _build_game_service(db: Session) -> GameService:
    """
    Factory del GameService con tutte le dipendenze iniettate.
    In futuro: sostituire MockLLM con OllamaLLM.
    """
    lore_graph = LoreGraph(settings.NEO4J_URI, settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    qdrant     = QdrantIngestor(settings.QDRANT_URL, settings.EMBEDDING_MODEL, settings.EMBEDDING_DEVICE)
    kcore      = KCoreAnalyzer(lore_graph)
    ppr        = PersonalizedPageRank(lore_graph, kcore)
    retriever  = HybridRetriever(qdrant, ppr, lore_graph)
    graph      = build_narrator_graph(retriever, llm=MockLLM())
    return GameService(graph, db)


@router.post("/action", response_model=ActionResponse)
def player_action(req: ActionRequest, db: Session = Depends(get_db)):
    """Endpoint principale — azione del giocatore."""
    service = _build_game_service(db)
    try:
        result = service.process_action(
            session_id=req.session_id,
            player_input=req.action,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ActionResponse(
        session_id=req.session_id,
        narrative_response=result["response"],
        stat_updates=result["stats"],
        location_changed=result["action"].startswith("location_change"),
        new_location_id=(
            result["action"].split(":")[-1].strip()
            if result["action"].startswith("location_change") else None
        ),
    )


@router.get("/state/{session_id}", response_model=GameStateResponse)
def get_game_state(session_id: UUID, db: Session = Depends(get_db)):
    """Stato completo gioco."""
    game_session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not game_session:
        raise HTTPException(status_code=404, detail="Sessione non trovata")

    character = db.query(Character).filter(Character.id == game_session.character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="Personaggio non trovato")

    return GameStateResponse(
        session_id=session_id,
        character_name=character.name,
        health=character.health,
        reputation=character.reputation,
        suspicion=character.suspicion,
        current_location_id=game_session.current_location_id,
        current_quest_id=game_session.current_quest_id,
        inventory=character.inventory or [],
    )


@router.get("/history/{session_id}")
def get_game_history(session_id: UUID, db: Session = Depends(get_db)):
    """Log eventi sessione."""
    events = (
        db.query(GameEvent)
        .filter(GameEvent.session_id == session_id)
        .order_by(GameEvent.created_at)
        .all()
    )
    return {
        "session_id": session_id,
        "events": [
            {
                "id":         str(e.id),
                "event_type": e.event_type,
                "content":    e.content,
                "metadata":   e.event_extra_data,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }
