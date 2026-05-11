#!/usr/bin/env python3
"""Dynamic read verification: generates quiz questions from markdown files.

Parses target markdown files into sections, extracts concrete values,
and generates verification questions to ensure the files were fully read.

Supports three question categories:
  A: Single-section value reference (auto-gradable)
  B: Cross-section relationship within one file (manual check)
  C: Cross-file relationship (manual check)
"""

import json
import math
import os
import random
import re
import unicodedata
from collections import Counter


# ---------------------------------------------------------------------------
# Precompiled regex patterns (M-Q4)
# ---------------------------------------------------------------------------

_HEADING_PATTERN = re.compile(r"^(#{1,2})\s+(.+)$")
_BULLET_COLON_PATTERN = re.compile(
    r"^[\s]*[-*]\s+\*{0,2}([^:：\n]+?)\*{0,2}\s*[:：]\s*(.+)$", re.MULTILINE
)
_PAREN_VALUE_PATTERN = re.compile(
    r"([A-Za-z_][\w.]*)\s*[（(]([\d~.,]+[\d][\w%行個件本回]*)[)）]"
)
_BACKTICK_VALUE_PATTERN = re.compile(
    r"`([^`]{2,60})`[^`\n]{0,40}?([\d~.,]+[\d][\w%行個件本回]*)"
)
_KV_NUMERIC_PATTERN = re.compile(
    r"([A-Za-z\u3040-\u9fff][\w\u3040-\u9fff]*(?:\s+[\w\u3040-\u9fff]+){0,3})\s*[:：]\s*([\d~.,]+[\d][\w%行個件本回]*)"
)

# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def parse_sections(content: str, filename: str) -> list[dict]:
    """Split markdown content into sections by headings (## and above).

    Returns a list of dicts:
        heading: str       -- the heading text (without leading #)
        body: str          -- text between this heading and the next
        values: list[str]  -- extracted concrete values
        start_line: int    -- 1-based start line of the section
        end_line: int      -- 1-based end line of the section
        filename: str      -- originating filename
    """
    lines = content.split("\n")
    heading_pattern = _HEADING_PATTERN

    # Find all heading positions
    heading_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = heading_pattern.match(line)
        if m:
            heading_positions.append((i, m.group(2).strip()))

    if not heading_positions:
        # Treat entire file as one section
        body = content.strip()
        value_pairs = extract_value_pairs(body)
        values = extract_values(body)
        return [{
            "heading": os.path.basename(filename),
            "body": body,
            "values": values,
            "value_pairs": value_pairs,
            "start_line": 1,
            "end_line": len(lines),
            "filename": filename,
        }]

    sections = []
    for idx, (pos, heading) in enumerate(heading_positions):
        if idx + 1 < len(heading_positions):
            end_pos = heading_positions[idx + 1][0]
        else:
            end_pos = len(lines)

        body_lines = lines[pos + 1 : end_pos]
        body = "\n".join(body_lines).strip()
        value_pairs = extract_value_pairs(body)
        values = extract_values(body)

        sections.append({
            "heading": heading,
            "body": body,
            "values": values,
            "value_pairs": value_pairs,
            "start_line": pos + 1,   # 1-based
            "end_line": end_pos,     # 1-based (exclusive becomes inclusive)
            "filename": filename,
        })

    return sections


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------

def extract_values(text: str) -> list[str]:
    """Extract concrete values from section body text (flat list, legacy).

    Returns a flat list of value strings for backward compatibility
    with Category B/C logic that only needs presence of values.
    """
    pairs = extract_value_pairs(text)
    seen: set[str] = set()
    values: list[str] = []
    for k, v in pairs:
        for item in (k, v):
            if item and item not in seen:
                seen.add(item)
                values.append(item)
    return values


