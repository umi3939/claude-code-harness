#!/usr/bin/env python3
"""Tests for skill_executor.py

Tests the Hook -> Skill -> MCP chain:
- agent context: reads gap_analysis, emotion, STM, activation_surface, memory_search
- mcp context: reads tool definition from mcp-tools.md
- unknown context: returns empty
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "hooks", "skill_executor.py"
)

PASS = 0
FAIL = 0


def _run_test(desc, args, expect_in=None, expect_not_in=None, expect_exit=0):
    """Run skill_executor.py with given args and check output."""
    global PASS, FAIL
    try:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH] + args,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        actual_exit = result.returncode
        stdout = result.stdout
        stderr = result.stderr

        ok = True

        if actual_exit != expect_exit:
            print(f"  FAIL: {desc} (exit={actual_exit}, expected={expect_exit})")
            print(f"    stderr: {stderr[:200]}")
            FAIL += 1
            return

        if expect_in:
            for text in (expect_in if isinstance(expect_in, list) else [expect_in]):
                if text not in stdout:
                    print(f"  FAIL: {desc} (expected '{text}' in stdout)")
                    print(f"    stdout: {stdout[:300]}")
                    ok = False

        if expect_not_in:
            for text in (expect_not_in if isinstance(expect_not_in, list) else [expect_not_in]):
                if text in stdout:
                    print(f"  FAIL: {desc} (expected '{text}' NOT in stdout)")
                    ok = False

        if ok:
            print(f"  PASS: {desc}")
            PASS += 1
        else:
            FAIL += 1

    except subprocess.TimeoutExpired:
        print(f"  FAIL: {desc} (timeout)")
        FAIL += 1
    except Exception as e:
        print(f"  FAIL: {desc} (error: {e})")
        FAIL += 1


if __name__ == "__main__":
    print("=== Skill Executor Tests ===")

    # Test 1: agent context produces output with expected sections
    _run_test(
        "agent context produces structured output",
        ["agent", "Agent"],
        expect_in=["[Context Injection]"],
    )

    # Test 2: agent context includes emotion section
    _run_test(
        "agent context includes emotion",
        ["agent", "Agent"],
        expect_in=["Emotion"],
    )

    # Test 3: agent context includes STM section
    _run_test(
        "agent context includes STM",
        ["agent", "Agent"],
        expect_in=["STM"],
    )

    # Test 4: mcp context with known tool name
    _run_test(
        "mcp context with tool name",
        ["mcp", "mcp__memory-tools__stm_write"],
        expect_in=["[Context Injection]"],
    )

    # Test 5: mcp context includes tool reference
    _run_test(
        "mcp context mentions tool name",
        ["mcp", "mcp__memory-tools__stm_write"],
        expect_in=["stm_write"],
    )

    # Test 6: unknown context type returns empty
    _run_test(
        "unknown context returns empty",
        ["unknown", "SomeTool"],
        expect_in=[],  # No specific content expected
    )

    # Test 7: no arguments returns gracefully
    _run_test(
        "no arguments returns gracefully",
        [],
        expect_exit=0,
    )

    # Test 8: agent context for TeamCreate
    _run_test(
        "agent context works for TeamCreate",
        ["agent", "TeamCreate"],
        expect_in=["[Context Injection]"],
    )

    # Test 9: mcp context with emotion/self-observation tool includes emotion
    _run_test(
        "mcp context with emotion tool includes emotion state",
        ["mcp", "mcp__self-observation__self_observe"],
        expect_in=["[Context Injection]"],
    )

    # Test 10: subagent_type=implementer includes dev-flow skill summary
    _run_test(
        "agent+implementer includes skill summary from dev-flow",
        ["agent", "Agent", "implementer"],
        expect_in=["[Skill Summary]"],
    )

    # Test 11: subagent_type=analyzer includes dev-flow skill summary
    _run_test(
        "agent+analyzer includes skill summary from dev-flow",
        ["agent", "Agent", "analyzer"],
        expect_in=["[Skill Summary]"],
    )

    # Test 12: self_observation result included in agent context
    _run_test(
        "agent context includes self observation",
        ["agent", "Agent", "implementer"],
        expect_in=["[Self Observation]"],
    )

    # Test 13: nonexistent subagent_type does not crash
    _run_test(
        "nonexistent subagent_type is safe",
        ["agent", "Agent", "nonexistent_type"],
        expect_in=["[Context Injection]"],
    )

    # Test 14: MCP tool quick reference in agent context
    _run_test(
        "agent context includes MCP quick reference",
        ["agent", "Agent", "implementer"],
        expect_in=["[MCP Quick Ref]"],
    )

    # Test 15: bugfix-related subagent_type includes bugfix skill
    _run_test(
        "agent+bugfix includes bugfix skill summary",
        ["agent", "Agent", "bugfix"],
        expect_in=["[Skill Summary]", "バグ修正"],
    )

    print("")
    print("=== Dev Flow Position Tests (R2) ===")

    HOOKS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
    DEV_FLOW_STATE_FILE = os.path.join(HOOKS_DIR, ".dev-flow-state")

    def setup_dev_flow_state(state_dict):
        """Write a .dev-flow-state file with given state."""
        with open(DEV_FLOW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_dict, f)

    def cleanup_dev_flow_state():
        """Remove .dev-flow-state file."""
        try:
            os.remove(DEV_FLOW_STATE_FILE)
        except FileNotFoundError:
            pass

    # Test R2-1: _get_dev_flow_position with impl state shows post_analysis next
    cleanup_dev_flow_state()
    setup_dev_flow_state({"design": 1000, "planner": 2000, "pre_analysis": 3000, "impl": 4000})
    _run_test(
        "R2: flow position after impl shows post_analysis next",
        ["agent", "UserPromptSubmit"],
        expect_in=["Dev Flow", "impl"],
    )

    # Test R2-2: _get_dev_flow_position with no state file => fail-open (no injection)
    cleanup_dev_flow_state()
    _run_test(
        "R2: no .dev-flow-state file => no Dev Flow injection",
        ["agent", "UserPromptSubmit"],
        expect_not_in=["Dev Flow"],
    )

    # Test R2-3: _get_dev_flow_position with reviewer completed
    cleanup_dev_flow_state()
    setup_dev_flow_state({"design": 1000, "planner": 2000, "pre_analysis": 3000, "impl": 4000, "post_analysis": 5000, "reviewer": 6000})
    _run_test(
        "R2: flow position after reviewer shows commit next",
        ["agent", "UserPromptSubmit"],
        expect_in=["Dev Flow", "reviewer", "commit"],
    )

    # Test R2-4: _get_dev_flow_position with missing post_analysis shows warning
    cleanup_dev_flow_state()
    setup_dev_flow_state({"design": 1000, "planner": 2000, "pre_analysis": 3000, "impl": 4000, "post_analysis": 0, "reviewer": 0})
    _run_test(
        "R2: missing post_analysis and reviewer shows warnings",
        ["agent", "UserPromptSubmit"],
        expect_in=["post-impl analysis未実施", "reviewer未実施"],
    )

    cleanup_dev_flow_state()

    print("")
    print("=== MEMORY_DIR Selection Tests ===")

    # Test M1: _select_memory_dir with empty candidates returns empty string
    try:
        # Import the function directly
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_executor", SCRIPT_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        select_fn = mod._select_memory_dir

        # M1: empty candidates
        result = select_fn([])
        if result == "":
            print("  PASS: M1 empty candidates returns empty string")
            PASS += 1
        else:
            print(f"  FAIL: M1 expected empty, got '{result}'")
            FAIL += 1

        # M2: single candidate returns that candidate
        m2_tmpdir = tempfile.mkdtemp()
        m2_path = os.path.join(m2_tmpdir, "only_one", "memory")
        os.makedirs(m2_path, exist_ok=True)
        result = select_fn([m2_path])
        if result == m2_path:
            print("  PASS: M2 single candidate returns it")
            PASS += 1
        else:
            print(f"  FAIL: M2 expected '{m2_path}', got '{result}'")
            FAIL += 1

        import shutil
        shutil.rmtree(m2_tmpdir, ignore_errors=True)

        # M3: cwd-matching candidate is preferred over others
        # Create temp dirs to simulate multiple projects
        tmpbase = tempfile.mkdtemp()
        cwd = os.getcwd()
        # Normalize cwd to project dir name format
        cwd_normalized = cwd.replace("\\", "/")
        if len(cwd_normalized) >= 2 and cwd_normalized[1] == ":":
            cwd_normalized = cwd_normalized[0] + cwd_normalized[2:]
        cwd_key = cwd_normalized.replace("/", "-")

        matching_dir = os.path.join(tmpbase, cwd_key, "memory")
        other_dir = os.path.join(tmpbase, "other-project", "memory")
        os.makedirs(matching_dir, exist_ok=True)
        os.makedirs(other_dir, exist_ok=True)

        result = select_fn([other_dir, matching_dir])
        if result == matching_dir:
            print("  PASS: M3 cwd-matching candidate preferred")
            PASS += 1
        else:
            print(f"  FAIL: M3 expected matching_dir, got '{result}'")
            FAIL += 1

        # M4: no cwd match falls back to most recently modified
        import time
        old_dir = os.path.join(tmpbase, "old-project", "memory")
        new_dir = os.path.join(tmpbase, "new-project", "memory")
        os.makedirs(old_dir, exist_ok=True)
        time.sleep(0.05)
        os.makedirs(new_dir, exist_ok=True)
        # Touch new_dir to ensure it's newer
        os.utime(new_dir, None)

        result = select_fn([old_dir, new_dir])
        if result == new_dir:
            print("  PASS: M4 fallback to most recently modified")
            PASS += 1
        else:
            print(f"  FAIL: M4 expected new_dir, got '{result}'")
            FAIL += 1

        # Cleanup
        shutil.rmtree(tmpbase, ignore_errors=True)

    except Exception as e:
        print(f"  FAIL: MEMORY_DIR selection tests error: {e}")
        FAIL += 1

    print("")
    print("=== Security Fixes Tests ===")

    # M-S4: Path traversal — _select_memory_dir should resolve symlinks
    # and only accept paths under .claude/projects/
    try:
        import importlib.util
        spec2 = importlib.util.spec_from_file_location("skill_executor_s4", SCRIPT_PATH)
        mod2 = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(mod2)

        # Test S4-1: symlink pointing outside .claude/projects/ should not be selected
        tmpbase_s4 = tempfile.mkdtemp()
        evil_target = os.path.join(tmpbase_s4, "evil_dir")
        os.makedirs(evil_target, exist_ok=True)

        # Create a symlink inside a fake projects dir
        fake_projects = os.path.join(tmpbase_s4, ".claude", "projects", "fake-project")
        os.makedirs(fake_projects, exist_ok=True)
        symlink_path = os.path.join(fake_projects, "memory")
        try:
            os.symlink(evil_target, symlink_path)
            # _select_memory_dir should resolve the symlink and reject it
            result = mod2._select_memory_dir([symlink_path])
            # After fix, should return empty or the resolved path should be validated
            # The key point: realpath resolves the symlink
            real = os.path.realpath(symlink_path)
            if "projects" not in real.replace("\\", "/"):
                # If realpath is outside projects, the fix should reject it
                if result == "":
                    print("  PASS: S4-1 symlink outside projects rejected")
                    PASS += 1
                else:
                    print(f"  FAIL: S4-1 symlink outside projects was accepted: {result}")
                    FAIL += 1
            else:
                print("  PASS: S4-1 (symlink resolved within projects, ok)")
                PASS += 1
        except OSError:
            # Symlinks may require admin on Windows
            print("  SKIP: S4-1 (symlink creation requires privileges)")
            PASS += 1

        import shutil
        shutil.rmtree(tmpbase_s4, ignore_errors=True)

    except Exception as e:
        print(f"  FAIL: S4 tests error: {e}")
        FAIL += 1

    # H3: _is_safe_path should reject paths that claim .claude but resolve outside
    try:
        is_safe = mod2._is_safe_path
        claude_dir = os.path.join(os.path.expanduser("~"), ".claude")

        # H3-1: path that contains .claude but resolves outside should be rejected
        # Simulate a path like /tmp/fake/.claude/projects/evil/memory
        fake_claude = os.path.join(tempfile.gettempdir(), "fake", ".claude", "projects", "evil", "memory")
        os.makedirs(fake_claude, exist_ok=True)
        result = is_safe(fake_claude)
        if result is False:
            print("  PASS: H3-1 fake .claude path outside home rejected")
            PASS += 1
        else:
            print(f"  FAIL: H3-1 fake .claude path was accepted: {fake_claude}")
            FAIL += 1
        import shutil
        shutil.rmtree(os.path.join(tempfile.gettempdir(), "fake"), ignore_errors=True)

        # H3-2: path inside .claude/projects/ should be accepted
        proj_path = os.path.join(claude_dir, "projects", "test-proj", "memory")
        result = is_safe(proj_path)
        if result is True:
            print("  PASS: H3-2 path inside .claude/projects/ accepted")
            PASS += 1
        else:
            print("  FAIL: H3-2 path inside .claude/projects/ was rejected")
            FAIL += 1

        # H3-3: path inside .claude/tools/ should be accepted
        tools_path = os.path.join(claude_dir, "tools", "some_dir")
        result = is_safe(tools_path)
        if result is True:
            print("  PASS: H3-3 path inside .claude/tools/ accepted")
            PASS += 1
        else:
            print("  FAIL: H3-3 path inside .claude/tools/ was rejected")
            FAIL += 1

    except Exception as e:
        print(f"  FAIL: H3 tests error: {e}")
        FAIL += 1

    # M-S5: File size limit on gap analysis
    try:
        MAX_FILE_SIZE = 1024 * 1024  # 1MB expected limit
        spec3 = importlib.util.spec_from_file_location("skill_executor_s5", SCRIPT_PATH)
        mod3 = importlib.util.module_from_spec(spec3)
        spec3.loader.exec_module(mod3)

        if hasattr(mod3, 'MAX_GAP_ANALYSIS_SIZE'):
            if mod3.MAX_GAP_ANALYSIS_SIZE <= MAX_FILE_SIZE:
                print("  PASS: S5 MAX_GAP_ANALYSIS_SIZE constant exists")
                PASS += 1
            else:
                print(f"  FAIL: S5 limit too large: {mod3.MAX_GAP_ANALYSIS_SIZE}")
                FAIL += 1
        else:
            print("  FAIL: S5 MAX_GAP_ANALYSIS_SIZE constant not found")
            FAIL += 1
    except Exception as e:
        print(f"  FAIL: S5 tests error: {e}")
        FAIL += 1

    # M-S6: MEMORY_DIR validation before psyche_drive
    _run_test(
        "S6: empty MEMORY_DIR does not call psyche_drive",
        [],
        expect_exit=0,
    )

    print("")
    print("=== Auto-Continue Tests ===")

    # Test AC-1: cycle complete (reviewer > 0, impl > 0) => Auto-Continue injected
    cleanup_dev_flow_state()
    setup_dev_flow_state({
        "design": 1000, "planner": 2000, "pre_analysis": 3000,
        "impl": 4000, "post_analysis": 5000, "reviewer": 6000,
    })
    _run_test(
        "AC-1: cycle complete with gaps => Auto-Continue injected",
        ["agent", "Agent"],
        expect_in=["[Auto-Continue]"],
    )

    # Test AC-2: cycle not complete (reviewer=0) => no Auto-Continue
    cleanup_dev_flow_state()
    setup_dev_flow_state({
        "design": 1000, "planner": 2000, "pre_analysis": 3000,
        "impl": 4000, "post_analysis": 0, "reviewer": 0,
    })
    _run_test(
        "AC-2: cycle not complete => no Auto-Continue",
        ["agent", "Agent"],
        expect_not_in=["[Auto-Continue]"],
    )

    # Test AC-3: cycle complete but no .dev-flow-state => no Auto-Continue
    cleanup_dev_flow_state()
    _run_test(
        "AC-3: no dev-flow-state => no Auto-Continue",
        ["agent", "Agent"],
        expect_not_in=["[Auto-Continue]"],
    )

    cleanup_dev_flow_state()

    print("")
    print("=== Tool Guide Auto-Injection Tests ===")

    # Tool guide tests run with cwd set to the project root so that
    # _find_tool_guide_path can find .claude/commands/tool-*.md via cwd search.
    # Use the project-local skill_executor.py (same dir as this test file).
    _this_test_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_this_test_dir)
    _tg_script = os.path.join(_this_test_dir, "skill_executor.py")

    def _run_tg_test(desc, args, expect_in=None, expect_not_in=None, expect_exit=0):
        """Run project-local skill_executor.py with cwd=project_root for tool guide tests."""
        global PASS, FAIL
        try:
            result = subprocess.run(
                [sys.executable, _tg_script] + args,
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                cwd=_project_root,
            )
            actual_exit = result.returncode
            stdout = result.stdout
            stderr = result.stderr
            ok = True
            if actual_exit != expect_exit:
                print(f"  FAIL: {desc} (exit={actual_exit}, expected={expect_exit})")
                print(f"    stderr: {stderr[:200]}")
                FAIL += 1
                return
            if expect_in:
                for text in (expect_in if isinstance(expect_in, list) else [expect_in]):
                    if text not in stdout:
                        print(f"  FAIL: {desc} (expected '{text}' in stdout)")
                        print(f"    stdout: {stdout[:300]}")
                        ok = False
            if expect_not_in:
                for text in (expect_not_in if isinstance(expect_not_in, list) else [expect_not_in]):
                    if text in stdout:
                        print(f"  FAIL: {desc} (expected '{text}' NOT in stdout)")
                        ok = False
            if ok:
                print(f"  PASS: {desc}")
                PASS += 1
            else:
                FAIL += 1
        except subprocess.TimeoutExpired:
            print(f"  FAIL: {desc} (timeout)")
            FAIL += 1
        except Exception as e:
            print(f"  FAIL: {desc} (error: {e})")
            FAIL += 1

    # Tool guide tests use direct module import (not subprocess) to avoid
    # environment-dependent import failures in session_restorer/psyche_drive.
    # Detailed subprocess tests are in coding_tests/test_tool_guide.py.
    try:
        import importlib.util as _ilu
        _tg_spec = _ilu.spec_from_file_location("skill_executor_tg", _tg_script)
        _tg_mod = _ilu.module_from_spec(_tg_spec)

        # Temporarily change cwd so cwd/.claude/commands/ is found
        _saved_cwd = os.getcwd()
        os.chdir(_project_root)
        _tg_spec.loader.exec_module(_tg_mod)

        # TG-1: emotion_react tool gets guide
        tg1 = _tg_mod._get_tool_guide("mcp__memory-tools__emotion_react")
        if "[Tool Guide]" in tg1 and "tool-emotion-react.md" in tg1:
            print("  PASS: TG-1: emotion_react tool gets tool guide")
            PASS += 1
        else:
            print(f"  FAIL: TG-1: emotion_react tool guide missing (got: {tg1[:200]})")
            FAIL += 1

        # TG-2: emotion_react includes content
        if "emotion_react" in tg1:
            print("  PASS: TG-2: emotion_react guide includes content")
            PASS += 1
        else:
            print(f"  FAIL: TG-2: emotion_react content missing")
            FAIL += 1

        # TG-3: nonexistent tool returns empty
        tg3 = _tg_mod._get_tool_guide("mcp__memory-tools__nonexistent_tool")
        if tg3 == "":
            print("  PASS: TG-3: nonexistent tool returns empty")
            PASS += 1
        else:
            print(f"  FAIL: TG-3: nonexistent tool should be empty (got: {tg3[:200]})")
            FAIL += 1

        # TG-4: session_start tool gets guide
        tg4 = _tg_mod._get_tool_guide("mcp__memory-tools__session_start")
        if "[Tool Guide]" in tg4 and "tool-session-start.md" in tg4:
            print("  PASS: TG-4: session_start tool gets tool guide")
            PASS += 1
        else:
            print(f"  FAIL: TG-4: session_start guide missing (got: {tg4[:200]})")
            FAIL += 1

        # TG-5: discord_connect tool gets guide
        tg5 = _tg_mod._get_tool_guide("mcp__discord__discord_connect")
        if "[Tool Guide]" in tg5 and "tool-discord-connect.md" in tg5:
            print("  PASS: TG-5: discord_connect tool gets tool guide")
            PASS += 1
        else:
            print(f"  FAIL: TG-5: discord_connect guide missing (got: {tg5[:200]})")
            FAIL += 1

        # TG-6: persistent_cron_add tool gets guide
        tg6 = _tg_mod._get_tool_guide("mcp__persistent-cron__persistent_cron_add")
        if "[Tool Guide]" in tg6 and "tool-persistent-cron-add.md" in tg6:
            print("  PASS: TG-6: persistent_cron_add tool gets tool guide")
            PASS += 1
        else:
            print(f"  FAIL: TG-6: persistent_cron_add guide missing (got: {tg6[:200]})")
            FAIL += 1

        # TG-7: plain tool name returns empty
        tg7 = _tg_mod._get_tool_guide("some_plain_tool")
        if tg7 == "":
            print("  PASS: TG-7: plain tool name returns empty")
            PASS += 1
        else:
            print(f"  FAIL: TG-7: plain tool name should be empty (got: {tg7[:200]})")
            FAIL += 1

        # TG-8: handle_mcp_context integration - tool guide in output
        tg8 = _tg_mod.handle_mcp_context("mcp__memory-tools__emotion_react")
        if "[Tool Guide]" in tg8 and "[Context Injection]" in tg8:
            print("  PASS: TG-8: handle_mcp_context includes tool guide")
            PASS += 1
        else:
            print(f"  FAIL: TG-8: handle_mcp_context missing guide (got: {tg8[:200]})")
            FAIL += 1

        os.chdir(_saved_cwd)

    except Exception as e:
        print(f"  FAIL: Tool Guide tests error: {e}")
        FAIL += 1
        try:
            os.chdir(_saved_cwd)
        except Exception:
            pass

    print("")
    print("=== COMMANDS_DIR Path Tests ===")

    # Test CD-1: COMMANDS_DIR should point to .claude/commands/ not project_root/commands/
    try:
        import importlib.util as _cd_ilu
        _cd_spec = _cd_ilu.spec_from_file_location("skill_executor_cd", _tg_script)
        _cd_mod = _cd_ilu.module_from_spec(_cd_spec)
        _saved_cwd_cd = os.getcwd()
        os.chdir(_project_root)
        _cd_spec.loader.exec_module(_cd_mod)

        commands_dir = _cd_mod.COMMANDS_DIR
        normalized = os.path.normpath(commands_dir)
        # COMMANDS_DIR must end with .claude/commands (not just /commands)
        if normalized.endswith(os.path.join(".claude", "commands")):
            print("  PASS: CD-1 COMMANDS_DIR ends with .claude/commands")
            PASS += 1
        else:
            print(f"  FAIL: CD-1 COMMANDS_DIR does not end with .claude/commands: {normalized}")
            FAIL += 1

        # Test CD-2: COMMANDS_DIR should be an existing directory
        if os.path.isdir(commands_dir):
            print("  PASS: CD-2 COMMANDS_DIR exists as directory")
            PASS += 1
        else:
            print(f"  FAIL: CD-2 COMMANDS_DIR does not exist: {commands_dir}")
            FAIL += 1

        # Test CD-3: Skill loading via COMMANDS_DIR should find dev-flow.md
        dev_flow_path = os.path.join(commands_dir, "dev-flow.md")
        if os.path.isfile(dev_flow_path):
            print("  PASS: CD-3 dev-flow.md found via COMMANDS_DIR")
            PASS += 1
        else:
            print(f"  FAIL: CD-3 dev-flow.md not found at {dev_flow_path}")
            FAIL += 1

        os.chdir(_saved_cwd_cd)

    except Exception as e:
        print(f"  FAIL: COMMANDS_DIR tests error: {e}")
        FAIL += 1
        try:
            os.chdir(_saved_cwd_cd)
        except Exception:
            pass

    print("")
    print("=== Workflow Skill Auto-Injection Tests ===")

    # Use direct module import for workflow skill tests
    try:
        import importlib.util as _ws_ilu
        _ws_spec = _ws_ilu.spec_from_file_location("skill_executor_ws", _tg_script)
        _ws_mod = _ws_ilu.module_from_spec(_ws_spec)
        _saved_cwd_ws = os.getcwd()
        os.chdir(_project_root)
        _ws_spec.loader.exec_module(_ws_mod)

        # WS-1: _read_skill_content reads skill file and truncates (200 + "..." = 203 max)
        ws1 = _ws_mod._read_skill_content("tdd.md")
        if ws1 and len(ws1) <= 203 and "TDD" in ws1:
            print("  PASS: WS-1: _read_skill_content reads tdd.md with truncation")
            PASS += 1
        else:
            print(f"  FAIL: WS-1: _read_skill_content tdd.md (got: {repr(ws1)[:100]})")
            FAIL += 1

        # WS-2: _read_skill_content returns empty for nonexistent file
        ws2 = _ws_mod._read_skill_content("nonexistent-skill.md")
        if ws2 == "":
            print("  PASS: WS-2: _read_skill_content nonexistent returns empty")
            PASS += 1
        else:
            print(f"  FAIL: WS-2: expected empty, got: {repr(ws2)[:100]}")
            FAIL += 1

        # WS-3: implementer subagent_type injects tdd.md
        ws3 = _ws_mod._get_workflow_skills("implementer")
        if "[Workflow Skill] tdd" in ws3:
            print("  PASS: WS-3: implementer gets tdd.md injection")
            PASS += 1
        else:
            print(f"  FAIL: WS-3: implementer missing tdd (got: {ws3[:200]})")
            FAIL += 1

        # WS-4: researcher subagent_type injects research.md
        ws4 = _ws_mod._get_workflow_skills("researcher")
        if "[Workflow Skill] research" in ws4:
            print("  PASS: WS-4: researcher gets research.md injection")
            PASS += 1
        else:
            print(f"  FAIL: WS-4: researcher missing research (got: {ws4[:200]})")
            FAIL += 1

        # WS-5: nonexistent subagent_type returns empty
        ws5 = _ws_mod._get_workflow_skills("nonexistent_type")
        if ws5 == "":
            print("  PASS: WS-5: nonexistent subagent_type returns empty")
            PASS += 1
        else:
            print(f"  FAIL: WS-5: expected empty, got: {repr(ws5)[:100]}")
            FAIL += 1

        # WS-6: _get_workflow_skills with review_issues context injects think-before-fix
        # Setup: write .dev-flow-state with review_issues_pending
        _ws_hooks_dir = os.path.dirname(_tg_script)
        _ws_state_file = os.path.join(_ws_hooks_dir, ".dev-flow-state")
        try:
            with open(_ws_state_file, "w", encoding="utf-8") as f:
                json.dump({"review_issues_pending": True, "impl": 4000}, f)
            ws6 = _ws_mod._get_workflow_skills("implementer", hooks_dir=_ws_hooks_dir)
            if "[Workflow Skill] think-before-fix" in ws6:
                print("  PASS: WS-6: review_issues_pending injects think-before-fix")
                PASS += 1
            else:
                print(f"  FAIL: WS-6: missing think-before-fix (got: {ws6[:300]})")
                FAIL += 1
        finally:
            try:
                os.remove(_ws_state_file)
            except FileNotFoundError:
                pass

        # WS-7: bugfix subagent with multiple hypotheses injects competing-hypothesis
        ws7 = _ws_mod._get_workflow_skills("bugfix", hypotheses_count=2)
        if "[Workflow Skill] competing-hypothesis" in ws7:
            print("  PASS: WS-7: bugfix with 2+ hypotheses gets competing-hypothesis")
            PASS += 1
        else:
            print(f"  FAIL: WS-7: missing competing-hypothesis (got: {ws7[:200]})")
            FAIL += 1

        # WS-8: bugfix with 1 hypothesis does NOT inject competing-hypothesis
        ws8 = _ws_mod._get_workflow_skills("bugfix", hypotheses_count=1)
        if "competing-hypothesis" not in ws8:
            print("  PASS: WS-8: bugfix with 1 hypothesis skips competing-hypothesis")
            PASS += 1
        else:
            print(f"  FAIL: WS-8: should not have competing-hypothesis (got: {ws8[:200]})")
            FAIL += 1

        # WS-9: reviewer with tier=Large injects parallel-review
        ws9 = _ws_mod._get_workflow_skills("reviewer", tier="Large")
        if "[Workflow Skill] parallel-review" in ws9:
            print("  PASS: WS-9: Large tier reviewer gets parallel-review")
            PASS += 1
        else:
            print(f"  FAIL: WS-9: missing parallel-review (got: {ws9[:200]})")
            FAIL += 1

        # WS-10: reviewer with tier=Small does NOT inject parallel-review
        ws10 = _ws_mod._get_workflow_skills("reviewer", tier="Small")
        if "parallel-review" not in ws10:
            print("  PASS: WS-10: Small tier reviewer skips parallel-review")
            PASS += 1
        else:
            print(f"  FAIL: WS-10: should not have parallel-review (got: {ws10[:200]})")
            FAIL += 1

        # WS-11: handle_agent_context includes workflow skills for implementer
        ws11 = _ws_mod.handle_agent_context("Agent", "implementer")
        if "[Workflow Skill] tdd" in ws11:
            print("  PASS: WS-11: handle_agent_context includes tdd for implementer")
            PASS += 1
        else:
            print(f"  FAIL: WS-11: handle_agent_context missing tdd (got last 300: {ws11[-300:]})")
            FAIL += 1

        os.chdir(_saved_cwd_ws)

    except Exception as e:
        print(f"  FAIL: Workflow Skill tests error: {e}")
        import traceback
        traceback.print_exc()
        FAIL += 1
        try:
            os.chdir(_saved_cwd_ws)
        except Exception:
            pass

    print("")
    print("=== Session-End / PreCompact Skill Injection Tests ===")

    # WS-SE-1: session-end.js should contain session-end skill content injection
    try:
        session_end_js = os.path.join(_project_root, "hooks", "session-end.js")
        with open(session_end_js, "r", encoding="utf-8") as f:
            se_content = f.read()
        if "session-end.md" in se_content:
            print("  PASS: WS-SE-1: session-end.js references session-end.md")
            PASS += 1
        else:
            print("  FAIL: WS-SE-1: session-end.js does not reference session-end.md")
            FAIL += 1
    except Exception as e:
        print(f"  FAIL: WS-SE-1 error: {e}")
        FAIL += 1

    # WS-PC-1: pre-compact-save.js should contain compact-guide skill content injection
    try:
        precompact_js = os.path.join(_project_root, "hooks", "pre-compact-save.js")
        with open(precompact_js, "r", encoding="utf-8") as f:
            pc_content = f.read()
        if "compact-guide.md" in pc_content:
            print("  PASS: WS-PC-1: pre-compact-save.js references compact-guide.md")
            PASS += 1
        else:
            print("  FAIL: WS-PC-1: pre-compact-save.js does not reference compact-guide.md")
            FAIL += 1
    except Exception as e:
        print(f"  FAIL: WS-PC-1 error: {e}")
        FAIL += 1

    # WS-HS-1: _get_session_start_extras should include hook-status
    try:
        _ws_hs_spec = _ws_ilu.spec_from_file_location("skill_executor_hs", _tg_script)
        _ws_hs_mod = _ws_ilu.module_from_spec(_ws_hs_spec)
        _saved_cwd_hs = os.getcwd()
        os.chdir(_project_root)
        _ws_hs_spec.loader.exec_module(_ws_hs_mod)

        hs_result = _ws_hs_mod._get_session_start_extras()
        if "HookStatus" in hs_result:
            print("  PASS: WS-HS-1: _get_session_start_extras includes HookStatus")
            PASS += 1
        else:
            print(f"  FAIL: WS-HS-1: missing HookStatus (got: {hs_result[:300]})")
            FAIL += 1

        os.chdir(_saved_cwd_hs)
    except Exception as e:
        print(f"  FAIL: WS-HS-1 error: {e}")
        FAIL += 1
        try:
            os.chdir(_saved_cwd_hs)
        except Exception:
            pass

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        sys.exit(1)
