#!/usr/bin/env python3
"""Tests for dynamic_read_verification.py"""

import json
import math
import os
import sys
import tempfile
import unittest

# Ensure the tools directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dynamic_read_verification import (
    parse_sections,
    extract_values,
    extract_value_pairs,
    determine_question_counts,
    generate_questions,
    format_questions,
    save_expected_values,
    load_expected_values,
    normalize_text,
    extract_numeric,
    check_answer,
    parse_answers,
    verify_answers,
    resolve_file_paths,
    run_verification,
    run_verify,
    _generate_a_question,
    _generate_b_question,
    _generate_c_question,
    _common_keywords,
    _filter_ambiguous_pairs,
)


# ---------------------------------------------------------------------------
# Section parsing tests
# ---------------------------------------------------------------------------

class TestParseSections(unittest.TestCase):
    def test_basic_sections(self):
        content = "## Section One\nSome text here.\n\n## Section Two\nMore text."
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["heading"], "Section One")
        self.assertEqual(sections[1]["heading"], "Section Two")

    def test_h1_headings(self):
        content = "# Top Level\nIntro.\n\n## Sub Section\nDetails."
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["heading"], "Top Level")
        self.assertEqual(sections[1]["heading"], "Sub Section")

    def test_no_headings(self):
        content = "Just plain text\nwith no headings."
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 1)
        self.assertIn("test.md", sections[0]["heading"])

    def test_empty_content(self):
        sections = parse_sections("", "test.md")
        self.assertEqual(len(sections), 1)

    def test_section_body_content(self):
        content = "## My Section\nLine 1\nLine 2\n\n## Next\nLine 3"
        sections = parse_sections(content, "test.md")
        self.assertIn("Line 1", sections[0]["body"])
        self.assertIn("Line 2", sections[0]["body"])
        self.assertIn("Line 3", sections[1]["body"])

    def test_line_numbers(self):
        content = "## First\nA\n\n## Second\nB"
        sections = parse_sections(content, "test.md")
        self.assertEqual(sections[0]["start_line"], 1)
        self.assertEqual(sections[1]["start_line"], 4)

    def test_filename_preserved(self):
        sections = parse_sections("## X\ntext", "/path/to/file.md")
        self.assertEqual(sections[0]["filename"], "/path/to/file.md")

    def test_value_pairs_in_sections(self):
        content = "## Stats\n| Module | Lines |\n| --- | --- |\n| brain.py | 941 |"
        sections = parse_sections(content, "test.md")
        self.assertIn("value_pairs", sections[0])
        self.assertTrue(len(sections[0]["value_pairs"]) >= 1)

    def test_h3_not_treated_as_separator(self):
        content = "## Main\nText\n\n### Sub\nMore text\n\n## Another\nEnd"
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 2)
        self.assertIn("### Sub", sections[0]["body"])


# ---------------------------------------------------------------------------
# Value extraction tests
# ---------------------------------------------------------------------------

class TestExtractValues(unittest.TestCase):
    def test_table_values(self):
        text = "| Name | Value |\n| --- | --- |\n| alpha | 100 |"
        values = extract_values(text)
        # Values are derived from pairs, so both sides appear
        cell_values = [v for v in values if v in ("Name", "Value", "alpha", "100")]
        self.assertTrue(len(cell_values) >= 2)

    def test_bullet_with_colon(self):
        text = "- **Total tests**: 12,010\n- **Total lines**: ~261,000行"
        values = extract_values(text)
        self.assertTrue(len(values) >= 2)

    def test_empty_text(self):
        values = extract_values("")
        self.assertEqual(values, [])

    def test_no_duplicates(self):
        text = "| alpha | 100 |\n| alpha | 100 |"
        values = extract_values(text)
        alpha_count = sum(1 for v in values if v == "alpha")
        self.assertEqual(alpha_count, 1)

    def test_too_short_values_excluded(self):
        # Single-char values should not appear
        text = "| x | y |"
        values = extract_values(text)
        single_chars = [v for v in values if len(v) == 1]
        self.assertEqual(len(single_chars), 0)

    def test_too_long_values_excluded(self):
        long_val = "x" * 201
        text = f"| {long_val} | something |"
        values = extract_values(text)
        self.assertEqual(len([v for v in values if len(v) > 200]), 0)


