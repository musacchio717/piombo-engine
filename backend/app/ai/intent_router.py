"""
intent_router.py — Classifica l'intento del player durante l'esplorazione.

Usa un LLM piccolo (CHECKER_MODEL, default Qwen2.5-3B) per capire cosa
il player vuole fare, senza richiedere che scriva esattamente il nome
dell'oggetto o dell'NPC.

Tipi di intento restituiti:
  examined     → esamina un oggetto specifico della scena
  dialog       → parla con un NPC specifico
  group_dialog → si rivolge al gruppo / a tutti
  flavor       → azione non riconoscibile, fuori scena, nonsense
"""

from __future__ import annotations
import json
import logging
import re

from app.ai.llm_client import LLMClient
from app.game.models import (
    BeatContext, ExplorationBeat, ExplorationResult,
    ExaminableObject,
)

logger = logging.getLogger(__name__)


ROUTER_SYSTEM = """\
Sei un classificatore di intenzioni per un gioco testuale.
Rispondi SOLO con un oggetto JSON valido su una riga. Niente altro.\
"""

ROUTER_PROMPT = """\
Scena: {scene_title}

Oggetti esaminabili:
{objects_list}

NPC con cui si può parlare:
{npcs_list}

Azione del giocatore: "{player_input}"

Regole:
- Se il player esamina/guarda/tocca/cerca un oggetto → intent "examine"
- Se il player parla/chiede/dice qualcosa a un NPC specifico → intent "dialog"
- Se il player parla al gruppo (ragazzi, tutti, voi, amici...) → intent "group_dialog"
- Tutto il resto (azioni impossibili, fuori contesto, nonsense) → intent "flavor"

Rispondi con UNO di questi JSON:
{{"intent": "examine",      "target_id": "<id_oggetto>"}}
{{"intent": "dialog",       "target_id": "<id_npc>"}}
{{"intent": "group_dialog"}}
{{"intent": "flavor"}}

JSON:\
"""


class IntentRouter:
    """Classifica l'intento di esplorazione tramite LLM."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def classify(
        self,
        player_text: str,
        ctx: BeatContext,
        exploration: ExplorationBeat,
    ) -> ExplorationResult:
        """
        Classifica l'input del player e restituisce un ExplorationResult
        con l'entità corretta già risolta.
        """
        objects_list = "\n".join(
            f"  id={obj.id}  nome='{obj.name}'"
            for obj in exploration.examinable
            if self._obj_available(obj, ctx)
        ) or "  (nessuno)"

        npcs_list = "\n".join(
            f"  id={npc.id}"
            for npc in exploration.dialogable_npcs
        ) or "  (nessuno)"

        prompt = ROUTER_PROMPT.format(
            scene_title=ctx.scene_title,
            objects_list=objects_list,
            npcs_list=npcs_list,
            player_input=player_text,
        )

        raw = self.llm.generate(ROUTER_SYSTEM, prompt)
        intent_data = self._parse_json(raw)

        logger.info(
            "IntentRouter: '%s...' → %s",
            player_text[:50], intent_data
        )

        return self._resolve(intent_data, player_text, ctx, exploration)

    # ------------------------------------------------------------------
    # Risoluzione intento → ExplorationResult
    # ------------------------------------------------------------------

    def _resolve(
        self,
        intent_data: dict,
        player_text: str,
        ctx: BeatContext,
        exploration: ExplorationBeat,
    ) -> ExplorationResult:
        intent = intent_data.get("intent", "flavor")
        target_id = intent_data.get("target_id", "")

        if intent == "examine":
            obj = next(
                (o for o in exploration.examinable
                 if o.id == target_id and self._obj_available(o, ctx)),
                None
            )
            if obj:
                return ExplorationResult(
                    type="examined",
                    object=obj,
                    player_query=player_text,
                )

        if intent == "dialog":
            npc = next(
                (n for n in exploration.dialogable_npcs if n.id == target_id),
                None
            )
            if npc:
                return ExplorationResult(
                    type="dialog",
                    npc=npc,
                    player_query=player_text,
                )

        if intent == "group_dialog":
            return ExplorationResult(
                type="group_dialog",
                player_query=player_text,
            )

        return ExplorationResult(
            type="flavor",
            player_query=player_text,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _obj_available(obj: ExaminableObject, ctx: BeatContext) -> bool:
        if obj.requires_flag:
            return ctx.game_state.flags.get(obj.requires_flag, False)
        return True

    @staticmethod
    def _parse_json(raw: str) -> dict:
        match = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("IntentRouter: JSON non parsabile: %s", raw[:120])
        return {"intent": "flavor"}