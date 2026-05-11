#!/usr/bin/env python3
"""Tests for spontaneous_surfacing.py / activation_surface session-start memory briefing generation."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spontaneous_surfacing as ss


class TestTagNormalization(unittest.TestCase):
    """Test tag normalization consistency with topic_index.py."""

    def test_lowercase(self):
        self.assertEqual(ss._normalize_tag("Psyche/Emotion.py"), "psyche/emotion.py")

    def test_strip_whitespace(self):
        self.assertEqual(ss._normalize_tag("  hello  "), "hello")

    def test_backslash_to_forward_slash(self):
        self.assertEqual(ss._normalize_tag("psyche\\emotion.py"), "psyche/emotion.py")

    def test_combined_normalization(self):
        self.assertEqual(ss._normalize_tag("  Psyche\\Emotion.PY  "), "psyche/emotion.py")

    def test_empty_string(self):
        self.assertEqual(ss._normalize_tag(""), "")

    def test_already_normalized(self):
        self.assertEqual(ss._normalize_tag("psyche/emotion.py"), "psyche/emotion.py")


class TestExtractTags(unittest.TestCase):
    """Test tag extraction from environmental signals."""

    def test_extract_from_cwd(self):
        signals = {
            "cwd": "/home/user/SampleProject",
            "branch": None,
            "git_files": [],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("cyreneproject", tags)

    def test_extract_from_cwd_multiple_components(self):
        signals = {
            "cwd": "/home/user/my_project",
            "branch": None,
            "git_files": [],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("my_project", tags)
        self.assertIn("user/my_project", tags)
        self.assertIn("home/user/my_project", tags)

    def test_extract_from_git_branch(self):
        signals = {
            "cwd": "/home/user/project",
            "branch": "feature/memory-v2",
            "git_files": [],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("feature/memory-v2", tags)

    def test_extract_from_git_status(self):
        signals = {
            "cwd": "/home/user/project",
            "branch": None,
            "git_files": ["psyche/emotion.py", "tests/test_emotion.py"],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("psyche/emotion.py", tags)
        self.assertIn("tests/test_emotion.py", tags)

    def test_extract_from_recent_files(self):
        signals = {
            "cwd": "/home/user/project",
            "branch": None,
            "git_files": [],
            "recent_files": ["src/utils.py", "README.md"],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("src/utils.py", tags)
        self.assertIn("readme.md", tags)

    def test_extract_deduplicates(self):
        signals = {
            "cwd": "/home/user/project",
            "branch": None,
            "git_files": ["emotion.py"],
            "recent_files": ["emotion.py"],
        }
        tags = ss._extract_tags(signals)
        # Should appear only once
        count = tags.count("emotion.py")
        self.assertEqual(count, 1)

    def test_extract_empty_signals(self):
        signals = {
            "cwd": "",
            "branch": None,
            "git_files": [],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        # Should not contain empty strings
        self.assertNotIn("", tags)

    def test_backslash_paths_normalized(self):
        signals = {
            "cwd": "C:\\Users\\user\\Project",
            "branch": None,
            "git_files": ["psyche\\emotion.py"],
            "recent_files": [],
        }
        tags = ss._extract_tags(signals)
        self.assertIn("psyche/emotion.py", tags)
        # No backslash tags
        for tag in tags:
            self.assertNotIn("\\", tag)


class TestCollectSignals(unittest.TestCase):
    """Test environmental signal collection."""

    def test_collect_with_git(self):
        """Test signal collection when git is available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("# test", encoding="utf-8")

            with patch("spontaneous_surfacing.subprocess.run") as mock_run:
                # Mock git branch
                mock_branch = MagicMock()
                mock_branch.returncode = 0
                mock_branch.stdout = "main\n"

                # Mock git status
                mock_status = MagicMock()
                mock_status.returncode = 0
                mock_status.stdout = " M test.py\n?? new_file.py\n"

                mock_run.side_effect = [mock_branch, mock_status]

                signals = ss._collect_signals(tmpdir)

            self.assertEqual(signals["cwd"], tmpdir)
            self.assertEqual(signals["branch"], "main")
            self.assertIn("test.py", signals["git_files"])
            self.assertIn("new_file.py", signals["git_files"])

    def test_collect_without_git(self):
        """Test signal collection when git is not available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                signals = ss._collect_signals(tmpdir)

            self.assertEqual(signals["cwd"], tmpdir)
            self.assertIsNone(signals["branch"])
            self.assertEqual(signals["git_files"], [])

    def test_collect_not_a_repo(self):
        """Test signal collection when cwd is not a git repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("spontaneous_surfacing.subprocess.run") as mock_run:
                mock_result = MagicMock()
                mock_result.returncode = 128  # git error: not a repo
                mock_result.stdout = ""
                mock_run.return_value = mock_result

                signals = ss._collect_signals(tmpdir)

            self.assertIsNone(signals["branch"])

    def test_collect_recent_files(self):
        """Test that recently modified files are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with recognized extensions
            (Path(tmpdir) / "recent.py").write_text("# recent", encoding="utf-8")
            (Path(tmpdir) / "data.json").write_text("{}", encoding="utf-8")
            # Create a non-recognized extension
            (Path(tmpdir) / "image.png").write_bytes(b"\x89PNG")

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                signals = ss._collect_signals(tmpdir)

            recent = signals["recent_files"]
            self.assertTrue(any("recent.py" in f for f in recent))
            self.assertTrue(any("data.json" in f for f in recent))
            # .png should not be included
            self.assertFalse(any("image.png" in f for f in recent))

    def test_collect_recent_files_in_subdirs(self):
        """Test scanning one level into common subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "module.py").write_text("# module", encoding="utf-8")

            tests_dir = Path(tmpdir) / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_module.py").write_text("# test", encoding="utf-8")

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                signals = ss._collect_signals(tmpdir)

            recent = signals["recent_files"]
            self.assertTrue(any("module.py" in f for f in recent))
            self.assertTrue(any("test_module.py" in f for f in recent))