class TestExtractValuePairs(unittest.TestCase):
    def test_table_pairs(self):
        text = "| Name | Value |\n| --- | --- |\n| alpha | 100 |"
        pairs = extract_value_pairs(text)
        # Should have pairs from table rows
        self.assertTrue(len(pairs) >= 1)
        # alpha-100 pair should exist
        pair_strs = [(k, v) for k, v in pairs]
        self.assertTrue(
            any(("alpha" in k and "100" in v) or ("100" in k and "alpha" in v) for k, v in pair_strs),
            f"Expected alpha-100 pair in {pair_strs}"
        )

    def test_bullet_colon_pairs(self):
        text = "- **Total tests**: 12,010\n- **Lines**: ~261,000行"
        pairs = extract_value_pairs(text)
        self.assertTrue(len(pairs) >= 2)
        keys = [k for k, v in pairs]
        self.assertTrue(any("Total tests" in k for k in keys))

    def test_parenthetical_pairs(self):
        text = "vision (393行) and brain (941行)"
        pairs = extract_value_pairs(text)
        self.assertTrue(len(pairs) >= 2)
        pair_dict = dict(pairs)
        self.assertIn("vision", pair_dict)
        self.assertEqual(pair_dict["vision"], "393行")

    def test_backtick_numeric_pairs(self):
        text = "`brain.py` has 941行 of code"
        pairs = extract_value_pairs(text)
        self.assertTrue(len(pairs) >= 1)
        self.assertTrue(any("brain.py" in k for k, v in pairs))

    def test_key_value_colon_pairs(self):
        text = "総テスト数: 12,010"
        pairs = extract_value_pairs(text)
        self.assertTrue(len(pairs) >= 1)

    def test_empty_text(self):
        pairs = extract_value_pairs("")
        self.assertEqual(pairs, [])

    def test_no_self_pairs(self):
        """Key and value should not be identical."""
        text = "| same | same |"
        pairs = extract_value_pairs(text)
        for k, v in pairs:
            self.assertNotEqual(k, v)

    def test_complex_table(self):
        text = "| # | モジュール | 行数 |\n| --- | --- | --- |\n| 1 | self_model.py | 1,601 |"
        pairs = extract_value_pairs(text)
        # Should pair module name with line count
        self.assertTrue(
            any("self_model.py" in k or "self_model.py" in v for k, v in pairs),
            f"Expected self_model.py in pairs: {pairs}"
        )


# ---------------------------------------------------------------------------
# Question count tests
# ---------------------------------------------------------------------------

class TestDetermineQuestionCounts(unittest.TestCase):
    def test_minimum_clamp(self):
        counts = determine_question_counts(1)
        self.assertEqual(counts["total"], 3)

    def test_maximum_clamp(self):
        counts = determine_question_counts(100)
        self.assertEqual(counts["total"], 15)

    def test_normal_count(self):
        # 20 sections -> ceil(20*0.3) = 6
        counts = determine_question_counts(20)
        self.assertEqual(counts["total"], 6)

    def test_distribution(self):
        counts = determine_question_counts(30)
        total = counts["total"]
        self.assertEqual(counts["A"] + counts["B"] + counts["C"], total)
        # A should be the majority
        self.assertGreater(counts["A"], counts["B"])
        self.assertGreater(counts["A"], counts["C"])

    def test_ceil_behavior(self):
        # 7 sections -> ceil(7*0.3) = ceil(2.1) = 3
        counts = determine_question_counts(7)
        self.assertEqual(counts["total"], 3)


# ---------------------------------------------------------------------------
# Question generation tests
# ---------------------------------------------------------------------------

