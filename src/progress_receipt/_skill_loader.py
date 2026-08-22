"""Load the standalone Agent Skill scripts used by the packaged CLI."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

from importlib import resources, util
from pathlib import Path
import sys
from types import ModuleType


_SKILL_NAME = "visualize-project-progress"
_DEPENDENCIES = {
    "collect_progress": (),
    "render_progress": ("collect_progress",),
    "accept_progress": ("collect_progress", "render_progress"),
}


def skill_root() -> Path:
    """Return the skill directory from wheel data or a source checkout."""
    packaged = resources.files("progress_receipt").joinpath("skills", _SKILL_NAME)
    if packaged.is_dir():
        return Path(str(packaged))
    checkout = Path(__file__).resolve().parents[2] / "skills" / _SKILL_NAME
    if checkout.is_dir():
        return checkout
    raise RuntimeError("The packaged visualize-project-progress skill is missing")


def load_skill_script(module_name: str) -> ModuleType:
    """Load one canonical skill script by its original module name."""
    if module_name not in _DEPENDENCIES:
        raise ValueError(f"Unknown skill script: {module_name}")
    script = skill_root() / "scripts" / f"{module_name}.py"
    existing = sys.modules.get(module_name)
    existing_file = getattr(existing, "__file__", None)
    if existing_file is not None and Path(existing_file).resolve() == script.resolve():
        return existing
    for dependency in _DEPENDENCIES[module_name]:
        load_skill_script(dependency)
    spec = util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load skill script: {script}")
    module = util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def run_script_cli(module_name: str, argv: list[str] | None = None) -> int:
    """Invoke an unchanged skill script's CLI with explicit arguments."""
    module = load_skill_script(module_name)
    original = sys.argv
    sys.argv = [module_name, *(sys.argv[1:] if argv is None else argv)]
    try:
        return int(module.main())
    finally:
        sys.argv = original