class _EpisodeTestBase(unittest.TestCase):
    """Base class providing helpers for creating test episode data."""

    def _create_memory_dir(self, tmpdir: str) -> str:
        """Create a memory directory structure."""
        memory_dir = os.path.join(tmpdir, "memory")
        episodes_dir = os.path.join(memory_dir, "episodes")
        os.makedirs(episodes_dir, exist_ok=True)
        return memory_dir

    def _write_session_file(
        self, memory_dir: str, session_id: str, episodes: list[dict]
    ) -> Path:
        """Write a session file with the given episodes."""
        episodes_dir = Path(memory_dir) / "episodes"
        session_data = {
            "session_id": session_id,
            "created_at": "2026-03-08T10:00:00Z",
            "episodes": episodes,
        }
        filepath = episodes_dir / f"{session_id}.json"
        filepath.write_text(json.dumps(session_data, ensure_ascii=False), encoding="utf-8")
        return filepath

    def _make_episode(
        self,
        episode_id: str = "ep001",
        episode_type: str = "decision",
        summary: str = "Test episode summary",
        tags: list[str] | None = None,
        timestamp: str = "2026-03-08T12:00:00Z",
        session_id: str = "session_20260308_100000",
    ) -> dict:
        """Create a test episode dict."""
        return {
            "episode_id": episode_id,
            "episode_type": episode_type,
            "summary": summary,
            "tags": tags or [],
            "timestamp": timestamp,
            "session_id": session_id,
            "user_utterances": [],
        }

    def _write_topic_index(self, memory_dir: str, index: dict) -> None:
        """Write a topic index file."""
        index_data = {
            "version": 1,
            "tag_count": len(index),
            "episode_count": sum(len(v) for v in index.values()),
            "index": index,
        }
        index_path = Path(memory_dir) / "topic_index.json"
        index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")