class TestGenerateQuestions(unittest.TestCase):
    def _make_sections(self, n, filename="test.md"):
        sections = []
        for i in range(n):
            sections.append({
                "heading": f"Section {i}",
                "body": f"Content {i} with value {i * 100}",
                "values": [f"key_{i}", f"{i * 100}"],
                "value_pairs": [(f"key_{i}", f"{i * 100}")],
                "start_line": i * 10 + 1,
                "end_line": (i + 1) * 10,
                "filename": filename,
            })
        return sections

    def test_generates_questions(self):
        sections = self._make_sections(10)
        questions = generate_questions(sections)
        self.assertGreater(len(questions), 0)

    def test_question_numbers_sequential(self):
        sections = self._make_sections(10)
        questions = generate_questions(sections)
        numbers = [q["number"] for q in questions]
        self.assertEqual(numbers, list(range(1, len(questions) + 1)))

    def test_empty_sections(self):
        questions = generate_questions([])
        self.assertEqual(questions, [])

    def test_sections_without_values(self):
        sections = [{
            "heading": "Empty",
            "body": "",
            "values": [],
            "start_line": 1,
            "end_line": 2,
            "filename": "test.md",
        }]
        questions = generate_questions(sections)
        self.assertEqual(questions, [])

    def test_category_a_has_expected_value(self):
        sections = self._make_sections(10)
        questions = generate_questions(sections)
        for q in questions:
            if q["category"] == "A":
                self.assertTrue(q["auto_grade"])
                self.assertTrue(len(q["expected_value"]) > 0)

    def test_category_bc_no_auto_grade(self):
        sections = self._make_sections(10, "file1.md")
        sections += self._make_sections(10, "file2.md")
        questions = generate_questions(sections)
        for q in questions:
            if q["category"] in ("B", "C"):
                self.assertFalse(q["auto_grade"])

    def test_min_questions(self):
        sections = self._make_sections(5)
        questions = generate_questions(sections)
        self.assertGreaterEqual(len(questions), 3)

    def test_max_questions(self):
        sections = self._make_sections(100)
        questions = generate_questions(sections)
        self.assertLessEqual(len(questions), 15)

    def test_multi_file_generates_c_questions(self):
        sections = self._make_sections(15, "file_alpha.md")
        sections += self._make_sections(15, "file_beta.md")
        # Run multiple times to account for randomness
        found_c = False
        for _ in range(20):
            questions = generate_questions(sections)
            if any(q["category"] == "C" for q in questions):
                found_c = True
                break
        self.assertTrue(found_c, "Expected at least one Category C question across 20 attempts")


# ---------------------------------------------------------------------------
# Individual question generator tests
# ---------------------------------------------------------------------------

class TestFilterAmbiguousPairs(unittest.TestCase):
    def test_all_unique(self):
        pairs = [("a", "1"), ("b", "2"), ("c", "3")]
        result = _filter_ambiguous_pairs(pairs)
        self.assertEqual(len(result), 3)

    def test_duplicate_key(self):
        """Same key with different values: both should be excluded."""
        pairs = [("scope", "LOCAL"), ("scope", "GLOBAL"), ("other", "123")]
        result = _filter_ambiguous_pairs(pairs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("other", "123"))

    def test_duplicate_value(self):
        """Same value with different keys: both should be excluded."""
        pairs = [("alpha", "LOCAL"), ("beta", "LOCAL"), ("gamma", "REMOTE")]
        result = _filter_ambiguous_pairs(pairs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("gamma", "REMOTE"))

    def test_all_ambiguous(self):
        """All pairs are ambiguous: return empty list."""
        pairs = [("a", "X"), ("b", "X"), ("a", "Y")]
        result = _filter_ambiguous_pairs(pairs)
        self.assertEqual(len(result), 0)

    def test_empty_pairs(self):
        result = _filter_ambiguous_pairs([])
        self.assertEqual(result, [])

    def test_single_pair(self):
        pairs = [("key", "val")]
        result = _filter_ambiguous_pairs(pairs)
        self.assertEqual(len(result), 1)

    def test_generate_a_skips_ambiguous(self):
        """_generate_a_question should return None if all pairs are ambiguous."""
        sec = {
            "heading": "Config",
            "body": "| scope | type |\n| LOCAL | read |\n| LOCAL | write |",
            "values": ["LOCAL", "read", "write"],
            "value_pairs": [("LOCAL", "read"), ("LOCAL", "write")],
            "start_line": 1,
            "end_line": 5,
            "filename": "test.md",
        }
        q = _generate_a_question(sec)
        self.assertIsNone(q)


