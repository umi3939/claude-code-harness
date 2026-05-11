#!/usr/bin/env python3
"""Tests for session_postmortem.py.

Supports both direct execution and pytest.
"""

import importlib.util
import json
import os
import shutil
import tempfile

import pytest

SCRIPT_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "tools", "session_postmortem.py"
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("session_postmortem", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


def test_generate_returns_dict():
    result = mod.generate_postmortem()
    assert isinstance(result, dict)


def test_has_required_keys():
    result = mod.generate_postmortem()
    for key in ["went_well", "difficulties", "lessons"]:
        assert key in result


def test_went_well_is_list():
    result = mod.generate_postmortem()
    assert isinstance(result["went_well"], list)


def test_difficulties_is_list():
    result = mod.generate_postmortem()
    assert isinstance(result["difficulties"], list)


def test_lessons_is_list():
    result = mod.generate_postmortem()
    assert isinstance(result["lessons"], list)


def test_format_returns_string():
    result = mod.generate_postmortem()
    formatted = mod.format_postmortem(result)
    assert isinstance(formatted, str)


def test_formatted_has_section_headers():
    result = mod.generate_postmortem()
    formatted = mod.format_postmortem(result)
    assert "うまくいった" in formatted or "Went Well" in formatted
    assert "困難" in formatted or "Difficulties" in formatted
    assert "教訓" in formatted or "Lessons" in formatted


def test_format_empty_data():
    empty_result = {"went_well": [], "difficulties": [], "lessons": []}
    formatted = mod.format_postmortem(empty_result)
    assert isinstance(formatted, str)


def test_custom_memory_dir():
    tmpdir = tempfile.mkdtemp()
    try:
        stm_file = os.path.join(tmpdir, "short_term_memory.json")
        stm_data = {
            "entries": [
                {"category": "thought", "content": "This approach worked well", "ts": "2026-03-25T10:00:00+00:00"},
                {"category": "unresolved", "content": "Struggled with hook timing", "ts": "2026-03-25T11:00:00+00:00"},
            ]
        }
        with open(stm_file, "w") as f:
            json.dump(stm_data, f)
        result = mod.generate_postmortem(memory_dir=tmpdir)
        assert isinstance(result, dict)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
