"""Every [project.scripts] console entry point must resolve to a callable."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PIPELINE_ROOT / 'pyproject.toml'


def _console_scripts() -> dict[str, str]:
    with PYPROJECT.open('rb') as f:
        data = tomllib.load(f)
    return dict(data['project']['scripts'])


@pytest.mark.parametrize('script_name,target', list(_console_scripts().items()))
def test_console_script_target_is_callable(script_name: str, target: str) -> None:
    module_name, attr_name = target.split(':', 1)
    module = importlib.import_module(module_name)
    attr = getattr(module, attr_name)
    assert callable(attr), f'{script_name} -> {target} is not callable'