class TestGenerateAQuestion(unittest.TestCase):
    def test_basic(self):
        sec = {
            "heading": "Stats",
            "body": "Tests: 12010",
            "values": ["Tests", "12010"],
            "value_pairs": [("Tests", "12010")],
            "start_line": 1,
            "end_line": 5,
            "filename": "arch.md",
        }
        q = _generate_a_question(sec)
        self.assertIsNotNone(q)
        self.assertEqual(q["category"], "A")
        self.assertTrue(q["auto_grade"])
        # Expected value should be one side of the pair
        self.assertIn(q["expected_value"], ["Tests", "12010"])

    def test_answer_not_in_question(self):
        """The expected value should NOT appear in the question text."""
        sec = {
            "heading": "Stats",
            "body": "module: self_model.py, lines: 1601",
            "values": ["self_model.py", "1601"],
            "value_pairs": [("self_model.py", "1601")],
            "start_line": 1,
            "end_line": 5,
            "filename": "arch.md",
        }
        # Run multiple times to cover both directions
        for _ in range(50):
            q = _generate_a_question(sec)
            self.assertIsNotNone(q)
            expected = q["expected_value"]
            question = q["question"]
            # The expected_value must NOT appear as-is in the question
            self.assertNotIn(
                expected, question,
                f"Expected value '{expected}' found in question: '{question}'"
            )

    def test_no_value_pairs(self):
        sec = {
            "heading": "Empty",
            "body": "",
            "values": [],
            "value_pairs": [],
            "start_line": 1,
            "end_line": 2,
            "filename": "test.md",
        }
        q = _generate_a_question(sec)
        self.assertIsNone(q)

    def test_no_value_pairs_key_missing(self):
        """Section without value_pairs key should return None."""
        sec = {
            "heading": "Empty",
            "body": "",
            "values": ["something"],
            "start_line": 1,
            "end_line": 2,
            "filename": "test.md",
        }
        q = _generate_a_question(sec)
        self.assertIsNone(q)


class TestGenerateBQuestion(unittest.TestCase):
    def test_basic(self):
        sa = {"heading": "Sec A", "filename": "f.md", "body": "", "values": []}
        sb = {"heading": "Sec B", "filename": "f.md", "body": "", "values": []}
        q = _generate_b_question(sa, sb)
        self.assertIsNotNone(q)
        self.assertEqual(q["category"], "B")
        self.assertFalse(q["auto_grade"])


class TestGenerateCQuestion(unittest.TestCase):
    def test_basic(self):
        sx = {"heading": "Alpha", "filename": "x.md", "body": "", "values": []}
        sy = {"heading": "Beta", "filename": "y.md", "body": "", "values": []}
        q = _generate_c_question(sx, sy)
        self.assertIsNotNone(q)
        self.assertEqual(q["category"], "C")
        self.assertFalse(q["auto_grade"])


class TestCommonKeywords(unittest.TestCase):
    def test_finds_common(self):
        sa = {"heading": "Orchestrator", "body": "The orchestrator handles processing"}
        sb = {"heading": "Pipeline", "body": "The orchestrator runs the pipeline"}
        common = _common_keywords(sa, sb)
        self.assertIn("orchestrator", common)

    def test_no_common(self):
        sa = {"heading": "Alpha", "body": "completely different text"}
        sb = {"heading": "Beta", "body": "nothing similar here"}
        common = _common_keywords(sa, sb)
        # Short words (<4 chars) are excluded, so there may or may not be overlap
        # The point is it doesn't crash


# ---------------------------------------------------------------------------
# Format questions tests
# ---------------------------------------------------------------------------