def extract_value_pairs(text: str) -> list[tuple[str, str]]:
    """Extract key-value pairs from section body text.

    Returns a list of (key, value) tuples where:
      - key: the identifying/contextual element (e.g., module name, label)
      - value: the concrete data element (e.g., line count, description)

    Patterns:
      - Table rows: adjacent cells form pairs (cell_i, cell_j)
      - Bullet items with colon: "label: description" -> (label, description)
      - Backtick name + nearby number: `name` ... number -> (name, number)
      - Parenthetical values: "name (123行)" -> (name, 123行)
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _valid(v: str) -> bool:
        return bool(v) and len(v) >= 2 and len(v) <= 200

    def _add(k: str, v: str) -> None:
        k = k.strip()
        v = v.strip()
        if _valid(k) and _valid(v) and k != v:
            pair = (k, v)
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)

    # --- Table row pairs ---
    # Parse each row with | delimiters, pair adjacent non-header cells
    for line in text.split("\n"):
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c and not c.startswith("---") and not c.startswith(":---")]
        if len(cells) >= 2:
            for i in range(len(cells)):
                for j in range(i + 1, len(cells)):
                    _add(cells[i], cells[j])

    # --- Bullet items with colon separator ---
    # "- **label**: description" or "- label: description"
    for m in _BULLET_COLON_PATTERN.finditer(text):
        label = m.group(1).strip().rstrip("*").strip()
        desc = m.group(2).strip()
        if label and desc:
            _add(label, desc)

    # --- Parenthetical value patterns ---
    # "name (123行)" or "name（123行）"
    for m in _PAREN_VALUE_PATTERN.finditer(text):
        name = m.group(1).strip()
        val = m.group(2).strip()
        _add(name, val)

    # --- Backtick name + associated numeric value ---
    # Look for patterns like "`module_name` ... 1,234行" on the same line
    for m in _BACKTICK_VALUE_PATTERN.finditer(text):
        name = m.group(1).strip()
        val = m.group(2).strip()
        _add(name, val)

    # --- "key: numeric_value" patterns (non-bullet) ---
    for m in _KV_NUMERIC_PATTERN.finditer(text):
        label = m.group(1).strip()
        val = m.group(2).strip()
        _add(label, val)

    return pairs


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

def determine_question_counts(total_sections_with_values: int) -> dict:
    """Decide how many questions of each category to generate.

    Total = ceil(total_sections * 0.30), clamped to [3, 15].
    A: 65%, B: 20%, C: 15%  (remainders added to A).
    """
    total = math.ceil(total_sections_with_values * 0.30)
    total = max(3, min(15, total))

    b_count = int(total * 0.20)
    c_count = int(total * 0.15)
    a_count = total - b_count - c_count

    return {"total": total, "A": a_count, "B": b_count, "C": c_count}


def _pick_random(lst, n):
    """Pick up to n distinct random items from lst."""
    if len(lst) <= n:
        return list(lst)
    return random.sample(lst, n)


def _common_keywords(sec_a: dict, sec_b: dict) -> list[str]:
    """Find common significant words between two sections."""
    def _words(s: dict) -> set[str]:
        text = s["heading"] + " " + s["body"]
        tokens = re.findall(r"[A-Za-z_]\w{3,}", text)
        return {t.lower() for t in tokens}

    return list(_words(sec_a) & _words(sec_b))


# --- Category A templates ---
# {hint} = the presented side of the pair, {ask} = what we're asking for

_TEMPLATES_A_KEY_TO_VALUE = [
    "{filename} の {heading} において、「{hint}」に対応する値は何ですか？",
    "{filename} の {heading} に記載されている「{hint}」の数値・内容を答えてください",
]

_TEMPLATES_A_VALUE_TO_KEY = [
    "{filename} の {heading} において、「{hint}」に該当する名称・項目は何ですか？",
    "{filename} の {heading} で「{hint}」という値を持つ項目は何ですか？",
]


def _filter_ambiguous_pairs(value_pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Filter out pairs where the same value maps to multiple keys or vice versa.

    If a key appears with multiple different values, asking "what is the value
    for key?" becomes ambiguous. Similarly for asking "what key has value?".
    We exclude such pairs from Category A (auto-graded) questions.
    """
    # Count how many times each key and each value appears
    key_counts: Counter = Counter(k for k, v in value_pairs)
    value_counts: Counter = Counter(v for k, v in value_pairs)

    # A pair is unambiguous only if both its key and value appear exactly once
    return [
        (k, v) for k, v in value_pairs
        if key_counts[k] == 1 and value_counts[v] == 1
    ]


