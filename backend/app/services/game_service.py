"""
game_service.py — Logica di business per il flusso /game/action.
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
    if not action_str:
        return None
    match = re.match(r"location_change\s*:\s*(\S+)", action_str.strip(), re.IGNORECASE)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Game Service
# ---------------------------------------------------------------------------

class GameService:
    def __init__(self, narrator_graph, db: Session) -> None:
        self._graph = narrator_graph
        self._db = db

    def process_action(
        self,
        session_id: UUID,
        player_input: str,
        system_prompt: str = "",
    ) -> dict:
        t0 = time.monotonic()

        game_session = self._db.query(GameSession).filter(GameSession.id == session_id).first()
        if game_session is None:
            raise ValueError(f"Sessione {session_id} non trovata")

        character = self._db.query(Character).filter(Character.id == game_session.character_id).first()
        if character is None:
            raise ValueError(f"Personaggio non trovato per sessione {session_id}")

        initial_state = {
            "player_input": player_input,
            "current_location_id": game_session.current_location_id or "starting_location",
            "character_stats": {
                "health":     character.health,
                "reputation": character.reputation,
                "suspicion":  character.suspicion,
            },
            "system_prompt": system_prompt or _build_system_prompt(character),
            "retrieval_context": "",
            "raw_llm_output": "",
            "narrator_output": None,
            "retry_count": 0,
            "errors": [],
        }

        final_state = self._graph.invoke(initial_state)
        narrator_out: NarratorOutput = final_state["narrator_output"]

        stat_delta = apply_stat_changes(character, narrator_out.stat_change)

        new_location = parse_location_from_action(narrator_out.action)
        if new_location:
            game_session.current_location_id = new_location

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

        end_reason = None
        if character.health <= 0:
            end_reason = "death"
            game_session.status = "ended"
        elif character.suspicion >= 80:
            end_reason = "captured"
            game_session.status = "ended"

        if end_reason:
            self._db.commit()

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
# System prompt con few-shot examples
# ---------------------------------------------------------------------------

def _build_system_prompt(character: Character) -> str:
    """
    Costruisce il system prompt dinamicamente.
    Include few-shot examples per garantire aderenza al formato XML.
    Il contenuto narrativo (trama, personaggi) verrà aggiunto quando
    la trama sarà definita.
    """
    return f"""Sei il narratore di un gioco testuale distopico ambientato in Italia nel 2020.
Una pandemia ha paralizzato il paese. Il protagonista è {character.name},
bloccato in una città italiana che vuole lasciare.

REGOLE ASSOLUTE:
- Rispondi ESCLUSIVAMENTE con i 4 tag XML sotto. Zero testo prima o dopo.
- NON usare markdown, asterischi, grassetto o altri formati.
- NON aggiungere spiegazioni o commenti fuori dai tag.
- Usa SOLO entità e luoghi presenti nel CONTESTO LORE fornito. Non inventare nomi.
- Il testo narrativo è sempre in seconda persona singolare, italiano.

FORMATO OBBLIGATORIO:
<think>ragionamento interno: cosa sta succedendo, come rispondere</think>
<action>none</action>
<stat_change>none</stat_change>
<response>testo narrativo qui, 3-5 frasi, seconda persona</response>

Per <action> usa SOLO: none | location_change: <id_nodo> | item_use: <id_oggetto>
Per <stat_change> usa SOLO: none | health: ±N | suspicion: ±N | reputation: ±N

--- ESEMPI ---

Input: Busso alla porta del vicino per chiedere informazioni.
<think>Il giocatore vuole interagire con un vicino. Non ci sono PNG specifici menzionati nel contesto. Rispondo con una scena neutra che mantiene la tensione.</think>
<action>none</action>
<stat_change>none</stat_change>
<response>Bussi tre volte. Silenzio. Poi un rumore di passi cauti oltre il legno. "Chi è?" chiede una voce tesa. Nessuno si fida più dei vicini, in questi giorni.</response>

Input: Provo ad uscire dall'edificio.
<think>Il giocatore vuole spostarsi. Non è specificata una destinazione valida. Descrivo l'uscita mantenendo la tensione senza cambiare location.</think>
<action>none</action>
<stat_change>suspicion: +5</stat_change>
<response>Spingi il portone. L'aria fredda ti colpisce in faccia insieme alla luce accecante dei riflettori del checkpoint. Due soldati ti osservano dall'altro lato della strada. Meglio non muoversi in modo sospetto.</response>

--- FINE ESEMPI ---"""
