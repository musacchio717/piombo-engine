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
    core_context: str              # contesto fisso (storia + protagonista + gruppo)
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
    core = state.get("core_context", "")
    user_prompt = (
        f"{core}\n\n## Contesto lore dinamico\n{state['retrieval_context']}\n\n"
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


def node_check(state: NarratorState, checker: "ConsistencyChecker") -> dict:
    """
    Consistency check a tre livelli (HiPRAG + TeaRAG pattern):
      1. Format check — XML ben formato, tag obbligatori
      2. Entity check — nomi propri esistono nel KG
      3. Location check — location_change punta a nodo connesso
    """
    from app.ai.agents.consistency import ConsistencyChecker
    out: NarratorOutput | None = state["narrator_output"]

    if out is None or not out.is_valid:
        logger.warning("node_check: output non valido, retry_count=%d", state["retry_count"])
        return {"errors": ["output non valido da node_check"]}

    result = checker.check(
        output=out,
        current_location_id=state["current_location_id"],
    )

    if result.violations:
        logger.warning("node_check: violations=%s", result.violations)
    if result.warnings:
        logger.info("node_check: warnings=%s", result.warnings)

    errors = result.violations  # solo le violations bloccano
    if not result.is_consistent:
        return {"errors": errors, "narrator_output": None}  # forza retry

    logger.info("node_check: consistente ✓ (warnings=%d)", len(result.warnings))
    return {"errors": errors}  # warnings passano come errori non bloccanti


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


def node_semantic_check(state: NarratorState, semantic_checker: "SemanticChecker") -> dict:
    """
    Semantic grounding check (Fase 2 — LLM-as-Judge).
    Usa Qwen2.5-3B come judge per verificare che la response
    sia grounded nel contesto lore retrieved.

    Comportamento configurabile:
        - blocking=False (default): warning, non blocca
        - blocking=True: forza retry se non grounded
    """
    from app.ai.agents.semantic_checker import SemanticChecker
    out: NarratorOutput | None = state["narrator_output"]

    if out is None or not out.is_valid:
        return {}  # già gestito da node_finalize

    full_context = (state.get("core_context") or "") + "\n\n" + state["retrieval_context"]
    result = semantic_checker.check(
        retrieval_context=full_context,
        narrator_response=out.response,
    )

    if result.skipped:
        logger.info("node_semantic_check: skipped — %s", result.reason)
        return {}

    errors = []
    if not result.grounded:
        msg = f"semantic check: NOT grounded — {result.reason}"
        if semantic_checker.blocking:
            logger.warning("%s — forcing retry", msg)
            errors.append(msg)
            return {"errors": errors, "narrator_output": None}
        else:
            logger.warning("%s — soft warning", msg)
            errors.append(msg)

    return {"errors": errors}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_narrator_graph(
    retriever: HybridRetriever,
    llm: LLMClient | None = None,
    lore_graph=None,
    semantic_checker=None,
) -> StateGraph:
    """
    Costruisce e compila il grafo LangGraph del narratore.

    Grafo:
        retrieve → generate → check → finalize → semantic_check → END
                                 ↑         |
                                 └─────────┘ (retry se invalido)

    Args:
        retriever:        HybridRetriever già inizializzato
        llm:              client LLM da usare (default: MockLLM)
        lore_graph:       LoreGraph per ConsistencyChecker deterministico
        semantic_checker: SemanticChecker LLM-based (opzionale)
    """
    from app.ai.agents.consistency import ConsistencyChecker
    from app.ai.agents.semantic_checker import SemanticChecker

    if llm is None:
        llm = MockLLM()

    checker = ConsistencyChecker(lore_graph) if lore_graph else None

    if semantic_checker is None:
        semantic_checker = SemanticChecker()

    def _check_node(s):
        if checker:
            return node_check(s, checker)
        out = s["narrator_output"]
        if out is None or not out.is_valid:
            return {"errors": ["output non valido"]}
        return {}

    graph = StateGraph(NarratorState)

    # Nodi
    graph.add_node("retrieve",       lambda s: node_retrieve(s, retriever))
    graph.add_node("generate",       lambda s: node_generate(s, llm))
    graph.add_node("check",          _check_node)
    graph.add_node("retry",          node_retry)
    graph.add_node("finalize",       node_finalize)
    graph.add_node("semantic_check", lambda s: node_semantic_check(s, semantic_checker))

    # Edges
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "check")
    graph.add_conditional_edges("check", should_retry, {
        "retry":    "retry",
        "finalize": "finalize",
    })
    graph.add_edge("retry", "generate")
    graph.add_edge("finalize", "semantic_check")
    graph.add_edge("semantic_check", END)

    return graph.compile()