def _generate_a_question(section: dict) -> dict | None:
    """Generate a Category A question from a section with value pairs.

    Picks a (key, value) pair and randomly chooses to:
      - Present the key, ask for the value (key->value direction)
      - Present the value, ask for the key (value->key direction)

    Pairs where the same key or value appears multiple times are excluded
    to avoid ambiguous auto-grading.
    """
    value_pairs = section.get("value_pairs", [])
    if not value_pairs:
        return None

    # Filter out ambiguous pairs
    unambiguous = _filter_ambiguous_pairs(value_pairs)
    if not unambiguous:
        return None

    key, value = random.choice(unambiguous)
    fname = os.path.basename(section["filename"])
    heading = section["heading"]

    # Randomly choose direction: ask for value or ask for key
    if random.random() < 0.5:
        # Present key, ask for value
        hint = key
        expected = value
        template = random.choice(_TEMPLATES_A_KEY_TO_VALUE)
    else:
        # Present value, ask for key
        hint = value
        expected = key
        template = random.choice(_TEMPLATES_A_VALUE_TO_KEY)

    question_text = template.format(
        filename=fname,
        heading=heading,
        hint=hint,
    )

    return {
        "category": "A",
        "question": question_text,
        "source_file": section["filename"],
        "source_heading": heading,
        "expected_value": expected,
        "auto_grade": True,
    }


# --- Category B templates ---

_TEMPLATES_B = [
    "{filename} の {heading_a} で述べられている概念は、同ファイルの {heading_b} のどの要素と関連していますか？",
]


def _generate_b_question(sec_a: dict, sec_b: dict) -> dict | None:
    """Generate a Category B question from two sections in the same file."""
    fname = os.path.basename(sec_a["filename"])
    return {
        "category": "B",
        "question": random.choice(_TEMPLATES_B).format(
            filename=fname,
            heading_a=sec_a["heading"],
            heading_b=sec_b["heading"],
        ),
        "source_file": sec_a["filename"],
        "source_heading": f"{sec_a['heading']} / {sec_b['heading']}",
        "expected_value": "",
        "auto_grade": False,
    }


# --- Category C templates ---

_TEMPLATES_C = [
    "{file_x} の {heading_x} に記載されている概念に対応する {file_y} の記述はどのセクションにありますか？その内容は何ですか？",
]


def _generate_c_question(sec_x: dict, sec_y: dict) -> dict | None:
    """Generate a Category C question from sections in different files."""
    fname_x = os.path.basename(sec_x["filename"])
    fname_y = os.path.basename(sec_y["filename"])
    return {
        "category": "C",
        "question": random.choice(_TEMPLATES_C).format(
            file_x=fname_x,
            heading_x=sec_x["heading"],
            file_y=fname_y,
        ),
        "source_file": f"{sec_x['filename']} / {sec_y['filename']}",
        "source_heading": f"{sec_x['heading']} / {sec_y['heading']}",
        "expected_value": "",
        "auto_grade": False,
    }


