"""Tests for gap analysis integration in session_start."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path so we can import memory_mcp_server
sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_mcp_server


# --- Fixtures ---

@pytest.fixture
def docs_dir(tmp_path):
    """Provide a temporary docs directory."""
    d = tmp_path / "docs"
    d.mkdir()
    return str(d)


GAP_CONTENT_C14 = """\
# Gap Analysis -- Cycle 14 (2026-03-18)

## Current gaps

### G1: Input pathways
Need more input channels.
-> **Candidate: Discord relay**

### G2: Browser automation
Playwright MCP needed.
-> **Candidate: Playwright integration**
"""

GAP_CONTENT_C15 = """\
# Gap Analysis -- Cycle 15 (2026-03-21)

## Gaps

### G1: Discord botの応答品質と人格
Discord botが別インスタンスで応答する。
-> **Candidate: Bot personality integration**

### G2: Heartbeatの自律行動
Heartbeatインスタンスは報告するだけで行動しない。
-> **Candidate: Heartbeat action protocol**

### G3: 常駐プロセスの信頼性
Discordデーモンが12時間でGateway reconnect失敗。
-> **Candidate: Daemon monitoring**
"""

GAP_CONTENT_ONLY_HEADER = """\
# Gap Analysis -- Cycle 13 (2026-03-15)

## No real gaps found
All good.
"""


class TestExtractGaps:
    """Tests for _extract_gap_analysis function."""

    def test_gaps_extracted_from_file(self, docs_dir):
        """Gap analysis file present -> gaps listed in output."""
        filepath = os.path.join(docs_dir, "gap_analysis_c15_20260321.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_C15)

        result = memory_mcp_server._extract_gap_analysis(docs_dir)

        assert "=== Current Cycle Gaps ===" in result
        assert "gap_analysis_c15_20260321.md" in result
        assert "G1:" in result
        assert "Discord bot" in result
        assert "G2:" in result
        assert "Heartbeat" in result
        assert "G3:" in result
        assert "常駐プロセス" in result

    def test_no_gap_file(self, docs_dir):
        """No gap analysis file -> fallback message."""
        result = memory_mcp_server._extract_gap_analysis(docs_dir)
        assert "No gap analysis found" in result

    def test_latest_file_selected(self, docs_dir):
        """Multiple gap files -> latest (by sorted name) is chosen."""
        # Write older file
        path_c14 = os.path.join(docs_dir, "gap_analysis_c14_20260318.md")
        with open(path_c14, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_C14)

        # Write newer file
        path_c15 = os.path.join(docs_dir, "gap_analysis_c15_20260321.md")
        with open(path_c15, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_C15)

        result = memory_mcp_server._extract_gap_analysis(docs_dir)

        # Should pick c15, not c14
        assert "gap_analysis_c15_20260321.md" in result
        assert "G3:" in result  # Only in c15
        # Should NOT contain c14-only content
        assert "gap_analysis_c14_20260318.md" not in result

    def test_gap_line_parsing(self, docs_dir):
        """### G lines are parsed correctly to extract title."""
        filepath = os.path.join(docs_dir, "gap_analysis_c15_20260321.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_C15)

        result = memory_mcp_server._extract_gap_analysis(docs_dir)
        lines = result.strip().split("\n")

        # Find gap lines (indented with G number)
        gap_lines = [l.strip() for l in lines if l.strip().startswith("G")]
        assert len(gap_lines) == 3
        assert gap_lines[0] == "G1: Discord botの応答品質と人格"
        assert gap_lines[1] == "G2: Heartbeatの自律行動"
        assert gap_lines[2] == "G3: 常駐プロセスの信頼性"

    def test_file_with_no_gaps(self, docs_dir):
        """Gap file exists but has no ### G lines -> shows file but no gaps."""
        filepath = os.path.join(docs_dir, "gap_analysis_c13_20260315.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_ONLY_HEADER)

        result = memory_mcp_server._extract_gap_analysis(docs_dir)
        assert "=== Current Cycle Gaps ===" in result
        assert "gap_analysis_c13_20260315.md" in result
        # No G lines should appear
        gap_lines = [l for l in result.split("\n") if l.strip().startswith("G")]
        assert len(gap_lines) == 0

    def test_nonexistent_docs_dir(self):
        """Docs directory doesn't exist -> fallback message."""
        result = memory_mcp_server._extract_gap_analysis("/nonexistent/path/docs")
        assert "No gap analysis found" in result

    def test_unreadable_file_graceful(self, docs_dir):
        """File exists but can't be read -> fallback without crash."""
        filepath = os.path.join(docs_dir, "gap_analysis_c15_20260321.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(GAP_CONTENT_C15)

        # Make file unreadable (may not work on Windows, so we mock instead)
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = memory_mcp_server._extract_gap_analysis(docs_dir)
        # Should not crash - either fallback or partial output
        assert isinstance(result, str)
