"""
beat_narrator.py — Rende prosa narrativa per ogni beat del SceneRunner.

Retrieval attivo solo per exploration (examined e dialog).
Arrival, event e decision usano solo core_context + contenuto autorato del JSON.
"""

from __future__ import annotations
import logging
import re

from app.ai.llm_client import LLMClient
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.context_builder import build_core_context
from app.ai.graph.lore_graph import LoreGraph
from app.game.models import BeatContext, ExplorationResult

logger = logging.getLogger(__name__)


BEAT_SYSTEM_PROMPT = """\
/no_think
Sei il narratore di Piombo Engine, un gioco testuale distopico ambientato \
nell'Italia di marzo 2020 in cui la pandemia è esplosa molto più gravemente del reale.

Tono: realistico, asciutto, mai eroico. Niente moralismo. \
Ironia solo quando il personaggio la userebbe.

Punto di vista: seconda persona singolare. "Tu" è Alessandro Rullo. \
Non usare mai la terza persona per lui.

REGOLE:
1. Descrivi solo ciò che il protagonista vede, sente, percepisce. \
NON decidere mai cosa fa o pensa — lo decide il giocatore.
2. Non inventare fatti che contraddicono il contesto.
3. Scrivi SOLO dentro <response>...</response>. Niente fuori.
4. Solo prosa dentro <response>: niente titoli, liste, intestazioni.
5. Le istruzioni di regia marcate PRIVATA non vanno nel testo.
6. Italiano corretto. Niente parole inglesi.

FORMATO OUTPUT:
<response>
[testo narrativo qui]
</response>
"""