def generate_questions(all_sections: list[dict]) -> list[dict]:
    """Generate verification questions from parsed sections.

    Returns a list of question dicts with number assigned.
    """
    # Filter to sections with values (for B/C) and value_pairs (for A)
    sections_with_values = [s for s in all_sections if s.get("values")]
    sections_with_pairs = [s for s in all_sections if s.get("value_pairs")]

    if not sections_with_values and not sections_with_pairs:
        return []

    # Use the larger pool for question count calculation
    count_basis = max(len(sections_with_values), len(sections_with_pairs))
    if count_basis == 0:
        return []

    counts = determine_question_counts(count_basis)
    questions: list[dict] = []

    # Group sections by file (using sections_with_values for B/C)
    by_file: dict[str, list[dict]] = {}
    for s in sections_with_values:
        by_file.setdefault(s["filename"], []).append(s)

    # Category A -- uses value_pairs
    a_pool = list(sections_with_pairs)
    random.shuffle(a_pool)
    a_generated = 0
    for sec in a_pool:
        if a_generated >= counts["A"]:
            break
        q = _generate_a_question(sec)
        if q:
            questions.append(q)
            a_generated += 1

    # Category B -- pairs within same file
    b_generated = 0
    if counts["B"] > 0:
        b_candidates: list[tuple[dict, dict]] = []
        for fname, secs in by_file.items():
            if len(secs) >= 2:
                for i in range(len(secs)):
                    for j in range(i + 1, len(secs)):
                        common = _common_keywords(secs[i], secs[j])
                        if common:
                            b_candidates.append((secs[i], secs[j]))
        random.shuffle(b_candidates)
        for sa, sb in b_candidates:
            if b_generated >= counts["B"]:
                break
            q = _generate_b_question(sa, sb)
            if q:
                questions.append(q)
                b_generated += 1

    # Category C -- pairs across files
    c_generated = 0
    if counts["C"] > 0:
        file_list = list(by_file.keys())
        c_candidates: list[tuple[dict, dict]] = []
        if len(file_list) >= 2:
            for i in range(len(file_list)):
                for j in range(i + 1, len(file_list)):
                    for sx in by_file[file_list[i]]:
                        for sy in by_file[file_list[j]]:
                            common = _common_keywords(sx, sy)
                            if common:
                                c_candidates.append((sx, sy))
        random.shuffle(c_candidates)
        for sx, sy in c_candidates:
            if c_generated >= counts["C"]:
                break
            q = _generate_c_question(sx, sy)
            if q:
                questions.append(q)
                c_generated += 1

    # If B or C couldn't generate enough, fill remainder with A
    shortfall = counts["total"] - len(questions)
    if shortfall > 0:
        used_headings = {q.get("source_heading", "") for q in questions}
        # Use a_pool (sections_with_pairs) for fill
        fill_pool = list(sections_with_pairs)
        random.shuffle(fill_pool)
        for sec in fill_pool:
            if shortfall <= 0:
                break
            if sec["heading"] not in used_headings:
                q = _generate_a_question(sec)
                if q:
                    questions.append(q)
                    used_headings.add(sec["heading"])
                    shortfall -= 1

    # Shuffle and number
    random.shuffle(questions)
    for i, q in enumerate(questions, 1):
        q["number"] = i

    return questions


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_MAX_OUTPUT_CHARS = 5000


def format_questions(questions: list[dict], file_list: list[str]) -> str:
    """Format questions into the standard output format."""
    if not questions:
        return "検証質問を生成できませんでした（具体値を持つセクションが見つかりません）"

    a_count = sum(1 for q in questions if q["category"] == "A")
    filenames = [os.path.basename(f) for f in file_list]

    lines: list[str] = []
    lines.append("=== 動的読了検証 ===")
    lines.append("")
    lines.append("以下の質問に具体的な値で回答してください。")
    lines.append("回答するには対象ファイルを全文読む必要があります。")
    lines.append("カテゴリAの質問は自動判定されます。回答後に verify サブコマンドで判定を実行してください。")
    lines.append("")
    lines.append("対象ファイル:")
    for fn in filenames:
        lines.append(f"- {fn}")
    lines.append("")
    lines.append(f"質問 ({len(questions)}問, うちカテゴリA: {a_count}問[自動判定]):")
    lines.append("")

    for q in questions:
        lines.append(f"{q['number']}. [{q['category']}] {q['question']}")

    lines.append("")
    lines.append("全ての質問に回答してから作業を開始してください。")
    lines.append("カテゴリAの回答は以下のコマンドで検証できます:")
    lines.append('  python memory_manager.py verify --memory-dir <DIR> --answers "Q1:回答1,Q3:回答3,..."')
    lines.append("=== 検証終了 ===")

    output = "\n".join(lines)

    # Output size safety valve: reduce questions if too long
    if len(output) > _MAX_OUTPUT_CHARS and len(questions) > 3:
        return format_questions(questions[:len(questions) - 1], file_list)

    return output