class TestFormatQuestions(unittest.TestCase):
    def test_basic_format(self):
        questions = [
            {"number": 1, "category": "A", "question": "What is X?", "auto_grade": True, "expected_value": "42"},
            {"number": 2, "category": "B", "question": "How does Y relate?", "auto_grade": False, "expected_value": ""},
        ]
        output = format_questions(questions, ["file1.md", "file2.md"])
        self.assertIn("=== 動的読了検証 ===", output)
        self.assertIn("1. [A] What is X?", output)
        self.assertIn("2. [B] How does Y relate?", output)
        self.assertIn("=== 検証終了 ===", output)
        self.assertIn("カテゴリA: 1問", output)

    def test_empty_questions(self):
        output = format_questions([], ["file.md"])
        self.assertIn("生成できませんでした", output)

    def test_file_list_in_output(self):
        questions = [
            {"number": 1, "category": "A", "question": "Q?", "auto_grade": True, "expected_value": "v"},
        ]
        output = format_questions(questions, ["/path/to/README.md"])
        self.assertIn("README.md", output)


# ---------------------------------------------------------------------------
# Expected value persistence tests
# ---------------------------------------------------------------------------

class TestExpectedValuePersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        path = os.path.join(self.tmpdir, "_verify_expected.json")
        if os.path.exists(path):
            os.remove(path)
        os.rmdir(self.tmpdir)

    def test_save_and_load(self):
        questions = [
            {"number": 1, "category": "A", "auto_grade": True, "expected_value": "42", "question": "Q?"},
            {"number": 2, "category": "B", "auto_grade": False, "expected_value": "", "question": "Q?"},
            {"number": 3, "category": "A", "auto_grade": True, "expected_value": "hello", "question": "Q?"},
        ]
        save_expected_values(questions, self.tmpdir)
        loaded = load_expected_values(self.tmpdir)
        self.assertEqual(loaded["Q1"], "42")
        self.assertEqual(loaded["Q3"], "hello")
        self.assertNotIn("Q2", loaded)

    def test_load_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_expected_values(os.path.join(self.tmpdir, "nonexistent"))

    def test_overwrite_on_save(self):
        q1 = [{"number": 1, "category": "A", "auto_grade": True, "expected_value": "old", "question": "Q?"}]
        save_expected_values(q1, self.tmpdir)
        q2 = [{"number": 1, "category": "A", "auto_grade": True, "expected_value": "new", "question": "Q?"}]
        save_expected_values(q2, self.tmpdir)
        loaded = load_expected_values(self.tmpdir)
        self.assertEqual(loaded["Q1"], "new")


# ---------------------------------------------------------------------------
# Normalization and matching tests
# ---------------------------------------------------------------------------