class BeatNarrator:

    def __init__(
        self,
        llm: LLMClient,
        retriever: HybridRetriever,
        lore_graph: LoreGraph,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.lore_graph = lore_graph

    def render(
        self,
        ctx: BeatContext,
        exploration_result: ExplorationResult | None = None,
    ) -> str:
        core_ctx = build_core_context(self.lore_graph, ctx.location_id)
        retrieval_ctx = self._retrieval(ctx, exploration_result)
        user_prompt = self._user_prompt(ctx, exploration_result, core_ctx, retrieval_ctx)

        logger.info("BeatNarrator: beat=%s scene=%s chars=%d",
                    ctx.beat_type, ctx.scene_id, len(user_prompt))
        if retrieval_ctx:
            logger.info("Retrieval:\n%s", retrieval_ctx[:600])

        raw = self.llm.generate(BEAT_SYSTEM_PROMPT, user_prompt)
        return self._extract(raw)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def _retrieval(self, ctx: BeatContext, expl: ExplorationResult | None) -> str:
        if ctx.beat_type in ("arrival", "event", "decision"):
            return ""

        if ctx.beat_type == "exploration" and expl:
            if expl.type == "examined" and expl.object:
                return self._fetch(expl.object.name, ctx.location_id, max_chunks=4)
            if expl.type == "dialog" and expl.npc:
                query = f"{expl.npc.id} {expl.player_query or ''}".strip()
                return self._fetch(query, ctx.location_id, max_chunks=4)
            if expl.type == "group_dialog":
                # Retrieval sugli NPC principali della scena
                npcs_query = " ".join(ctx.npcs_present) if ctx.npcs_present else ctx.location_id
                return self._fetch(npcs_query, ctx.location_id, max_chunks=4)

        return ""

    def _fetch(self, query: str, location_id: str, max_chunks: int) -> str:
        result = self.retriever.retrieve(
            player_input=query,
            current_location_id=location_id,
            max_chunks=max_chunks,
        )
        return result.to_prompt_text(max_chunks=max_chunks)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _user_prompt(
        self,
        ctx: BeatContext,
        expl: ExplorationResult | None,
        core_ctx: str,
        retrieval_ctx: str,
    ) -> str:
        parts = [core_ctx]
        if retrieval_ctx:
            parts.append("## Contesto lore recuperato\n" + retrieval_ctx)
        parts.append(self._state_block(ctx))
        parts.append(self._instruction(ctx, expl))
        return "\n\n".join(p for p in parts if p)

    def _state_block(self, ctx: BeatContext) -> str:
        gs = ctx.game_state
        lines = [
            "## Stato runtime",
            f"Salute: {gs.stats.get('health', 100)} | "
            f"Sospetto: {gs.stats.get('suspicion', 0)} | "
            f"Reputazione: {gs.stats.get('reputation', 0)}",
            f"Inventario: {', '.join(gs.inventory) or 'vuoto'}",
        ]
        if gs.recent_events:
            lines.append("Ultimi eventi:")
            lines.extend(f"  - {e}" for e in gs.recent_events[-3:])
        return "\n".join(lines)

    def _instruction(self, ctx: BeatContext, expl: ExplorationResult | None) -> str:

        if ctx.beat_type == "arrival":
            npcs = ", ".join(ctx.npcs_present) or "nessuno"
            objs = ", ".join(ctx.objects_present) or "—"
            return "\n".join([
                "## Istruzione di regia [PRIVATA]",
                ctx.director_note or "",
                "",
                f"NPC presenti: {npcs}",
                f"Oggetti visibili: {objs}",
                "",
                "## Compito",
                "Scrivi la descrizione di arrivo. 80-130 parole. "
                "Seconda persona. Termina nel momento presente.",
            ])

        if ctx.beat_type == "event":
            facts_lines = "\n".join(f"- {f}" for f in (ctx.facts or []))
            return "\n".join([
                "## Istruzione di regia [PRIVATA]",
                ctx.director_note or "",
                "",
                "## Fatti da rendere — TUTTI, nessuno escluso",
                facts_lines,
                "",
                "## Compito",
                "Scrivi la scena in prosa. 100-160 parole. "
                "Tutti i fatti devono apparire. Niente fatti inventati.",
            ])

        if ctx.beat_type == "exploration" and expl:

            if expl.type == "examined" and expl.object:
                return "\n".join([
                    f"## Azione: esamina '{expl.object.name}'",
                    "",
                    "## Indicazione [PRIVATA]",
                    expl.object.description_hint or "",
                    "",
                    "## Compito",
                    "Descrivi cosa vede o scopre il protagonista. "
                    "50-90 parole. Solo percezione, niente azione.",
                ])

            if expl.type == "dialog" and expl.npc:
                topics = ", ".join(expl.npc.topic_hints) or "—"
                forbidden = ", ".join(expl.npc.forbidden_topics) or "—"
                return "\n".join([
                    f"## Azione: parla con '{expl.npc.id}'",
                    f"Cosa dice o chiede: {expl.player_query}",
                    "",
                    "## Vincoli NPC",
                    f"Può parlare di: {topics}",
                    f"NON rivela mai: {forbidden}",
                    "",
                    "## Compito",
                    "Scrivi la risposta dell'NPC in prosa. "
                    "Può includere battute fra virgolette. "
                    "50-90 parole. Resta in personaggio.",
                ])

            if expl.type == "group_dialog":
                npcs = ", ".join(ctx.npcs_present) or "il gruppo"
                return "\n".join([
                    f"## Azione: il protagonista si rivolge al gruppo",
                    f"Cosa dice: {expl.player_query}",
                    f"Presenti: {npcs}",
                    "",
                    "## Compito",
                    "Scrivi la reazione del gruppo alla rivelazione/domanda del protagonista. "
                    "Puoi includere brevi risposte di più personaggi. "
                    "60-100 parole. Ogni personaggio risponde coerentemente "
                    "con la propria personalità (descritta nel contesto).",
                ])

            # flavor
            return "\n".join([
                "## Azione fuori contesto",
                expl.player_query or "",
                "",
                "## Compito",
                f"Scena corrente: '{ctx.scene_title}'. "
                "Scrivi UNA frase atmosferica ancorata al presente della scena. "
                "Non fare avanzare la trama. Non citare scene precedenti.",
            ])

        if ctx.beat_type == "decision":
            return "\n".join([
                "## Esito della scelta [PRIVATO]",
                ctx.director_note or "",
                "",
                "## Compito",
                "Descrivi l'esito in prosa. 40-70 parole. Asciutto.",
            ])

        return "## Compito\nDescrivi la scena corrente in 50 parole."

    # ------------------------------------------------------------------
    # Estrazione
    # ------------------------------------------------------------------

    @staticmethod
    def _extract(raw: str) -> str:
        # Rimuovi blocchi <think> (Qwen3 thinking mode)
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        
        match = re.search(
            r"<response>\s*(.*?)\s*(?:</response>|$)",
            cleaned, re.DOTALL | re.IGNORECASE,
        )
        if match:
            text = match.group(1).strip()
            text = re.sub(r"</?(think|action|stat_change)>", "", text, flags=re.IGNORECASE)
            return text.strip()
        
        # Fallback: se non c'è il tag ma c'è testo, prendilo
        if cleaned:
            logger.warning("Nessun tag <response> — restituisco testo cleaned")
            return cleaned
        
        logger.warning("Output LLM vuoto")
        return ""