class TestRetrieveByTopics(_EpisodeTestBase):
    """Test topic-based retrieval."""

    def test_matching_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            # Write topic index with matching tags
            self._write_topic_index(memory_dir, {
                "psyche/emotion.py": [
                    {"episode_id": "ep001", "session_id": "session_1", "timestamp": "2026-03-08T12:00:00Z"},
                    {"episode_id": "ep002", "session_id": "session_1", "timestamp": "2026-03-08T13:00:00Z"},
                ],
                "orchestrator.py": [
                    {"episode_id": "ep003", "session_id": "session_1", "timestamp": "2026-03-08T14:00:00Z"},
                ],
            })

            results = ss._retrieve_by_topics(memory_dir, ["psyche/emotion.py"])
            self.assertEqual(len(results), 2)
            ep_ids = {r["episode_id"] for r in results}
            self.assertIn("ep001", ep_ids)
            self.assertIn("ep002", ep_ids)

    def test_no_matching_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_topic_index(memory_dir, {
                "psyche/emotion.py": [
                    {"episode_id": "ep001", "session_id": "session_1", "timestamp": "2026-03-08T12:00:00Z"},
                ],
            })

            results = ss._retrieve_by_topics(memory_dir, ["nonexistent/file.py"])
            self.assertEqual(len(results), 0)

    def test_topic_index_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            # No topic index file

            results = ss._retrieve_by_topics(memory_dir, ["anything"])
            self.assertEqual(len(results), 0)

    def test_multiple_matching_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_topic_index(memory_dir, {
                "tag_a": [
                    {"episode_id": "ep001", "session_id": "s1", "timestamp": "2026-03-08T12:00:00Z"},
                ],
                "tag_b": [
                    {"episode_id": "ep001", "session_id": "s1", "timestamp": "2026-03-08T12:00:00Z"},
                ],
            })

            results = ss._retrieve_by_topics(memory_dir, ["tag_a", "tag_b"])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["match_count"], 2)
            self.assertIn("tag_a", results[0]["matching_tags"])
            self.assertIn("tag_b", results[0]["matching_tags"])

    def test_results_sorted_by_match_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_topic_index(memory_dir, {
                "tag_a": [
                    {"episode_id": "ep001", "session_id": "s1", "timestamp": "2026-03-08T12:00:00Z"},
                    {"episode_id": "ep002", "session_id": "s1", "timestamp": "2026-03-08T13:00:00Z"},
                ],
                "tag_b": [
                    {"episode_id": "ep001", "session_id": "s1", "timestamp": "2026-03-08T12:00:00Z"},
                ],
            })

            results = ss._retrieve_by_topics(memory_dir, ["tag_a", "tag_b"])
            # ep001 matches both tags (count=2), ep002 matches one (count=1)
            self.assertEqual(results[0]["episode_id"], "ep001")
            self.assertEqual(results[0]["match_count"], 2)


class TestRetrieveByRecency(_EpisodeTestBase):
    """Test time-based retrieval."""

    def test_recent_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            ep1 = self._make_episode(episode_id="ep001", timestamp="2026-03-07T10:00:00Z")
            ep2 = self._make_episode(episode_id="ep002", timestamp="2026-03-08T10:00:00Z")
            ep3 = self._make_episode(episode_id="ep003", timestamp="2026-03-08T14:00:00Z")

            self._write_session_file(memory_dir, "session_20260307_100000", [ep1])
            import time
            time.sleep(0.05)  # Ensure different mtime
            self._write_session_file(memory_dir, "session_20260308_100000", [ep2, ep3])

            results = ss._retrieve_by_recency(memory_dir, n_sessions=1)
            ep_ids = {r.get("episode_id") for r in results}
            # Should only contain episodes from the most recent session
            self.assertIn("ep002", ep_ids)
            self.assertIn("ep003", ep_ids)
            self.assertNotIn("ep001", ep_ids)

    def test_all_sessions_when_n_exceeds_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            ep1 = self._make_episode(episode_id="ep001")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep1])

            results = ss._retrieve_by_recency(memory_dir, n_sessions=10)
            self.assertEqual(len(results), 1)

    def test_empty_episodes_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            # No session files

            results = ss._retrieve_by_recency(memory_dir, n_sessions=3)
            self.assertEqual(len(results), 0)

    def test_results_sorted_by_recency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            ep1 = self._make_episode(episode_id="ep_old", timestamp="2026-03-07T10:00:00Z")
            ep2 = self._make_episode(episode_id="ep_new", timestamp="2026-03-08T14:00:00Z")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep1, ep2])

            results = ss._retrieve_by_recency(memory_dir, n_sessions=1)
            # Most recent first
            self.assertEqual(results[0]["episode_id"], "ep_new")