# ---------------------------------------------------------------------------
# Expected value persistence
# ---------------------------------------------------------------------------

def save_expected_values(questions: list[dict], memory_dir: str) -> str:
    """Save Category A expected values to a temp file in memory_dir.

    Returns the path of the saved file.
    """
    expectations: dict[str, str] = {}
    for q in questions:
        if q.get("auto_grade") and q.get("expected_value"):
            expectations[f"Q{q['number']}"] = q["expected_value"]

    path = os.path.join(memory_dir, "_verify_expected.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(expectations, f, ensure_ascii=False, indent=2)

    return path


def load_expected_values(memory_dir: str) -> dict[str, str]:
    """Load expected values from the temp file.

    Returns dict mapping 'Q<n>' -> expected_value.
    Raises FileNotFoundError if file does not exist.
    """
    path = os.path.join(memory_dir, "_verify_expected.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"期待値ファイルが見つかりません: {path}\n"
            "先に startup コマンドで検証質問を生成してください。"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Answer verification (Category A)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize text for comparison: strip, NFKC, lowercase, remove formatting."""
    text = text.strip()
    text = unicodedata.normalize("NFKC", text)
    # Remove formatting characters: Japanese brackets and backticks
    text = text.replace("「", "").replace("」", "")
    text = text.replace("『", "").replace("』", "")
    text = text.replace("`", "")
    text = text.lower()
    return text


def extract_numeric(text: str) -> float | None:
    """Try to extract a numeric value from text, ignoring commas/units."""
    cleaned = re.sub(r"[,、，]", "", text)
    m = re.search(r"~?(\d+(?:\.\d+)?)", cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def check_answer(expected: str, answer: str) -> bool:
    """Check if answer matches expected value.

    Matching order:
    1. Exact match
    2. Normalized match (strip, NFKC, lowercase)
    3. Partial match (expected in answer)
    4. Numeric equivalence (for numeric values)
    """
    if not expected or not answer:
        return False

    # 1. Exact match
    if expected == answer:
        return True

    # 2. Normalized match
    norm_expected = normalize_text(expected)
    norm_answer = normalize_text(answer)
    if not norm_expected or not norm_answer:
        return False
    if norm_expected == norm_answer:
        return True

    # 3. Partial match
    if norm_expected in norm_answer:
        return True

    # 4. Numeric equivalence
    num_expected = extract_numeric(expected)
    num_answer = extract_numeric(answer)
    if num_expected is not None and num_answer is not None:
        if num_expected == num_answer:
            return True

    return False


def parse_answers(answers_str: str) -> dict[str, str]:
    """Parse answer string 'Q1:answer1,Q3:answer3,...' into a dict.

    Handles values that may themselves contain commas by using
    the pattern Q<number>: as delimiters.
    """
    result: dict[str, str] = {}
    # Split by Q<number>: pattern
    parts = re.split(r"(?:^|,)(Q\d+):", answers_str)
    # parts[0] is before the first match (usually empty)
    # then alternating: key, value, key, value, ...
    i = 1
    while i < len(parts) - 1:
        key = parts[i].strip()
        value = parts[i + 1].strip()
        result[key] = value
        i += 2

    return result


def verify_answers(expected: dict[str, str], answers: dict[str, str]) -> str:
    """Verify answers against expected values and return formatted result.

    expected: {'Q1': 'expected_val', 'Q3': 'expected_val', ...}
    answers: {'Q1': 'answer', 'Q3': 'answer', ...}
    """
    lines: list[str] = []
    lines.append("=== 読了検証判定結果 ===")
    lines.append("")
    lines.append("カテゴリA（自動判定）:")

    correct = 0
    total = len(expected)

    for qkey in sorted(expected.keys(), key=lambda k: int(k[1:])):
        exp_val = expected[qkey]
        if qkey in answers:
            ans_val = answers[qkey]
            if check_answer(exp_val, ans_val):
                lines.append(f'  {qkey}: 正解 (期待値: "{exp_val}", 回答: "{ans_val}")')
                correct += 1
            else:
                lines.append(f'  {qkey}: 不正解 (期待値: "{exp_val}", 回答: "{ans_val}")')
        else:
            lines.append(f'  {qkey}: 未回答 (期待値: "{exp_val}")')

    lines.append("")
    if total > 0:
        pct = correct / total * 100
        lines.append(f"正答率: {correct}/{total} ({pct:.1f}%)")
    else:
        lines.append("正答率: 判定対象なし")

    # Find B/C questions (those not in expected)
    all_answer_keys = set(answers.keys())
    bc_keys = sorted(
        (k for k in all_answer_keys if k not in expected),
        key=lambda k: int(k[1:])
    )
    if bc_keys:
        lines.append("")
        lines.append("カテゴリB/C（手動確認）:")
        lines.append(f"  {', '.join(bc_keys)}: 回答を確認してください（自動判定対象外）")

    lines.append("")
    lines.append("=== 判定終了 ===")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# High-level orchestration (called from memory_manager.py)
# ---------------------------------------------------------------------------

_DEFAULT_FILES = ["SYSTEM_ARCHITECTURE.md", "README.md", "CLAUDE.md"]


def resolve_file_paths(cwd: str, verify_files: str | None = None) -> list[str]:
    """Resolve target file paths.

    If verify_files is provided (comma-separated), use those.
    Otherwise use defaults relative to cwd.
    """
    if verify_files:
        return [f.strip() for f in verify_files.split(",") if f.strip()]

    return [os.path.join(cwd, f) for f in _DEFAULT_FILES]


def run_verification(cwd: str, memory_dir: str,
                     verify_files: str | None = None) -> str:
    """Run the full verification question generation.

    1. Resolve file paths
    2. Read and parse each file
    3. Generate questions
    4. Save expected values
    5. Return formatted output

    Returns formatted question output string.
    """
    file_paths = resolve_file_paths(cwd, verify_files)

    # Read files
    all_sections: list[dict] = []
    valid_files: list[str] = []
    warnings: list[str] = []

    for fpath in file_paths:
        if not os.path.exists(fpath):
            warnings.append(f"警告: ファイルが見つかりません: {fpath}")
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            sections = parse_sections(content, fpath)
            all_sections.extend(sections)
            valid_files.append(fpath)
        except Exception as e:
            warnings.append(f"警告: ファイル読み込みエラー: {fpath}: {e}")

    if not valid_files:
        msg = "検証対象のファイルがありません"
        if warnings:
            msg += "\n" + "\n".join(warnings)
        return msg

    # Generate questions
    questions = generate_questions(all_sections)

    if not questions:
        msg = "検証質問を生成できませんでした（具体値を持つセクションが見つかりません）"
        if warnings:
            msg += "\n" + "\n".join(warnings)
        return msg

    # Save expected values
    try:
        save_expected_values(questions, memory_dir)
    except Exception as e:
        warnings.append(f"警告: 期待値の保存に失敗: {e}")

    # Format output
    output = format_questions(questions, valid_files)
    if warnings:
        output = "\n".join(warnings) + "\n\n" + output

    return output


def run_verify(memory_dir: str, answers_str: str) -> str:
    """Run the answer verification phase.

    Loads expected values and compares with provided answers.
    """
    try:
        expected = load_expected_values(memory_dir)
    except FileNotFoundError as e:
        return f"ERROR: {e}"

    answers = parse_answers(answers_str)

    return verify_answers(expected, answers)