class TestNormalizeText(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(normalize_text("  hello  "), "hello")

    def test_lowercase(self):
        self.assertEqual(normalize_text("HELLO"), "hello")

    def test_nfkc(self):
        # Full-width digits should normalize
        self.assertEqual(normalize_text("\uff11\uff12\uff13"), "123")


    def test_strip_japanese_brackets(self):
        self.assertEqual(normalize_text("「hello」"), "hello")
        self.assertEqual(normalize_text("『world』"), "world")

    def test_strip_backticks(self):
        self.assertEqual(normalize_text("`brain.py`"), "brain.py")

    def test_strip_mixed_formatting(self):
        self.assertEqual(normalize_text("「`test`」"), "test")

    def test_brackets_with_nfkc_and_lowercase(self):
        self.assertEqual(normalize_text("「HELLO」"), "hello")


class TestExtractNumeric(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(extract_numeric("42"), 42.0)

    def test_comma_separated(self):
        self.assertEqual(extract_numeric("12,010"), 12010.0)

    def test_tilde_prefix(self):
        self.assertEqual(extract_numeric("~261,000"), 261000.0)

    def test_with_units(self):
        self.assertEqual(extract_numeric("12,010テスト"), 12010.0)

    def test_no_number(self):
        self.assertIsNone(extract_numeric("hello"))

    def test_decimal(self):
        self.assertEqual(extract_numeric("3.14"), 3.14)


class TestCheckAnswer(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(check_answer("42", "42"))

    def test_normalized_match(self):
        self.assertTrue(check_answer("Brain.py", "brain.py"))

    def test_partial_match(self):
        self.assertTrue(check_answer("12,010", "テスト数は12,010です"))

    def test_numeric_equivalence(self):
        self.assertTrue(check_answer("12,010", "12010"))

    def test_no_match(self):
        self.assertFalse(check_answer("hello", "world"))

    def test_empty_expected(self):
        self.assertFalse(check_answer("", "something"))

    def test_tilde_numeric(self):
        self.assertTrue(check_answer("~261,000行", "261000"))

    def test_brackets_in_expected(self):
        """Expected value with brackets should match answer without brackets."""
        self.assertTrue(check_answer("「brain.py」", "brain.py"))

    def test_backticks_in_expected(self):
        """Expected value with backticks should match answer without backticks."""
        self.assertTrue(check_answer("`brain.py`", "brain.py"))

    def test_brackets_in_answer(self):
        """Answer with brackets should match expected without brackets."""
        self.assertTrue(check_answer("brain.py", "「brain.py」"))

    def test_both_have_brackets(self):
        """Both with brackets should match."""
        self.assertTrue(check_answer("「hello」", "「hello」"))


# ---------------------------------------------------------------------------
# Parse answers tests
# ---------------------------------------------------------------------------

class TestParseAnswers(unittest.TestCase):
    def test_basic(self):
        result = parse_answers("Q1:answer1,Q3:answer3")
        self.assertEqual(result["Q1"], "answer1")
        self.assertEqual(result["Q3"], "answer3")

    def test_single(self):
        result = parse_answers("Q5:hello world")
        self.assertEqual(result["Q5"], "hello world")

    def test_empty(self):
        result = parse_answers("")
        self.assertEqual(result, {})

    def test_with_spaces(self):
        result = parse_answers("Q1: brain.py ,Q2: orchestrator ")
        self.assertEqual(result["Q1"], "brain.py")
        self.assertEqual(result["Q2"], "orchestrator")


# ---------------------------------------------------------------------------
# Verify answers tests
# ---------------------------------------------------------------------------

class TestVerifyAnswers(unittest.TestCase):
    def test_all_correct(self):
        expected = {"Q1": "42", "Q3": "hello"}
        answers = {"Q1": "42", "Q3": "hello"}
        result = verify_answers(expected, answers)
        self.assertIn("正答率: 2/2", result)
        self.assertIn("正解", result)

    def test_mixed_results(self):
        expected = {"Q1": "42", "Q3": "hello"}
        answers = {"Q1": "42", "Q3": "wrong"}
        result = verify_answers(expected, answers)
        self.assertIn("正答率: 1/2", result)

    def test_missing_answers(self):
        expected = {"Q1": "42", "Q3": "hello"}
        answers = {"Q1": "42"}
        result = verify_answers(expected, answers)
        self.assertIn("未回答", result)

    def test_bc_questions_listed(self):
        expected = {"Q1": "42"}
        answers = {"Q1": "42", "Q2": "some relation", "Q4": "cross-file"}
        result = verify_answers(expected, answers)
        self.assertIn("カテゴリB/C", result)
        self.assertIn("Q2", result)

    def test_empty_expected(self):
        result = verify_answers({}, {})
        self.assertIn("判定対象なし", result)

    def test_output_format(self):
        expected = {"Q1": "val"}
        answers = {"Q1": "val"}
        result = verify_answers(expected, answers)
        self.assertIn("=== 読了検証判定結果 ===", result)
        self.assertIn("=== 判定終了 ===", result)


# ---------------------------------------------------------------------------
# File path resolution tests
# ---------------------------------------------------------------------------

class TestResolveFilePaths(unittest.TestCase):
    def test_default_paths(self):
        paths = resolve_file_paths("/myproject")
        self.assertEqual(len(paths), 3)
        self.assertTrue(any("SYSTEM_ARCHITECTURE.md" in p for p in paths))
        self.assertTrue(any("README.md" in p for p in paths))
        self.assertTrue(any("CLAUDE.md" in p for p in paths))

    def test_custom_paths(self):
        paths = resolve_file_paths("/myproject", verify_files="/a/b.md,/c/d.md")
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], "/a/b.md")
        self.assertEqual(paths[1], "/c/d.md")


# ---------------------------------------------------------------------------
# Integration tests (run_verification / run_verify)
# ---------------------------------------------------------------------------

class TestRunVerification(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up files
        for d in [self.tmpdir, self.memory_dir]:
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
            os.rmdir(d)

    def _write_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_basic_run(self):
        self._write_file("test.md", "## Section\nValue: 42\nAnother: `brain.py`\n\n## Section2\nCount: 100")
        result = run_verification(
            cwd=self.tmpdir,
            memory_dir=self.memory_dir,
            verify_files=os.path.join(self.tmpdir, "test.md"),
        )
        self.assertIn("動的読了検証", result)

    def test_missing_files(self):
        result = run_verification(
            cwd=self.tmpdir,
            memory_dir=self.memory_dir,
            verify_files="/nonexistent/file.md",
        )
        self.assertIn("ファイルがありません", result)

    def test_all_missing(self):
        result = run_verification(
            cwd="/nonexistent/path",
            memory_dir=self.memory_dir,
        )
        self.assertIn("ファイルがありません", result)

    def test_expected_values_saved(self):
        self._write_file("test.md", "## Stats\nTests: 12,010\nLines: ~261,000行\n\n## More\nVersion: v44")
        run_verification(
            cwd=self.tmpdir,
            memory_dir=self.memory_dir,
            verify_files=os.path.join(self.tmpdir, "test.md"),
        )
        expected_path = os.path.join(self.memory_dir, "_verify_expected.json")
        self.assertTrue(os.path.exists(expected_path))

    def test_end_to_end_verify(self):
        self._write_file("test.md", "## Stats\nTests: 12,010\nLines: ~261,000行\n\n## Config\nModel: `brain.py`")
        run_verification(
            cwd=self.tmpdir,
            memory_dir=self.memory_dir,
            verify_files=os.path.join(self.tmpdir, "test.md"),
        )
        # Load expected to know what to answer
        expected = load_expected_values(self.memory_dir)
        # Build answer string
        answer_parts = []
        for qkey, val in expected.items():
            answer_parts.append(f"{qkey}:{val}")
        answers_str = ",".join(answer_parts)

        result = run_verify(self.memory_dir, answers_str)
        self.assertIn("読了検証判定結果", result)
        # All correct since we answered with the expected values
        if expected:
            self.assertIn("正解", result)


class TestRunVerifyErrors(unittest.TestCase):
    def test_missing_expected_file(self):
        result = run_verify("/nonexistent/path", "Q1:answer")
        self.assertIn("ERROR", result)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_section_with_only_heading(self):
        content = "## Empty Section\n\n## Section With Content\nHello: 42"
        sections = parse_sections(content, "test.md")
        # Both should parse, but the empty one should have no values
        empty_sec = [s for s in sections if s["heading"] == "Empty Section"][0]
        self.assertEqual(len(empty_sec["values"]), 0)

    def test_unicode_content(self):
        content = "## 日本語セクション\nテスト数: 12,010\n総行数: ~261,000行"
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 1)
        # Should have value_pairs from colon patterns
        self.assertTrue(len(sections[0]["value_pairs"]) > 0)

    def test_very_large_section(self):
        lines = ["## Big Section"]
        for i in range(1000):
            lines.append(f"- Item {i}: value_{i}")
        content = "\n".join(lines)
        sections = parse_sections(content, "test.md")
        self.assertEqual(len(sections), 1)
        # Should have extracted many value pairs from bullet items
        self.assertGreater(len(sections[0]["value_pairs"]), 10)

    def test_code_block_values(self):
        content = "## Code\n```python\ndef hello_world():\n    pass\n```"
        sections = parse_sections(content, "test.md")
        # Should at least parse without error
        self.assertEqual(len(sections), 1)


if __name__ == "__main__":
    unittest.main()
