from fastapi import APIRouter, Depends
from app.core.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.post("/lore/ingest")
def ingest_lore(db=Depends(get_db)):
    """Ingesta seed lore nel knowledge graph"""
    return {"message": "Lore ingestion queued"}

@router.get("/graph/stats")
def graph_stats(db=Depends(get_db)):
    """Statistiche grafo"""
    return {
        "nodes": 0,
        "relationships": 0,
        "k_core_distribution": {}
    }

@router.get("/metrics")
def get_metrics(db=Depends(get_db)):
    """Token usage, latenza, etc."""
    return {
        "avg_tokens_per_response": 0,
        "avg_latency_ms": 0,
        "over_search_rate": 0.0
    }