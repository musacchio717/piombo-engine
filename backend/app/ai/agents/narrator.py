"""
narrator.py — Narrator Agent implementato con LangGraph.

Grafo:
    retrieve → generate → check → finalize
                             ↑         |
                             └─────────┘ (se inconsistente, retry una volta)

Lo stato del grafo (NarratorState) fluisce tra i nodi.
Il LLMClient è iniettato: MockLLM in dev, OllamaLLM in prod.
"""

from __future__ import annotations
import logging
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END

from app.ai.llm_client import LLMClient, MockLLM
from app.ai.output_parser import parse_narrator_output, NarratorOutput
from app.ai.retrieval.hybrid import HybridRetriever, HybridContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class NarratorState(TypedDict):
    # Input
    player_input: str
    current_location_id: str
    character_stats: dict          # {"health": 80, "reputation": 10, "suspicion": 20}
    system_prompt: str

    # Prodotto durante il flusso
    retrieval_context: str         # testo pronto per il prompt
    raw_llm_output: str
    narrator_output: NarratorOutput | None
    retry_count: int
    errors: Annotated[list[str], operator.add]


# ---------------------------------------------------------------------------
# Nodi
# ---------------------------------------------------------------------------

def node_retrieve(state: NarratorState, retriever: HybridRetriever) -> dict:
    """Esegue il retrieval ibrido e aggiorna il contesto nel state."""
    logger.info("node_retrieve: player_input=%r", state["player_input"][:60])

    context: HybridContext = retriever.retrieve(
        player_input=state["player_input"],
        current_location_id=state["current_location_id"],
    )
    prompt_text = context.to_prompt_text(max_chunks=8)

    logger.info(
        "node_retrieve: %d chunks, ~%d token stimati",
        len(context.chunks),
        context.total_tokens_estimate,
    )
    return {"retrieval_context": prompt_text}


def node_generate(state: NarratorState, llm: LLMClient) -> dict:
    """Chiama l'LLM e produce l'output strutturato XML."""
    logger.info("node_generate: chiamata LLM")

    stats = state["character_stats"]
    user_prompt = (
        f"## Contesto lore recuperato\n{state['retrieval_context']}\n\n"
        f"## Stato personaggio\n"
        f"- Salute: {stats.get('health', 100)}/100\n"
        f"- Reputazione: {stats.get('reputation', 0)}\n"
        f"- Sospetto: {stats.get('suspicion', 0)}/100\n\n"
        f"## Azione del giocatore\n{state['player_input']}"
    )

    raw = llm.generate(
        system_prompt=state["system_prompt"],
        user_prompt=user_prompt,
    )
    parsed = parse_narrator_output(raw)

    if parsed.parse_errors:
        logger.warning("node_generate: parse errors: %s", parsed.parse_errors)

    return {
        "raw_llm_output": raw,
        "narrator_output": parsed,
        "errors": parsed.parse_errors,
    }


def node_check(state: NarratorState) -> dict:
    """
    Consistency check leggero.
    Settimana 3: verifica minima (output valido + no contenuto vuoto).
    Settimana 4: potenziare con secondo agente che interroga Neo4j.
    """
    out: NarratorOutput | None = state["narrator_output"]

    if out is None or not out.is_valid:
        logger.warning("node_check: output non valido, retry_count=%d", state["retry_count"])
        return {"errors": ["output non valido da node_check"]}

    logger.info("node_check: output valido ✓")
    return {}


def should_retry(state: NarratorState) -> str:
    """
    Edge condizionale dopo node_check.
    Ritorna 'retry' se l'output non è valido e non abbiamo già riprovato.
    Ritorna 'finalize' altrimenti.
    """
    out: NarratorOutput | None = state["narrator_output"]
    if (out is None or not out.is_valid) and state["retry_count"] < 1:
        return "retry"
    return "finalize"


def node_retry(state: NarratorState) -> dict:
    """Incrementa il retry counter e pulisce l'output per rigenerare."""
    logger.info("node_retry: tentativo #%d", state["retry_count"] + 1)
    return {
        "retry_count": state["retry_count"] + 1,
        "narrator_output": None,
        "raw_llm_output": "",
    }


def node_finalize(state: NarratorState) -> dict:
    """
    Nodo finale: se l'output è ancora invalido dopo il retry,
    usa un fallback narrativo generico invece di rompere il flusso.
    """
    out: NarratorOutput | None = state["narrator_output"]
    if out is None or not out.is_valid:
        logger.error("node_finalize: output ancora invalido dopo retry — uso fallback")
        from app.ai.output_parser import NarratorOutput as NO
        fallback = NO(
            response="Il narratore esita. Il silenzio della quarantena è tutto intorno a te.",
            action="none",
            stat_change="none",
        )
        return {"narrator_output": fallback}
    return {}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_narrator_graph(
    retriever: HybridRetriever,
    llm: LLMClient | None = None,
) -> StateGraph:
    """
    Costruisce e compila il grafo LangGraph del narratore.

    Args:
        retriever: HybridRetriever già inizializzato
        llm:       client LLM da usare (default: MockLLM)
    """
    if llm is None:
        llm = MockLLM()

    graph = StateGraph(NarratorState)

    # Nodi (lambda per iniettare dipendenze senza classi)
    graph.add_node("retrieve",  lambda s: node_retrieve(s, retriever))
    graph.add_node("generate",  lambda s: node_generate(s, llm))
    graph.add_node("check",     node_check)
    graph.add_node("retry",     node_retry)
    graph.add_node("finalize",  node_finalize)

    # Edges
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "check")
    graph.add_conditional_edges("check", should_retry, {
        "retry":    "retry",
        "finalize": "finalize",
    })
    graph.add_edge("retry", "generate")
    graph.add_edge("finalize", END)

    return graph.compile()