class TestMergeResults(_EpisodeTestBase):
    """Test merging and deduplication of topic and time results."""

    def test_duplicate_removal(self):
        """Episodes found in both passes should only appear in topic results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            ep_shared = self._make_episode(episode_id="ep_shared", summary="Shared episode")
            ep_time_only = self._make_episode(episode_id="ep_time", summary="Time only")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep_shared, ep_time_only])

            topic_results = [{
                "episode_id": "ep_shared",
                "session_id": "session_20260308_100000",
                "timestamp": "2026-03-08T12:00:00Z",
                "match_count": 1,
                "matching_tags": ["tag_a"],
            }]
            time_results = [ep_shared, ep_time_only]

            topic_eps, time_eps = ss._merge_results(memory_dir, topic_results, time_results)

            # ep_shared should be in topic_eps only
            topic_ids = {ep[0]["episode_id"] for ep in topic_eps}
            time_ids = {ep["episode_id"] for ep in time_eps}

            self.assertIn("ep_shared", topic_ids)
            self.assertNotIn("ep_shared", time_ids)
            self.assertIn("ep_time", time_ids)

    def test_empty_both(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            topic_eps, time_eps = ss._merge_results(memory_dir, [], [])
            self.assertEqual(len(topic_eps), 0)
            self.assertEqual(len(time_eps), 0)


class TestFormatBriefing(unittest.TestCase):
    """Test briefing formatting."""

    def _make_ep(self, ep_id="ep001", ep_type="decision", summary="Summary text",
                 timestamp="2026-03-08T12:00:00Z"):
        return {
            "episode_id": ep_id,
            "episode_type": ep_type,
            "summary": summary,
            "timestamp": timestamp,
            "tags": [],
            "user_utterances": [],
        }

    def test_basic_format(self):
        topic_episodes = [
            (self._make_ep(ep_id="ep001"), ["tag_a"]),
        ]
        time_episodes = [
            self._make_ep(ep_id="ep002", summary="Recent thing"),
        ]
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch="main",
            topic_episodes=topic_episodes,
            time_episodes=time_episodes,
        )
        self.assertIn("# Memory Briefing", result)
        self.assertIn("/home/user/project", result)
        self.assertIn("Branch: main", result)
        self.assertIn("Relevant to Current Context", result)
        self.assertIn("Recent Activity", result)
        self.assertIn("Summary text", result)
        self.assertIn("tag_a", result)

    def test_no_branch(self):
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=[(self._make_ep(), ["tag"])],
            time_episodes=[],
        )
        self.assertNotIn("Branch:", result)

    def test_topic_only(self):
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=[(self._make_ep(), ["tag"])],
            time_episodes=[],
        )
        self.assertIn("Relevant to Current Context", result)
        self.assertNotIn("Recent Activity", result)

    def test_time_only(self):
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=[],
            time_episodes=[self._make_ep()],
        )
        self.assertNotIn("Relevant to Current Context", result)
        self.assertIn("Recent Activity", result)

    def test_size_cap_enforcement(self):
        """Briefing should be capped and show omission count."""
        topic_episodes = [
            (self._make_ep(ep_id=f"ep{i:03d}", summary="A" * 80), [f"tag_{i}"])
            for i in range(20)
        ]
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=topic_episodes,
            time_episodes=[],
            max_chars=500,
        )
        self.assertLessEqual(len(result), 800)  # Some overhead from footer
        self.assertIn("omitted", result)

    def test_summary_truncation(self):
        long_summary = "x" * 200
        ep = self._make_ep(summary=long_summary)
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=[(ep, ["tag"])],
            time_episodes=[],
        )
        self.assertIn("...", result)
        # Should not contain the full 200-char summary
        self.assertNotIn("x" * 200, result)

    def test_footer_format(self):
        result = ss._format_briefing(
            cwd="/home/user/project",
            branch=None,
            topic_episodes=[(self._make_ep(), ["tag"])],
            time_episodes=[],
        )
        self.assertIn("Total:", result)
        self.assertIn("Briefing capped at", result)


class TestGenerateBriefing(_EpisodeTestBase):
    """Test the main generate_briefing function."""

    def test_with_topic_and_time_results(self):
        """Briefing with both topic-matched and recent episodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir  # Use tmpdir as cwd

            ep1 = self._make_episode(
                episode_id="ep001", summary="Fixed emotion bug",
                tags=["psyche/emotion.py"], timestamp="2026-03-08T12:00:00Z",
                session_id="session_20260308_100000",
            )
            ep2 = self._make_episode(
                episode_id="ep002", summary="Recent work on memory",
                timestamp="2026-03-08T14:00:00Z",
                session_id="session_20260308_100000",
            )
            self._write_session_file(memory_dir, "session_20260308_100000", [ep1, ep2])

            # Create topic index matching a tag from the cwd
            project_name = Path(tmpdir).name.lower()
            self._write_topic_index(memory_dir, {
                project_name: [
                    {"episode_id": "ep001", "session_id": "session_20260308_100000",
                     "timestamp": "2026-03-08T12:00:00Z"},
                ],
            })

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertIn("# Memory Briefing", result)
            self.assertNotEqual(result, "No relevant memories found.")

    def test_with_recent_only_no_topic_index(self):
        """Briefing with recent episodes only (no topic index)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            ep1 = self._make_episode(episode_id="ep001", summary="Some work")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep1])

            # No topic index
            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertIn("# Memory Briefing", result)
            self.assertIn("Recent Activity", result)

    def test_no_episodes(self):
        """Briefing with no episodes at all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertEqual(result, "No relevant memories found.")

    def test_working_directory_not_exist(self):
        """Error when working directory does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            nonexistent = os.path.join(tmpdir, "nonexistent_dir")

            result = ss.generate_briefing(memory_dir, nonexistent)
            self.assertTrue(result.startswith("ERROR:"))
            self.assertIn("does not exist", result)

    def test_no_episodes_directory(self):
        """Return 'no memories' when episodes directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            # Don't create episodes subdirectory
            cwd = tmpdir

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertEqual(result, "No relevant memories found.")

    def test_corrupted_session_files_skipped(self):
        """Corrupted session files should be skipped silently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            # Write a valid session
            ep1 = self._make_episode(episode_id="ep001", summary="Valid episode")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep1])

            # Write a corrupted session
            corrupted_path = Path(memory_dir) / "episodes" / "session_20260307_100000.json"
            corrupted_path.write_text("not valid json{{{", encoding="utf-8")

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            # Should still produce a briefing with the valid session
            self.assertIn("# Memory Briefing", result)

    def test_max_chars_override(self):
        """Override the max chars parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            episodes = [
                self._make_episode(episode_id=f"ep{i:03d}", summary="A" * 80)
                for i in range(20)
            ]
            self._write_session_file(memory_dir, "session_20260308_100000", episodes)

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd, max_chars=300)

            self.assertIn("omitted", result)

    def test_recent_sessions_override(self):
        """Override the recent sessions parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            ep1 = self._make_episode(episode_id="ep001", summary="Old session")
            self._write_session_file(memory_dir, "session_20260306_100000", [ep1])

            import time
            time.sleep(0.05)

            ep2 = self._make_episode(episode_id="ep002", summary="New session")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep2])

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                # Only 1 recent session
                result = ss.generate_briefing(memory_dir, cwd, recent_sessions=1)

            # Should contain ep002 but not ep001
            self.assertIn("New session", result)


class TestUTF8Content(_EpisodeTestBase):
    """Test UTF-8 content handling in episode data and file paths."""

    def test_utf8_episode_summary(self):
        """Episodes with UTF-8 content should be handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir

            ep = self._make_episode(
                episode_id="ep_utf8",
                summary="日本語のサマリー テスト",
            )
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertIn("日本語のサマリー", result)

    def test_utf8_tags_in_index(self):
        """Topic index with UTF-8 tags should work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            ep = self._make_episode(episode_id="ep001", summary="test")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            self._write_topic_index(memory_dir, {
                "テスト": [
                    {"episode_id": "ep001", "session_id": "session_20260308_100000",
                     "timestamp": "2026-03-08T12:00:00Z"},
                ],
            })

            results = ss._retrieve_by_topics(memory_dir, ["テスト"])
            self.assertEqual(len(results), 1)


class TestSessionFileReading(_EpisodeTestBase):
    """Test session file reading utilities."""

    def test_load_valid_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            ep = self._make_episode()
            filepath = self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            data = ss._load_session_file(filepath)
            self.assertIsNotNone(data)
            self.assertEqual(data["session_id"], "session_20260308_100000")
            self.assertEqual(len(data["episodes"]), 1)

    def test_load_corrupted_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            filepath = Path(memory_dir) / "episodes" / "session_bad.json"
            filepath.write_text("not json", encoding="utf-8")

            data = ss._load_session_file(filepath)
            self.assertIsNone(data)

    def test_load_invalid_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            filepath = Path(memory_dir) / "episodes" / "session_invalid.json"
            filepath.write_text('{"key": "value"}', encoding="utf-8")

            data = ss._load_session_file(filepath)
            self.assertIsNone(data)

    def test_list_session_files_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            episodes_dir = Path(memory_dir) / "episodes"

            import time
            # Create files with different mtimes
            (episodes_dir / "session_20260307_100000.json").write_text(
                json.dumps({"session_id": "s1", "episodes": []}), encoding="utf-8"
            )
            time.sleep(0.05)
            (episodes_dir / "session_20260308_100000.json").write_text(
                json.dumps({"session_id": "s2", "episodes": []}), encoding="utf-8"
            )

            files = ss._list_session_files(episodes_dir)
            self.assertEqual(len(files), 2)
            # Oldest first
            self.assertIn("20260307", files[0].name)
            self.assertIn("20260308", files[1].name)

    def test_list_session_files_nonexistent_dir(self):
        files = ss._list_session_files(Path("/nonexistent/path"))
        self.assertEqual(files, [])


class TestTopicIndex(_EpisodeTestBase):
    """Test topic index loading."""

    def test_load_valid_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_topic_index(memory_dir, {
                "tag1": [{"episode_id": "ep001", "session_id": "s1", "timestamp": "t1"}]
            })

            data = ss._load_topic_index(memory_dir)
            self.assertIsNotNone(data)
            self.assertIn("index", data)

    def test_load_missing_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            # No index file

            data = ss._load_topic_index(memory_dir)
            self.assertIsNone(data)

    def test_load_corrupted_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            index_path = Path(memory_dir) / "topic_index.json"
            index_path.write_text("corrupted{{{", encoding="utf-8")

            data = ss._load_topic_index(memory_dir)
            self.assertIsNone(data)


class TestCLI(unittest.TestCase):
    """Test CLI subcommand parsing and output."""

    def test_briefing_command(self):
        """Test CLI briefing subcommand."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(os.path.join(memory_dir, "episodes"), exist_ok=True)

            # Write a session file
            session_data = {
                "session_id": "session_20260308_100000",
                "created_at": "2026-03-08T10:00:00Z",
                "episodes": [{
                    "episode_id": "ep001",
                    "episode_type": "decision",
                    "summary": "Test episode",
                    "tags": [],
                    "timestamp": "2026-03-08T12:00:00Z",
                    "session_id": "session_20260308_100000",
                    "user_utterances": [],
                }],
            }
            session_path = Path(memory_dir) / "episodes" / "session_20260308_100000.json"
            session_path.write_text(json.dumps(session_data), encoding="utf-8")

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                with patch("sys.stdout") as mock_stdout:
                    # Capture print output
                    printed = []
                    mock_stdout.write = lambda x: printed.append(x)

                    ss.main([
                        "briefing",
                        "--memory-dir", memory_dir,
                        "--cwd", tmpdir,
                    ])

    def test_no_command(self):
        """Test that no command prints help and exits."""
        with self.assertRaises(SystemExit) as cm:
            ss.main([])
        self.assertEqual(cm.exception.code, 1)

    def test_briefing_error_exits_with_code_1(self):
        """Test that errors exit with code 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            nonexistent_cwd = os.path.join(tmpdir, "nonexistent")

            with self.assertRaises(SystemExit) as cm:
                ss.main([
                    "briefing",
                    "--memory-dir", memory_dir,
                    "--cwd", nonexistent_cwd,
                ])
            self.assertEqual(cm.exception.code, 1)

    def test_max_chars_cli_option(self):
        """Test --max-chars CLI option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(os.path.join(memory_dir, "episodes"), exist_ok=True)

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                # Should not raise
                ss.main([
                    "briefing",
                    "--memory-dir", memory_dir,
                    "--cwd", tmpdir,
                    "--max-chars", "2000",
                ])

    def test_recent_sessions_cli_option(self):
        """Test --recent-sessions CLI option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(os.path.join(memory_dir, "episodes"), exist_ok=True)

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                ss.main([
                    "briefing",
                    "--memory-dir", memory_dir,
                    "--cwd", tmpdir,
                    "--recent-sessions", "5",
                ])


class TestTruncateSummary(unittest.TestCase):
    """Test summary truncation."""

    def test_short_summary_unchanged(self):
        result = ss._truncate_summary("short", 100)
        self.assertEqual(result, "short")

    def test_exact_length_unchanged(self):
        text = "x" * 100
        result = ss._truncate_summary(text, 100)
        self.assertEqual(result, text)

    def test_long_summary_truncated(self):
        text = "x" * 200
        result = ss._truncate_summary(text, 100)
        self.assertEqual(len(result), 103)  # 100 + "..."
        self.assertTrue(result.endswith("..."))


class TestLoadEpisodeById(_EpisodeTestBase):
    """Test loading specific episodes by ID."""

    def test_load_from_specified_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            ep = self._make_episode(episode_id="ep001", summary="Target")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            result = ss._load_episode_by_id(memory_dir, "ep001", "session_20260308_100000")
            self.assertIsNotNone(result)
            self.assertEqual(result["episode_id"], "ep001")

    def test_load_fallback_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            ep = self._make_episode(episode_id="ep001", summary="Target")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            # Wrong session_id, should fall back to full search
            result = ss._load_episode_by_id(memory_dir, "ep001", "wrong_session")
            self.assertIsNotNone(result)
            self.assertEqual(result["episode_id"], "ep001")

    def test_load_nonexistent_episode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            ep = self._make_episode(episode_id="ep001")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            result = ss._load_episode_by_id(memory_dir, "nonexistent", "")
            self.assertIsNone(result)


class TestGitStatusParsing(unittest.TestCase):
    """Test git status porcelain output parsing."""

    def test_modified_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("spontaneous_surfacing.subprocess.run") as mock_run:
                mock_branch = MagicMock()
                mock_branch.returncode = 0
                mock_branch.stdout = "main\n"

                mock_status = MagicMock()
                mock_status.returncode = 0
                mock_status.stdout = " M file1.py\nMM file2.py\n?? file3.py\nA  file4.py\n"

                mock_run.side_effect = [mock_branch, mock_status]
                signals = ss._collect_signals(tmpdir)

            self.assertIn("file1.py", signals["git_files"])
            self.assertIn("file2.py", signals["git_files"])
            self.assertIn("file3.py", signals["git_files"])
            self.assertIn("file4.py", signals["git_files"])

    def test_renamed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("spontaneous_surfacing.subprocess.run") as mock_run:
                mock_branch = MagicMock()
                mock_branch.returncode = 0
                mock_branch.stdout = "main\n"

                mock_status = MagicMock()
                mock_status.returncode = 0
                mock_status.stdout = "R  old_name.py -> new_name.py\n"

                mock_run.side_effect = [mock_branch, mock_status]
                signals = ss._collect_signals(tmpdir)

            self.assertIn("new_name.py", signals["git_files"])


class TestScanDirForRecent(unittest.TestCase):
    """Test directory scanning for recent files."""

    def test_only_recognized_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "code.py").write_text("# python", encoding="utf-8")
            (base / "data.json").write_text("{}", encoding="utf-8")
            (base / "image.png").write_bytes(b"\x89PNG")
            (base / "binary.exe").write_bytes(b"\x00")

            results = []
            cutoff = 0  # Include all files
            ss._scan_dir_for_recent(base, base, cutoff, results)

            paths = [r[0] for r in results]
            self.assertTrue(any("code.py" in p for p in paths))
            self.assertTrue(any("data.json" in p for p in paths))
            self.assertFalse(any("image.png" in p for p in paths))
            self.assertFalse(any("binary.exe" in p for p in paths))

    def test_cutoff_filter(self):
        """Files older than cutoff should be excluded."""
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            old_file = base / "old.py"
            old_file.write_text("# old", encoding="utf-8")

            # Set cutoff to future to exclude all files
            cutoff = time.time() + 3600

            results = []
            ss._scan_dir_for_recent(base, base, cutoff, results)
            self.assertEqual(len(results), 0)


class TestEdgeCases(_EpisodeTestBase):
    """Test various edge cases."""

    def test_empty_topic_index(self):
        """Topic index exists but has no entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_topic_index(memory_dir, {})

            results = ss._retrieve_by_topics(memory_dir, ["anything"])
            self.assertEqual(len(results), 0)

    def test_session_with_no_episodes(self):
        """Session file exists but contains no episodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir
            self._write_session_file(memory_dir, "session_20260308_100000", [])

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            self.assertEqual(result, "No relevant memories found.")

    def test_episode_with_empty_summary(self):
        """Episodes with empty summary should still work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            cwd = tmpdir
            ep = self._make_episode(episode_id="ep001", summary="")
            self._write_session_file(memory_dir, "session_20260308_100000", [ep])

            with patch("spontaneous_surfacing.subprocess.run", side_effect=FileNotFoundError):
                result = ss.generate_briefing(memory_dir, cwd)

            # Should still produce output without errors
            self.assertIn("# Memory Briefing", result)

    def test_max_topic_results_limit(self):
        """Topic results should be capped at MAX_TOPIC_RESULTS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)

            # Create many entries under one tag
            refs = [
                {"episode_id": f"ep{i:03d}", "session_id": "s1", "timestamp": f"2026-03-08T{i:02d}:00:00Z"}
                for i in range(20)
            ]
            self._write_topic_index(memory_dir, {"tag_a": refs})

            results = ss._retrieve_by_topics(memory_dir, ["tag_a"])
            self.assertLessEqual(len(results), ss.MAX_TOPIC_RESULTS)


class TestContextualActivation(unittest.TestCase):
    """Tests for Attention Residual: context-aware activation surfacing.

    Tests the surface() function in activation_surface.py (not spontaneous_surfacing.py).
    """

    def _create_memory_dir(self, tmpdir):
        memory_dir = os.path.join(tmpdir, "memory")
        os.makedirs(os.path.join(memory_dir, "episodes"), exist_ok=True)
        # Create empty emotion files to avoid load errors
        for fname in ["emotion_state.json", "emotion_change_log.json"]:
            with open(os.path.join(memory_dir, fname), "w") as f:
                json.dump({}, f)
        return memory_dir

    def _write_episode(self, memory_dir, ep):
        """Write episode inside a session file (format expected by _load_all_episodes)."""
        session_id = ep.get("session_id", "session_20260318_000000")
        # Ensure session_id starts with "session_" for file naming
        if not session_id.startswith("session_"):
            session_id = f"session_{session_id}"
        session_file = os.path.join(memory_dir, "episodes", f"{session_id}.json")

        # Load existing session file or create new
        episodes = []
        if os.path.exists(session_file):
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                episodes = data.get("episodes", [])

        episodes.append(ep)
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump({"session_id": session_id, "episodes": episodes}, f, ensure_ascii=False)

    def _write_session_context(self, memory_dir):
        with open(os.path.join(memory_dir, "session_context.md"), "w") as f:
            f.write("# Session Context\n")

    def test_surface_accepts_context_parameter(self):
        """surface() should accept an optional context parameter."""
        import activation_surface as surf
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_session_context(memory_dir)
            result = surf.surface(memory_dir, context="バグ修正 TDD")
            self.assertIsInstance(result, str)

    def test_context_none_behaves_as_before(self):
        """surface(context=None) should produce same output as surface() without context."""
        import activation_surface as surf
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_session_context(memory_dir)
            result_no_ctx = surf.surface(memory_dir)
            result_none_ctx = surf.surface(memory_dir, context=None)
            self.assertEqual(result_no_ctx, result_none_ctx)

    def test_context_produces_context_relevant_candidates(self):
        """When context is given, candidates related to that context should appear."""
        import activation_surface as surf
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_session_context(memory_dir)
            ep = {
                "episode_id": "ep_tdd",
                "episode_type": "feedback",
                "summary": "TDDでテストを先に書くべきだった。プロセスは作業の一部。",
                "tags": ["tdd", "process"],
                "timestamp": "2026-03-18T00:00:00Z",
                "session_id": "s1",
            }
            self._write_episode(memory_dir, ep)

            result = surf.surface(memory_dir, context="TDD テスト駆動開発")
            self.assertTrue(
                "context_relevant" in result or "tdd" in result.lower() or "TDD" in result,
                f"Context-relevant candidate not found in: {result}"
            )

    def test_context_candidates_have_correct_type(self):
        """Context-derived candidates should have type 'context_relevant'."""
        import activation_surface as surf
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = self._create_memory_dir(tmpdir)
            self._write_session_context(memory_dir)
            ep = {
                "episode_id": "ep_memory",
                "episode_type": "observation",
                "summary": "記憶システムの改善が必要。Attention Residualパターンを導入。",
                "tags": ["memory", "attention-residual"],
                "timestamp": "2026-03-18T00:00:00Z",
                "session_id": "s1",
            }
            self._write_episode(memory_dir, ep)

            result = surf.surface(memory_dir, context="記憶 Attention Residual")
            self.assertIn("context_relevant", result)


if __name__ == "__main__":
    unittest.main()
