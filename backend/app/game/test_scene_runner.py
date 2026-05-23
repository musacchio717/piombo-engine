"""
CLI test runner for the SceneRunner.

Run from backend/ root:
    python -m app.game.test_scene_runner

Loads scenes from seed_lore/scenes/ and flags from seed_lore/flags.json,
plays through interactively. The "narrator" is mocked — it just prints
the BeatContext that would be sent to the LLM. This validates the state
machine before wiring up the actual LangGraph narrator.
"""
import sys
import logging
from pathlib import Path

from .scene_loader import SceneLoader, load_flags_manifest
from .scene_runner import SceneRunner
from .models import BeatContext, ExplorationResult

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

SEPARATOR = "=" * 70


def render_beat(ctx: BeatContext, exploration_result: ExplorationResult | None = None) -> None:
    """Mock narrator — prints what would be sent to the LLM."""
    print()
    print(SEPARATOR)
    print(f"[BEAT: {ctx.beat_type.upper()}]  scene: {ctx.scene_title}  ({ctx.scene_id})")
    print(f"location_id: {ctx.location_id}")
    print(SEPARATOR)

    if ctx.director_note:
        print(f"\n[director_note]\n{ctx.director_note}")

    if ctx.facts:
        print("\n[facts]")
        for fact in ctx.facts:
            print(f"  • {fact}")

    if ctx.npcs_present:
        print(f"\n[npcs_present] {ctx.npcs_present}")
    if ctx.objects_present:
        print(f"[objects_present] {ctx.objects_present}")

    if exploration_result:
        print(f"\n[exploration_result: {exploration_result.type}]")
        if exploration_result.type == "examined" and exploration_result.object:
            print(f"  examined: {exploration_result.object.name}")
            print(f"  hint: {exploration_result.object.description_hint}")
        elif exploration_result.type == "dialog" and exploration_result.npc:
            print(f"  npc: {exploration_result.npc.id}")
            print(f"  query: {exploration_result.player_query}")
            print(f"  topic_hints: {exploration_result.npc.topic_hints}")
            print(f"  forbidden: {exploration_result.npc.forbidden_topics}")
        elif exploration_result.type == "flavor":
            print(f"  free action (no match): {exploration_result.player_query}")
        print(f"  budget_remaining: {exploration_result.budget_remaining}")

    if ctx.choices:
        print("\n[choices]")
        for i, c in enumerate(ctx.choices):
            print(f"  [{i}] {c.text}  (id={c.id})")

    s = ctx.game_state
    print(f"\n[state] stats={s.stats}  inv={s.inventory}")
    active_flags = [k for k, v in s.flags.items() if v]
    print(f"[state] active_flags={active_flags}")
    print(f"[state] recent_events={s.recent_events}")


def play_session(runner: SceneRunner, start_scene_id: str) -> None:
    ctx = runner.start_session(session_id="test_001", start_scene_id=start_scene_id)

    while not runner.is_finished():
        render_beat(ctx)

        if ctx.beat_type == "arrival":
            input("\n(arrival rendered — premi Enter per continuare) ")
            ctx = runner.advance_beat()

        elif ctx.beat_type == "exploration":
            while runner.state.exploration_budget_remaining > 0:
                action = input(
                    f"\n(esplorazione — budget {runner.state.exploration_budget_remaining}) "
                    "scrivi azione, o 'avanti' per saltare > "
                ).strip()
                if action.lower() in ("avanti", "skip", "fine", ""):
                    break
                result = runner.handle_exploration_action(action)
                render_beat(ctx, exploration_result=result)
            ctx = runner.skip_exploration() if runner.state.current_beat == "exploration" else runner.advance_beat()

        elif ctx.beat_type == "event":
            input("\n(evento rendered — premi Enter per continuare) ")
            ctx = runner.advance_beat()

        elif ctx.beat_type == "decision":
            while True:
                raw = input("\nscegli (numero) > ").strip()
                try:
                    idx = int(raw)
                    choice = ctx.choices[idx]
                    break
                except (ValueError, IndexError):
                    print("Numero non valido. Riprova.")

            resolution_ctx = runner.make_choice(choice.id)
            render_beat(resolution_ctx)
            input("\n(resolution rendered — premi Enter per la prossima scena) ")
            ctx = runner.transition()
            if ctx is None:
                print("\n*** Game over — nessuna scena successiva ***")
                break

    print("\n" + SEPARATOR)
    print("FINE SESSIONE")
    print(SEPARATOR)
    print(f"Stato finale: {runner.state.model_dump_json(indent=2)}")


def main():
    backend_root = Path(__file__).parent.parent.parent
    scenes_dir = backend_root / "seed_lore" / "scenes"
    flags_file = backend_root / "seed_lore" / "flags.json"

    print(f"scenes_dir: {scenes_dir}")
    print(f"flags_file: {flags_file}")

    loader = SceneLoader(scenes_dir)
    scenes = loader.load_all()
    print(f"\nCaricate {len(scenes)} scene: {list(scenes.keys())}")

    flags_manifest = load_flags_manifest(flags_file)
    print(f"Caricati {len(flags_manifest)} flag")

    runner = SceneRunner(scenes, flags_manifest)

    start_scene = sys.argv[1] if len(sys.argv) > 1 else "stazione_pavia_inoculazione"
    play_session(runner, start_scene)


if __name__ == "__main__":
    main()