"""
SceneRunner — orchestratore beat-aware del motore narrativo.

Gestisce stato, progressione beat, transizioni tra scene.
NON gestisce LLM né classificazione intenti — quelli sono BeatNarrator e IntentRouter.
"""
import logging
from typing import Optional
from .models import (
    Scene, GameState, BeatContext, StatChanges, Choice,
    ExplorationResult, BeatType,
)

logger = logging.getLogger(__name__)


class SceneRunner:

    def __init__(self, scenes: dict[str, Scene], flags_manifest: dict[str, bool]):
        self.scenes = scenes
        self.flags_manifest = flags_manifest
        self.state: Optional[GameState] = None
        self._pending_choice: Optional[Choice] = None

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def start_session(self, session_id: str, start_scene_id: str) -> BeatContext:
        if start_scene_id not in self.scenes:
            raise KeyError(f"Scena non trovata: {start_scene_id}")
        self.state = GameState(
            session_id=session_id,
            current_scene_id=start_scene_id,
            current_beat="arrival",
            flags=dict(self.flags_manifest),
        )
        self._apply_on_enter(self.scenes[start_scene_id])
        return self._build_ctx()

    def is_finished(self) -> bool:
        return self.state is not None and self.state.current_beat == "completed"

    def current_beat_context(self) -> BeatContext:
        return self._build_ctx()

    # ------------------------------------------------------------------
    # Beat progression
    # ------------------------------------------------------------------

    def advance_beat(self) -> Optional[BeatContext]:
        """Avanza al beat successivo. Restituisce None se siamo alla decision."""
        scene = self.scenes[self.state.current_scene_id]
        beat = self.state.current_beat

        if beat == "arrival":
            if scene.exploration:
                self.state.current_beat = "exploration"
                self.state.exploration_budget_remaining = scene.exploration.budget
            elif scene.event:
                self.state.current_beat = "event"
            else:
                self.state.current_beat = "decision"

        elif beat == "exploration":
            if scene.event:
                self.state.current_beat = "event"
            else:
                self.state.current_beat = "decision"

        elif beat == "event":
            if scene.event and scene.event.sets_flag:
                self._set_flag(scene.event.sets_flag)
            self.state.current_beat = "decision"

        elif beat == "decision":
            return None

        return self._build_ctx()

    def skip_exploration(self) -> Optional[BeatContext]:
        if self.state.current_beat != "exploration":
            raise RuntimeError("Non siamo in exploration")
        self.state.exploration_budget_remaining = 0
        return self.advance_beat()

    # ------------------------------------------------------------------
    # Exploration — stato
    # ------------------------------------------------------------------

    def consume_exploration_action(self, result: ExplorationResult) -> ExplorationResult:
        """
        Applica gli effetti di stato di un'azione di esplorazione già classificata
        (da IntentRouter o dal matching legacy).

        - flavor:        nessun costo di budget, nessun flag
        - tutto il resto: decrementa budget, setta reveal_flag se presente
        """
        if result.type == "flavor":
            result.budget_remaining = self.state.exploration_budget_remaining
            return result

        # Azioni matched consumano budget
        self.state.exploration_budget_remaining = max(
            0, self.state.exploration_budget_remaining - 1
        )
        result.budget_remaining = self.state.exploration_budget_remaining

        # Flag dall'oggetto esaminato
        if result.type == "examined" and result.object and result.object.reveal_flag:
            self._set_flag(result.object.reveal_flag)

        return result

    def handle_exploration_action(self, action_text: str) -> ExplorationResult:
        """
        Matching legacy (substring). Usato nei test mock o come fallback.
        Preferire IntentRouter per il matching semantico.
        """
        if self.state.current_beat != "exploration":
            raise RuntimeError("Non siamo in exploration")

        scene = self.scenes[self.state.current_scene_id]
        if not scene.exploration:
            raise RuntimeError("Scena senza exploration beat")

        action_lower = action_text.lower()

        for obj in scene.exploration.examinable:
            if obj.requires_flag and not self.state.flags.get(obj.requires_flag, False):
                continue
            if obj.name.lower() in action_lower or obj.id.lower() in action_lower:
                result = ExplorationResult(
                    type="examined", object=obj, player_query=action_text
                )
                return self.consume_exploration_action(result)

        for npc in scene.exploration.dialogable_npcs:
            npc_token = npc.id.split("_")[0].lower()
            if npc.id.lower() in action_lower or npc_token in action_lower:
                result = ExplorationResult(
                    type="dialog", npc=npc, player_query=action_text
                )
                return self.consume_exploration_action(result)

        # Flavor — nessun budget consumato
        result = ExplorationResult(type="flavor", player_query=action_text)
        result.budget_remaining = self.state.exploration_budget_remaining
        return result

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def available_choices(self) -> list[Choice]:
        if self.state.current_beat != "decision":
            return []
        scene = self.scenes[self.state.current_scene_id]
        return [c for c in scene.decision.choices if self._choice_ok(c)]

    def make_choice(self, choice_id: str) -> BeatContext:
        if self.state.current_beat != "decision":
            raise RuntimeError("Non siamo in decision")
        scene = self.scenes[self.state.current_scene_id]
        choice = next((c for c in scene.decision.choices if c.id == choice_id), None)
        if not choice:
            raise ValueError(f"Choice non trovata: {choice_id}")
        if not self._choice_ok(choice):
            raise ValueError(f"Choice non disponibile: {choice_id}")

        self._apply_changes(choice.effects)
        self.state.recent_events.append(f"[{scene.title}] {choice.text}")
        if len(self.state.recent_events) > 8:
            self.state.recent_events.pop(0)

        self._pending_choice = choice

        return BeatContext(
            beat_type="decision",
            scene_id=scene.id,
            scene_title=scene.title,
            location_id=scene.location_id,
            director_note=choice.resolution_hint,
            choices=scene.decision.choices,
            selected_choice_id=choice_id,
            game_state=self.state.model_copy(),
        )

    def transition(self) -> Optional[BeatContext]:
        if not self._pending_choice:
            raise RuntimeError("Nessuna choice pending")
        next_id = self._pending_choice.next_scene_id
        self._pending_choice = None

        if not next_id:
            self.state.current_beat = "completed"
            return None

        next_scene = self.scenes.get(next_id)
        if not next_scene:
            logger.error("Scena non trovata: %s", next_id)
            self.state.current_beat = "completed"
            return None

        if not self._can_enter(next_scene):
            raise RuntimeError(f"Entry conditions non soddisfatte per: {next_id}")

        self.state.current_scene_id = next_id
        self.state.current_beat = "arrival"
        self._apply_on_enter(next_scene)
        return self._build_ctx()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_on_enter(self, scene: Scene) -> None:
        if scene.on_enter:
            self._apply_changes(scene.on_enter)

    def _apply_changes(self, changes: StatChanges) -> None:
        for stat, delta in changes.stats.items():
            self.state.stats[stat] = self.state.stats.get(stat, 0) + delta
        for flag in changes.flags_set:
            self._set_flag(flag)
        for flag in changes.flags_clear:
            self._clear_flag(flag)
        for item in changes.inventory_add:
            if item not in self.state.inventory:
                self.state.inventory.append(item)
        for item in changes.inventory_remove:
            if item in self.state.inventory:
                self.state.inventory.remove(item)

    def _set_flag(self, flag: str) -> None:
        if flag not in self.flags_manifest:
            logger.warning("Flag non nel manifesto: %s", flag)
        self.state.flags[flag] = True

    def _clear_flag(self, flag: str) -> None:
        self.state.flags[flag] = False

    def _choice_ok(self, choice: Choice) -> bool:
        if choice.requires_flag and not self.state.flags.get(choice.requires_flag, False):
            return False
        if choice.requires_stat:
            for key, req in choice.requires_stat.items():
                if key == "inventory_contains":
                    items = [req] if isinstance(req, str) else list(req)
                    if not all(it in self.state.inventory for it in items):
                        return False
                elif isinstance(req, dict):
                    val = self.state.stats.get(key, 0)
                    if "min" in req and val < req["min"]:
                        return False
                    if "max" in req and val > req["max"]:
                        return False
        return True

    def _can_enter(self, scene: Scene) -> bool:
        if not scene.entry_conditions:
            return True
        ec = scene.entry_conditions
        for flag in ec.flags_required:
            if not self.state.flags.get(flag, False):
                return False
        for flag in ec.flags_absent:
            if self.state.flags.get(flag, False):
                return False
        for stat, req in ec.stats.items():
            val = self.state.stats.get(stat, 0)
            if req.min is not None and val < req.min:
                return False
            if req.max is not None and val > req.max:
                return False
        return True

    def _build_ctx(self) -> BeatContext:
        scene = self.scenes[self.state.current_scene_id]
        beat = self.state.current_beat

        ctx = BeatContext(
            beat_type=beat,
            scene_id=scene.id,
            scene_title=scene.title,
            location_id=scene.location_id,
            game_state=self.state.model_copy(),
        )

        if beat == "arrival":
            ctx.director_note = scene.arrival.director_note
            ctx.npcs_present = scene.arrival.npcs_present
            ctx.objects_present = scene.arrival.objects_present
        elif beat == "event" and scene.event:
            ctx.director_note = scene.event.director_note
            ctx.facts = scene.event.facts
        elif beat == "decision":
            ctx.choices = self.available_choices()

        return ctx