"""Bundled, editable system instructions."""

from importlib.resources import files


def materials_prompt():
    return files(__package__).joinpath("MATERIALS.md").read_text(encoding="utf-8").strip()
