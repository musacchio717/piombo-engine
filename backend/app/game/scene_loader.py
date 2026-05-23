"""
Loads scene definitions from JSON files in a directory.
"""
import json
import logging
from pathlib import Path
from .models import Scene

logger = logging.getLogger(__name__)


class SceneLoader:
    def __init__(self, scenes_dir: Path):
        self.scenes_dir = Path(scenes_dir)
        self.scenes: dict[str, Scene] = {}

    def load_all(self) -> dict[str, Scene]:
        """Load all *.json files in scenes_dir as Scene objects, keyed by id."""
        if not self.scenes_dir.exists():
            raise FileNotFoundError(f"Scenes directory not found: {self.scenes_dir}")

        for json_file in sorted(self.scenes_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                scene = Scene(**data)
                if scene.id in self.scenes:
                    logger.warning("Duplicate scene id %s — overwriting", scene.id)
                self.scenes[scene.id] = scene
                logger.info("Loaded scene: %s (%s)", scene.id, json_file.name)
            except Exception as e:
                logger.error("Failed to load %s: %s", json_file, e)
                raise

        return self.scenes

    def get(self, scene_id: str) -> Scene:
        if scene_id not in self.scenes:
            raise KeyError(f"Scene not found: {scene_id}")
        return self.scenes[scene_id]


def load_flags_manifest(flags_file: Path) -> dict[str, bool]:
    """Load the flags manifest into a dict of {flag_name: default_value}."""
    with open(flags_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    flags = data.get("flags", {})
    return {name: spec.get("default", False) for name, spec in flags.items()}