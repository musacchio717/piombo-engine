"""
game_service.py — Logica di business per il flusso /game/action.

Responsabilità:
- Esegue il grafo LangGraph del narratore
- Parsea stat_change dall'output XML e aggiorna il personaggio
- Aggiorna game state (location se l'azione è un move)
- Salva GameEvent nel DB con metadata (token_count, latency)
"""

from __future__ import annotations
import logging
import re
import time
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.session import GameSession
from app.models.game_event import GameEvent
from app.ai.output_parser import NarratorOutput

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parsing stat_change
# ---------------------------------------------------------------------------

_STAT_RE = re.compile(
    r"(health|reputation|suspicion)\s*:\s*([+-]?\d+)",
    re.IGNORECASE,
)


def apply_stat_changes(character: Character, stat_change_str: str) -> dict:
    """
    Parsa la stringa stat_change e aggiorna i campi del personaggio.

    Formato atteso: "health: -10, suspicion: +5"
    Restituisce dict con i delta applicati (per metadata).
    """
    if not stat_change_str or stat_change_str.strip().lower() == "none":
        return {}

    applied: dict[str, int] = {}

    for match in _STAT_RE.finditer(stat_change_str):
        stat = match.group(1).lower()
        delta = int(match.group(2))

        if stat == "health":
            character.health = max(0, min(100, character.health + delta))
            applied["health"] = delta
        elif stat == "reputation":
            character.reputation = max(-100, min(100, character.reputation + delta))
            applied["reputation"] = delta
        elif stat == "suspicion":
            character.suspicion = max(0, min(100, character.suspicion + delta))
            applied["suspicion"] = delta

    return applied


def parse_location_from_action(action_str: str) -> str | None:
    """
    Estrae la nuova location da una stringa action tipo:
        "location_change: checkpoint_a1_sud"
    Restituisce None se l'azione non è un movimento.
    """
    if not action_str:
        return None
    match = re.match(r"location_change\s*:\s*(\S+)", action_str.strip(), re.IGNORECASE)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Game Service principale
# ---------------------------------------------------------------------------

class GameService:
    """
    Orchestratore del flusso /game/action.
    Riceve l'input del player, esegue il grafo narratore, aggiorna il DB.
    """

    def __init__(self, narrator_graph, db: Session) -> None:
        self._graph = narrator_graph
        self._db = db

    def process_action(
        self,
        session_id: UUID,
        player_input: str,
        system_prompt: str = "",
    ) -> dict:
        """
        Flusso principale:
        1. Carica sessione + personaggio
        2. Esegue narrator graph
        3. Aggiorna stats + location
        4. Salva GameEvent
        5. Restituisce risposta al frontend

        Returns:
            {
                "response": str,          # testo narrativo da mostrare al player
                "stats": dict,            # stats aggiornate
                "action": str,            # azione parsata
                "stat_delta": dict,       # delta applicati
                "errors": list[str],      # eventuali errori non fatali
                "latency_ms": int,
            }
        """
        t0 = time.monotonic()

        # --- 1. Carica stato ---
        game_session: GameSession | None = (
            self._db.query(GameSession).filter(GameSession.id == session_id).first()
        )
        if game_session is None:
            raise ValueError(f"Sessione {session_id} non trovata")

        character: Character | None = (
            self._db.query(Character).filter(Character.id == game_session.character_id).first()
        )
        if character is None:
            raise ValueError(f"Personaggio non trovato per sessione {session_id}")

        # --- 2. Esegui grafo narratore ---
        initial_state = {
            "player_input": player_input,
            "current_location_id": game_session.current_location_id or "starting_location",
            "character_stats": {
                "health":     character.health,
                "reputation": character.reputation,
                "suspicion":  character.suspicion,
            },
            "system_prompt": system_prompt or _DEFAULT_SYSTEM_PROMPT,
            "retrieval_context": "",
            "raw_llm_output": "",
            "narrator_output": None,
            "retry_count": 0,
            "errors": [],
        }

        final_state = self._graph.invoke(initial_state)
        narrator_out: NarratorOutput = final_state["narrator_output"]

        # --- 3. Aggiorna stats ---
        stat_delta = apply_stat_changes(character, narrator_out.stat_change)

        # --- 4. Aggiorna location se l'azione lo richiede ---
        new_location = parse_location_from_action(narrator_out.action)
        if new_location:
            game_session.current_location_id = new_location
            logger.info("location aggiornata: %s", new_location)

        # --- 5. Salva GameEvent ---
        latency_ms = int((time.monotonic() - t0) * 1000)

        event = GameEvent(
            session_id=session_id,
            event_type="narrator_response",
            content=narrator_out.response,
            event_extra_data={
                "action":       narrator_out.action,
                "stat_change":  narrator_out.stat_change,
                "stat_delta":   stat_delta,
                "latency_ms":   latency_ms,
                "retry_count":  final_state["retry_count"],
                "parse_errors": narrator_out.parse_errors,
                "think":        narrator_out.think,
            },
        )
        self._db.add(event)
        self._db.commit()

        # --- 6. Check condizioni di fine partita ---
        end_reason = None
        if character.health <= 0:
            end_reason = "death"
            game_session.status = "ended"
        elif character.suspicion >= 80:
            end_reason = "captured"
            game_session.status = "ended"

        if end_reason:
            self._db.commit()
            logger.info("partita terminata: %s", end_reason)

        return {
            "response":   narrator_out.response,
            "action":     narrator_out.action,
            "stat_delta": stat_delta,
            "stats": {
                "health":     character.health,
                "reputation": character.reputation,
                "suspicion":  character.suspicion,
            },
            "errors":     final_state["errors"],
            "latency_ms": latency_ms,
            "end_reason": end_reason,
        }


# ---------------------------------------------------------------------------
# System prompt di default (placeholder — verrà sostituito con la trama)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
Sei il narratore di un gioco testuale distopico ambientato in Italia nel 2020.
Una pandemia ha paralizzato il paese. Il protagonista è Alessandro Rullo,
capotreno bloccato a Pavia che vuole tornare dalla sua famiglia a Reggio Calabria.

Rispondi SEMPRE e SOLO con questo formato XML:
<think>ragionamento interno breve</think>
<action>none | location_change: <id> | item_use: <id></action>
<stat_change>none | health: ±N, suspicion: ±N, reputation: ±N</stat_change>
<response>testo narrativo in seconda persona, italiano, 3-6 frasi</response>
"""
