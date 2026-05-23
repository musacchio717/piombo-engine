"""
Models for the scene-based game engine.
"""
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field


# ---------- Scene schema ----------

class StatRange(BaseModel):
    min: Optional[int] = None
    max: Optional[int] = None


class EntryConditions(BaseModel):
    flags_required: list[str] = Field(default_factory=list)
    flags_absent: list[str] = Field(default_factory=list)
    stats: dict[str, StatRange] = Field(default_factory=dict)


class StatChanges(BaseModel):
    stats: dict[str, int] = Field(default_factory=dict)
    flags_set: list[str] = Field(default_factory=list)
    flags_clear: list[str] = Field(default_factory=list)
    inventory_add: list[str] = Field(default_factory=list)
    inventory_remove: list[str] = Field(default_factory=list)


class ArrivalBeat(BaseModel):
    director_note: str
    npcs_present: list[str] = Field(default_factory=list)
    objects_present: list[str] = Field(default_factory=list)


class ExaminableObject(BaseModel):
    id: str
    name: str
    lore_entity_id: Optional[str] = None
    description_hint: str
    reveal_flag: Optional[str] = None
    requires_flag: Optional[str] = None


class DialogableNPC(BaseModel):
    id: str
    topic_hints: list[str] = Field(default_factory=list)
    forbidden_topics: list[str] = Field(default_factory=list)


class ExplorationBeat(BaseModel):
    budget: int
    examinable: list[ExaminableObject] = Field(default_factory=list)
    dialogable_npcs: list[DialogableNPC] = Field(default_factory=list)


class EventBeat(BaseModel):
    trigger: Literal["immediate", "after_exploration"] = "after_exploration"
    director_note: str
    facts: list[str]
    npc_id: Optional[str] = None
    sets_flag: Optional[str] = None


class Choice(BaseModel):
    id: str
    text: str
    requires_flag: Optional[str] = None
    requires_stat: Optional[dict[str, Any]] = None
    effects: StatChanges = Field(default_factory=StatChanges)
    next_scene_id: Optional[str] = None
    resolution_hint: str


class DecisionBeat(BaseModel):
    prompt_hint: Optional[str] = None
    choices: list[Choice]


class Scene(BaseModel):
    id: str
    title: str
    location_id: str
    entry_conditions: Optional[EntryConditions] = None
    on_enter: Optional[StatChanges] = None
    arrival: ArrivalBeat
    exploration: Optional[ExplorationBeat] = None
    event: Optional[EventBeat] = None
    decision: DecisionBeat


# ---------- Runtime state ----------

BeatType = Literal["arrival", "exploration", "event", "decision", "completed"]


class GameState(BaseModel):
    session_id: str
    current_scene_id: str
    current_beat: BeatType = "arrival"
    exploration_budget_remaining: int = 0
    stats: dict[str, int] = Field(default_factory=lambda: {
        "health": 100, "suspicion": 5, "reputation": 0
    })
    flags: dict[str, bool] = Field(default_factory=dict)
    inventory: list[str] = Field(default_factory=list)
    recent_events: list[str] = Field(default_factory=list)


# ---------- Exploration result ----------

ExplorationResultType = Literal[
    "examined",     # player ha esaminato un oggetto
    "dialog",       # player ha parlato con un NPC specifico
    "group_dialog", # player si è rivolto al gruppo
    "flavor",       # azione non riconoscibile / fuori contesto
    "unavailable",  # entità richiesta non disponibile
]


class ExplorationResult(BaseModel):
    type: ExplorationResultType
    object: Optional[ExaminableObject] = None
    npc: Optional[DialogableNPC] = None
    player_query: Optional[str] = None
    budget_remaining: int = 0


# ---------- BeatContext (output verso il narrator) ----------

class BeatContext(BaseModel):
    beat_type: BeatType
    scene_id: str
    scene_title: str
    location_id: str

    director_note: Optional[str] = None
    facts: Optional[list[str]] = None
    npcs_present: list[str] = Field(default_factory=list)
    objects_present: list[str] = Field(default_factory=list)

    exploration_result: Optional[ExplorationResult] = None
    choices: Optional[list[Choice]] = None
    selected_choice_id: Optional[str] = None

    game_state: GameState