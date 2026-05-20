"""
consistency.py — Consistency Checker deterministico (Fase 1).

Implementa due check ispirati a TeaRAG (knowledge matching) e HiPRAG (format check):
  1. Format Check — XML ben formato, tag obbligatori presenti
  2. Entity Check — nomi propri nella <response> esistono nel KG
  3. Location Check — location_change punta a nodo connesso alla location corrente

Nessun LLM richiesto: tutti i check sono deterministici e interrogano Neo4j.
Fase 2 (semantic grounding check LLM-based) da aggiungere dopo Ollama.
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field

from app.ai.output_parser import NarratorOutput
from app.ai.graph.lore_graph import LoreGraph

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyResult:
    """Risultato del consistency check."""
    is_consistent: bool = True
    format_ok: bool = True
    entity_ok: bool = True
    location_ok: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.is_consistent = False
        self.violations.append(reason)

    def warn(self, reason: str) -> None:
        self.warnings.append(reason)


class ConsistencyChecker:
    """
    Checker deterministico a tre livelli.
    Usato come nodo 'check' nel grafo LangGraph del narratore.
    """

    def __init__(self, lore_graph: LoreGraph) -> None:
        self._graph = lore_graph
        # Cache nomi entità (caricata lazy al primo check)
        self._entity_names: set[str] | None = None

    # ------------------------------------------------------------------
    # API pubblica
    # ------------------------------------------------------------------

    def check(
        self,
        output: NarratorOutput,
        current_location_id: str,
    ) -> ConsistencyResult:
        """
        Esegue tutti e tre i check in sequenza.
        Ritorna subito se il format check fallisce (niente da analizzare).
        """
        result = ConsistencyResult()

        # 1. Format check
        self._check_format(output, result)
        if not result.format_ok:
            result.is_consistent = False
            return result

        # 2. Entity check
        self._check_entities(output, result)

        # 3. Location check
        self._check_location(output, current_location_id, result)

        return result

    # ------------------------------------------------------------------
    # Check 1 — Format
    # Ispirato a HiPRAG Algorithm 2: nessun testo spurio, tag obbligatori
    # ------------------------------------------------------------------

    def _check_format(self, output: NarratorOutput, result: ConsistencyResult) -> None:
        if not output.response or not output.response.strip():
            result.format_ok = False
            result.fail("format: <response> vuoto o assente")
            return

        if not output.action:
            result.warn("format: <action> assente — usato default 'none'")

        if len(output.response.strip()) < 20:
            result.warn("format: <response> troppo corto (< 20 chars)")

        logger.debug("format check: OK")

    # ------------------------------------------------------------------
    # Check 2 — Entity (Knowledge Matching, TeaRAG pattern)
    # Estrae nomi propri dalla response e verifica che esistano nel KG.
    # ------------------------------------------------------------------

    def _check_entities(self, output: NarratorOutput, result: ConsistencyResult) -> None:
        known = self._get_entity_names()
        if not known:
            result.warn("entity check: KG vuoto o non raggiungibile — skip")
            return

        # Estrai token capitalizzati dalla response (euristica per nomi propri)
        candidates = _extract_proper_nouns(output.response)
        if not candidates:
            logger.debug("entity check: nessun nome proprio trovato — skip")
            return

        unknown = []
        for candidate in candidates:
            # Match parziale case-insensitive (es. "Marini" matcha "Tenente Marini")
            if not any(candidate.lower() in known_name.lower() for known_name in known):
                unknown.append(candidate)

        if unknown:
            # Warning, non hard failure: il narratore può usare termini generici
            result.warn(
                f"entity check: termini non trovati nel KG: {unknown} "
                f"— potrebbe essere inventato o termine generico"
            )
        else:
            logger.debug("entity check: OK (%d candidati verificati)", len(candidates))

    # ------------------------------------------------------------------
    # Check 3 — Location (graph connectivity)
    # Verifica che location_change punti a un nodo connesso alla location corrente.
    # ------------------------------------------------------------------

    def _check_location(
        self,
        output: NarratorOutput,
        current_location_id: str,
        result: ConsistencyResult,
    ) -> None:
        if not output.action or not output.action.startswith("location_change"):
            return  # nessun movimento → skip

        target = output.action.split(":")[-1].strip()
        if not target:
            result.fail("location check: location_change senza target id")
            result.location_ok = False
            return

        # Verifica che target esista nel KG
        target_node = self._graph.get_node(target)
        if target_node is None:
            result.fail(
                f"location check: '{target}' non esiste nel Knowledge Graph"
            )
            result.location_ok = False
            return

        # Verifica connessione CONNECTED_TO dalla location corrente
        if not self._are_connected(current_location_id, target):
            result.warn(
                f"location check: '{target}' non è direttamente connessa "
                f"a '{current_location_id}' via CONNECTED_TO — "
                f"movimento lungo distanza > 1 hop"
            )
            # Warning, non hard failure: il narratore potrebbe gestire
            # spostamenti multi-hop narrativamente
        else:
            logger.debug("location check: OK (%s → %s)", current_location_id, target)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_entity_names(self) -> set[str]:
        """Carica e cachea i nomi di tutte le entità dal KG."""
        if self._entity_names is not None:
            return self._entity_names
        try:
            nodes = self._graph.get_all_nodes_and_edges()["nodes"]
            self._entity_names = {
                n["name"] for n in nodes if "name" in n and n["name"]
            }
            logger.info("entity cache: %d nomi caricati", len(self._entity_names))
        except Exception as e:
            logger.error("entity cache: errore caricamento KG: %s", e)
            self._entity_names = set()
        return self._entity_names

    def _are_connected(self, from_id: str, to_id: str) -> bool:
        """Verifica se esiste CONNECTED_TO tra due location nel KG."""
        try:
            with self._graph._driver.session() as session:
                result = session.run(
                    "MATCH (a {id: $from_id})-[:CONNECTED_TO]-(b {id: $to_id}) "
                    "RETURN count(*) AS cnt LIMIT 1",
                    from_id=from_id,
                    to_id=to_id,
                )
                record = result.single()
                return bool(record and record["cnt"] > 0)
        except Exception as e:
            logger.error("location connectivity check error: %s", e)
            return True  # fail-open: non blocca il flusso


def _extract_proper_nouns(text: str) -> list[str]:
    """
    Euristica semplice: estrae parole con iniziale maiuscola che non siano
    a inizio frase. Funziona bene per nomi propri italiani.
    Non usa NER — non serve per 26 entità.
    """
    # Rimuovi punteggiatura e splitta
    words = re.findall(r'\b[A-Z][a-zàèéìòù]+(?:\s+[A-Z][a-zàèéìòù]+)*\b', text)
    # Filtra parole comuni maiuscole (inizio frase, "Il", "La", ecc.)
    stop = {"Il", "La", "Lo", "Le", "Gli", "Un", "Una", "Uno", "The", "Mock"}
    return [w for w in words if w not in stop and len(w) > 2]
