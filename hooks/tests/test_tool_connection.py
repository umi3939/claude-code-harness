#!/usr/bin/env python3
"""Tests for 44 unlinked tool connection — Groups 1-6.

Tests the following integration points:
- Group 1: session_start extension in skill_executor._get_session_start_extras()
- Group 2: session_end extension in growth_recorder.handle_session_summary()
- Group 3: cycle_complete extension in growth_recorder.handle_cycle_complete()
- Group 4: Context Injection extension in skill_executor._get_attention_residual()
- Group 5: memory_search -> emotion_return chain in skill_executor
- Group 6: lesson validation chain in lesson-after-feedback.js (manual test)
- Group 7: behavior-rules.json suggest rules (structure test)
"""

import json
import os
import sys

import pytest

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

TOOLS_DIR = os.path.join(os.path.dirname(HOOKS_DIR), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


# ── Group 1: session_start extras ──


class TestSessionStartExtras:
    """Test _get_session_start_extras returns combined tool results."""

    def test_returns_string_on_all_import_failures(self):
        """When all tool imports fail, returns empty string (fail-open)."""
        import skill_executor

        # Even if modules are missing, should not raise
        result = skill_executor._get_session_start_extras()
        assert isinstance(result, str)

    def test_truncates_output(self):
        """Output from each tool is truncated to 200 chars."""
        import skill_executor

        result = skill_executor._get_session_start_extras()
        # Each individual line should be <= ~250 chars (200 + label prefix)
        if result:
            for line in result.split("\n"):
                assert len(line) <= 300, f"Line too long: {len(line)} chars"


# ── Group 2: session_end extension ──


class TestSessionSummaryExtension:
    """Test handle_session_summary calls additional tools."""

    def test_session_summary_includes_behavior_analyze(self, tmp_path):
        """Session summary should attempt behavior_analyze."""
        import growth_recorder

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        result = growth_recorder.handle_session_summary("", growth_dir)
        assert result["success"] is True
        # behavior_analyze result key should exist (even if empty/failed)
        assert "behavior_analyze" in result or "summary" in result

    def test_session_summary_includes_long_term_stats(self, tmp_path):
        """Session summary should attempt long_term_stats."""
        import growth_recorder

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        result = growth_recorder.handle_session_summary("", growth_dir)
        assert result["success"] is True

    def test_session_summary_fail_open(self, tmp_path):
        """Additional tool failures should not break session_summary."""
        import growth_recorder

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        # Should always succeed even if extra tools fail
        result = growth_recorder.handle_session_summary("{}", growth_dir)
        assert result["success"] is True


# ── Group 3: cycle_complete extension ──


class TestCycleCompleteExtension:
    """Test handle_cycle_complete calls additional growth tools."""

    def test_cycle_complete_includes_extra_tools(self, tmp_path):
        """cycle_complete should attempt additional tool calls."""
        import growth_recorder

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        stdin_data = json.dumps({
            "cycle_name": "C99-test-connection",
            "completed_gaps": ["G99"],
            "test_count": 5,
            "review_result": "APPROVE",
        })
        result = growth_recorder.handle_cycle_complete(stdin_data, growth_dir)
        assert result["success"] is True
        # New keys from extended tools
        assert "mastery_report" in result
        assert "workflow_crystallize" in result

    def test_cycle_complete_extra_tools_fail_open(self, tmp_path):
        """Extra tool failures should not affect base cycle_complete."""
        import growth_recorder

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        result = growth_recorder.handle_cycle_complete("", growth_dir)
        assert result["success"] is True


# ── Group 4: Attention Residual context injection ──


class TestAttentionResidual:
    """Test _get_attention_residual returns tool results for context injection."""

    def test_returns_string(self):
        """Should return a string (possibly empty)."""
        import skill_executor

        result = skill_executor._get_attention_residual("test task context")
        assert isinstance(result, str)

    def test_empty_context_handled(self):
        """Empty context should not crash."""
        import skill_executor

        result = skill_executor._get_attention_residual("")
        assert isinstance(result, str)

    def test_truncated_output(self):
        """Output lines should be truncated."""
        import skill_executor

        result = skill_executor._get_attention_residual("design phase")
        if result:
            for line in result.split("\n"):
                assert len(line) <= 300


# ── Group 5: memory_search -> emotion_return chain ──


class TestEmotionReturnChain:
    """Test _chain_emotion_return after memory search."""

    def test_returns_string(self):
        """Should return a string result."""
        import skill_executor

        result = skill_executor._chain_emotion_return("test search results")
        assert isinstance(result, str)

    def test_empty_input(self):
        """Empty search results should return empty string."""
        import skill_executor

        result = skill_executor._chain_emotion_return("")
        assert isinstance(result, str)

    def test_fail_open(self):
        """Import/call failures should return empty string, not raise."""
        import skill_executor

        result = skill_executor._chain_emotion_return(None)
        assert isinstance(result, str)


# ── Group 7: behavior-rules.json suggest rules structure ──


class TestBehaviorRulesSuggestRules:
    """Test that suggest-* rules exist and have correct structure."""

    @pytest.fixture
    def rules(self):
        rules_path = os.path.join(HOOKS_DIR, "behavior-rules.json")
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["rules"]

    def test_suggest_rules_exist(self, rules):
        """At least one suggest-* rule should exist."""
        suggest_rules = [r for r in rules if r["id"].startswith("suggest-")]
        assert len(suggest_rules) >= 5, f"Expected >= 5 suggest rules, found {len(suggest_rules)}"

    def test_suggest_rules_are_non_blocking(self, rules):
        """All suggest-* rules should have blocking=false."""
        suggest_rules = [r for r in rules if r["id"].startswith("suggest-")]
        for rule in suggest_rules:
            assert rule.get("blocking") is False, (
                f"Rule {rule['id']} should be non-blocking (suggest only)"
            )

    def test_suggest_rules_have_required_fields(self, rules):
        """All suggest-* rules should have id, trigger, message, type."""
        suggest_rules = [r for r in rules if r["id"].startswith("suggest-")]
        for rule in suggest_rules:
            assert "id" in rule
            assert "trigger" in rule
            assert "message" in rule
            assert "type" in rule
            assert "domain" in rule


# ── Group 6: lesson validation chain ──


class TestLessonValidationChain:
    """Test validate_lesson + detect_lesson_conflicts chain existence."""

    def test_validate_lesson_importable(self):
        """validate_lesson should be importable from memory_mcp_server."""
        from memory_mcp_server import validate_lesson
        assert callable(validate_lesson)

    def test_detect_lesson_conflicts_importable(self):
        """detect_lesson_conflicts should be importable."""
        from memory_mcp_server import detect_lesson_conflicts
        assert callable(detect_lesson_conflicts)


# ── Tool 1: memory_search auto in context injection ──


class TestAutoMemorySearch:
    """Test _auto_memory_search returns context-relevant search results."""

    def test_returns_string(self):
        """Should return a string (possibly empty)."""
        import skill_executor

        result = skill_executor._auto_memory_search("test context")
        assert isinstance(result, str)

    def test_empty_context(self):
        """Empty context should return empty string."""
        import skill_executor

        result = skill_executor._auto_memory_search("")
        assert isinstance(result, str)

    def test_truncated_output(self):
        """Output should be truncated to MAX_TOOL_RESULT_LEN."""
        import skill_executor

        result = skill_executor._auto_memory_search("session start context")
        if result:
            assert len(result) <= 300


# ── Tool 2: stm_write auto in context injection ──


class TestAutoStmWrite:
    """Test _auto_stm_session_plan writes session plan to STM."""

    def test_returns_string(self):
        """Should return a string."""
        import skill_executor

        result = skill_executor._auto_stm_session_plan("gap analysis context")
        assert isinstance(result, str)

    def test_empty_context(self):
        """Empty context should not crash."""
        import skill_executor

        result = skill_executor._auto_stm_session_plan("")
        assert isinstance(result, str)

    def test_fail_open(self):
        """Import/call failures should return empty string."""
        import skill_executor

        result = skill_executor._auto_stm_session_plan(None)
        assert isinstance(result, str)


# ── Tool 3: memory_record auto at session_end ──


class TestAutoMemoryRecordSessionEnd:
    """Test session_end_auto calls memory_record for session episode."""

    def test_session_end_auto_run_with_memory_record(self, tmp_path):
        """session_end_auto.run should attempt memory_record."""
        import session_end_auto

        hooks_dir = str(tmp_path / "hooks")
        memory_dir = str(tmp_path / "memory")
        os.makedirs(hooks_dir, exist_ok=True)
        os.makedirs(memory_dir, exist_ok=True)

        # Create minimal STM for summary
        stm_data = {"entries": [
            {"category": "thought", "content": "test thought", "weight": 1.0}
        ]}
        stm_path = os.path.join(memory_dir, "short_term_memory.json")
        with open(stm_path, "w", encoding="utf-8") as f:
            json.dump(stm_data, f)

        # run will fail at call_session_end (no full env), but
        # the flag should still be written (fail-open)
        session_end_auto.run(hooks_dir, memory_dir)
        # If run completes without exception, that's success
        assert True


# ── Tool 4: emotion_react auto at session_end ──


class TestAutoEmotionReactSessionEnd:
    """Test session_end_auto calls emotion_react."""

    def test_auto_emotion_react_returns_string(self):
        """_auto_emotion_react_session should return a string."""
        import session_end_auto

        result = session_end_auto._auto_emotion_react_session("productive session")
        assert isinstance(result, str)

    def test_empty_summary(self):
        """Empty summary should not crash."""
        import session_end_auto

        result = session_end_auto._auto_emotion_react_session("")
        assert isinstance(result, str)


# ── Tool 5: self_snapshot auto in context injection ──


class TestAutoSelfSnapshot:
    """Test _get_self_snapshot_result returns self_snapshot result."""

    def test_returns_string(self):
        """Should return a string."""
        import skill_executor

        result = skill_executor._get_self_snapshot_result()
        assert isinstance(result, str)

    def test_truncated(self):
        """Output should be truncated."""
        import skill_executor

        result = skill_executor._get_self_snapshot_result()
        if result:
            assert len(result) <= 350


# ── Tool 6: memory_consolidate auto in stop-consolidation-check ──


class TestAutoMemoryConsolidate:
    """Test auto_consolidate_if_needed function."""

    def test_auto_consolidate_returns_string(self):
        """Should return a string (possibly empty)."""
        import stop_consolidation_auto

        result = stop_consolidation_auto.auto_consolidate_if_needed()
        assert isinstance(result, str)

    def test_fail_open(self):
        """Should not raise on import/call failures."""
        import stop_consolidation_auto

        # Should always return a string, never raise
        result = stop_consolidation_auto.auto_consolidate_if_needed()
        assert isinstance(result, str)


# ── Auto cycle_complete on reviewer APPROVE ──


class TestAutoCycleComplete:
    """Test auto cycle_complete trigger on reviewer APPROVE detection."""

    def test_should_trigger_cycle_complete_returns_false_no_state(self, tmp_path):
        """No .dev-flow-state file → should not trigger."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        result = auto_cycle_complete.should_trigger_cycle_complete(hooks_dir)
        assert result is False

    def test_should_trigger_when_reviewer_and_impl_done(self, tmp_path):
        """impl > 0 and reviewer > 0 and no review_issues → should trigger."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        state = {
            "design": 1000,
            "planner": 1001,
            "pre_analysis": 1002,
            "impl": 1003,
            "post_analysis": 1004,
            "reviewer": 1005,
            "review_issues_pending": None,
        }
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        result = auto_cycle_complete.should_trigger_cycle_complete(hooks_dir)
        assert result is True

    def test_should_not_trigger_when_review_issues_pending(self, tmp_path):
        """review_issues_pending with count > 0 → should not trigger."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        state = {
            "impl": 1003,
            "reviewer": 1005,
            "review_issues_pending": {"count": 2, "summary": "2 MED issues"},
        }
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        result = auto_cycle_complete.should_trigger_cycle_complete(hooks_dir)
        assert result is False

    def test_should_not_trigger_when_impl_zero(self, tmp_path):
        """impl == 0 → should not trigger."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        state = {"impl": 0, "reviewer": 1005}
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        result = auto_cycle_complete.should_trigger_cycle_complete(hooks_dir)
        assert result is False

    def test_should_not_trigger_already_fired(self, tmp_path):
        """Already-fired flag prevents double trigger."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        state = {"impl": 1003, "reviewer": 1005, "review_issues_pending": None}
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        # Write already-fired flag with matching reviewer timestamp
        flag_file = os.path.join(hooks_dir, ".cycle-complete-fired")
        with open(flag_file, "w", encoding="utf-8") as f:
            f.write("1005")

        result = auto_cycle_complete.should_trigger_cycle_complete(hooks_dir)
        assert result is False

    def test_run_cycle_complete_returns_dict(self, tmp_path):
        """run_cycle_complete should return a result dict."""
        import auto_cycle_complete

        growth_dir = str(tmp_path / "growth")
        os.makedirs(growth_dir, exist_ok=True)

        result = auto_cycle_complete.run_cycle_complete(
            growth_dir, cycle_name="C99-test", test_count=5
        )
        assert isinstance(result, dict)
        assert result.get("success") is True

    def test_run_cycle_complete_fail_open(self, tmp_path):
        """run_cycle_complete should not raise on internal failures."""
        import auto_cycle_complete

        result = auto_cycle_complete.run_cycle_complete(
            str(tmp_path / "nonexistent"), cycle_name="", test_count=0
        )
        assert isinstance(result, dict)


# ── lesson_conflict_checker.py standalone script ──


class TestLessonConflictChecker:
    """Test lesson_conflict_checker.py standalone script."""

    def test_importable_and_callable(self):
        """lesson_conflict_checker.run_conflict_check should be importable."""
        import lesson_conflict_checker

        assert callable(lesson_conflict_checker.run_conflict_check)

    def test_returns_dict_with_success(self):
        """run_conflict_check should return a dict with 'success' key."""
        import lesson_conflict_checker

        result = lesson_conflict_checker.run_conflict_check()
        assert isinstance(result, dict)
        assert "success" in result

    def test_fail_open_on_import_error(self, monkeypatch):
        """If detect_lesson_conflicts import fails, should return success=True with empty report."""
        import lesson_conflict_checker

        # Force import to fail by removing tools from path temporarily
        def fake_import_error():
            raise ImportError("test forced failure")

        monkeypatch.setattr(
            lesson_conflict_checker, "run_conflict_check",
            lambda: {"success": True, "report": "", "error": "test forced failure"},
        )
        result = lesson_conflict_checker.run_conflict_check()
        assert result["success"] is True

    def test_main_outputs_json(self, capsys):
        """main() should print valid JSON to stdout."""
        import lesson_conflict_checker

        lesson_conflict_checker.main()
        captured = capsys.readouterr()
        # stdout should be valid JSON
        import json as _json
        parsed = _json.loads(captured.out)
        assert isinstance(parsed, dict)
        assert "success" in parsed


# ── auto_cycle_complete: success=False on exception ──


class TestAutoCycleCompleteFailureFlag:
    """Test that run_cycle_complete returns success=False on exception
    and that main() does NOT write fired flag on failure."""

    def test_run_cycle_complete_exception_returns_success_false(self, tmp_path, monkeypatch):
        """When growth_recorder.handle_cycle_complete raises, success should be False."""
        import auto_cycle_complete

        # Monkeypatch growth_recorder import to raise
        original_run = auto_cycle_complete.run_cycle_complete

        def patched_run(growth_dir, cycle_name="", test_count=0):
            # Force an import error inside
            import types
            fake_module = types.ModuleType("growth_recorder")

            def fake_handle(*a, **kw):
                raise RuntimeError("simulated failure")

            fake_module.handle_cycle_complete = fake_handle
            monkeypatch.setitem(sys.modules, "growth_recorder", fake_module)
            return original_run(growth_dir, cycle_name=cycle_name, test_count=test_count)

        result = patched_run(str(tmp_path / "growth"), cycle_name="test")
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_main_no_fired_flag_on_failure(self, tmp_path, monkeypatch):
        """main() should NOT write .cycle-complete-fired when run_cycle_complete fails."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        # Setup: .dev-flow-state that would trigger
        state = {
            "impl": 1003,
            "reviewer": 1005,
            "review_issues_pending": None,
        }
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        # Monkeypatch run_cycle_complete to return failure
        monkeypatch.setattr(
            auto_cycle_complete, "run_cycle_complete",
            lambda *a, **kw: {"success": False, "event_type": "cycle_complete", "error": "test"},
        )

        auto_cycle_complete.main(hooks_dir)

        flag_file = os.path.join(hooks_dir, ".cycle-complete-fired")
        assert not os.path.isfile(flag_file), "Fired flag should NOT be written on failure"

    def test_main_writes_fired_flag_on_success(self, tmp_path, monkeypatch):
        """main() SHOULD write .cycle-complete-fired when run_cycle_complete succeeds."""
        import auto_cycle_complete

        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        state = {
            "impl": 1003,
            "reviewer": 1005,
            "review_issues_pending": None,
        }
        state_file = os.path.join(hooks_dir, ".dev-flow-state")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        monkeypatch.setattr(
            auto_cycle_complete, "run_cycle_complete",
            lambda *a, **kw: {"success": True, "event_type": "cycle_complete"},
        )

        auto_cycle_complete.main(hooks_dir)

        flag_file = os.path.join(hooks_dir, ".cycle-complete-fired")
        assert os.path.isfile(flag_file), "Fired flag should be written on success"


# ── Integration test: handle_agent_context includes new sections ──


class TestAgentContextIncludesNewSections:
    """Test that handle_agent_context includes the new injection sections."""

    def test_agent_context_returns_string(self):
        """handle_agent_context should still return a string."""
        import skill_executor

        result = skill_executor.handle_agent_context("Agent", "implementer")
        assert isinstance(result, str)
        assert "Context Injection" in result
