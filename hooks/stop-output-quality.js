#!/usr/bin/env node
/**
 * G68 Stop hook: Leader output quality guard.
 *
 * Phase 2 scope: (a) hedging detection.
 * Phase 3 scope: (b) explanation-required (heading + body length).
 * Phase 7 (revised 5): proximity now via flag presence + 5min TTL.
 *                       sequence-comparison stage removed (was dead code).
 *
 * Inputs (Stop hook stdin JSON):
 *   - assistant_response | response : leader assistant text (External-untrusted)
 *
 * Behavior:
 *   - Parse failure / empty -> fail-open exit(0).
 *   - NFKC normalize + zero-width strip + ALL whitespace strip.
 *   - Mask quoted regions (full-width / half-width quotes) and blockquote lines.
 *   - Match against hedging detection table (linear-time regex, no
 *     backreferences / nested quantifiers — ReDoS structurally avoided).
 *   - 1+ hit -> blocking exit(2) + stderr (condition name only;
 *               detection terms NOT echoed -> self-block prevention §4.2.1).
 *   - 0 hit -> proceed to (b) explanation-required judgment.
 *
 * (b) judgment (Phase 3, revised 5 — F1-iii):
 *   - flag absent / TTL elapsed / schema corrupt -> fail-open (pass).
 *   - flag.subagent in {implementer, designer} AND TTL not elapsed
 *     -> 段判定 (heading / body). Flag presence + 5min TTL = proximity.
 *   - All stages satisfied -> pass + atomic flag delete.
 *   - Heading missing or body too short -> blocking exit(2).
 *
 * Self-block prevention (§4.2.1): stderr never contains the detection
 * terms themselves. Quoted regions and blockquote lines are masked
 * BEFORE matching, ensuring meta-mentions in design/lesson docs do not
 * trigger blocking.
 *
 * Fail-open paths: stdin parse error, empty response, regex/runtime
 * exception, file I/O error, flag schema error — all exit(0).
 */

const fs = require('fs');
const path = require('path');

const MAX_STDIN = 512 * 1024;

const FLAG_FILE = path.join(__dirname, '.b-flag-stop-output-quality');

// Emergency stop switch (Lesson #22 bootstrap-deadlock final escape):
//   - env STOP_OUTPUT_QUALITY_DISABLE=1
//   - flag file hooks/.stop-output-quality.disable (any contents)
// Either one set -> fail-open exit(0) immediately, before any I/O.
const DISABLE_FLAG_FILE = path.join(__dirname, '.stop-output-quality.disable');
(function emergencyStopCheck() {
  try {
    if (process.env.STOP_OUTPUT_QUALITY_DISABLE === '1') {
      process.exit(0);
    }
    if (fs.existsSync(DISABLE_FLAG_FILE)) {
      process.exit(0);
    }
  } catch {
    // Any failure here -> fail-open
    process.exit(0);
  }
})();

// (b) explanation-required parameters.
// [TBD-Phase0] 暫定: 100 字。Phase 0 実機計測 (assistant_response 上限) 完了後に確定値へ更新。
// docs/plan_g68_output_quality_guard.md §Phase 3 ステップ5 参照
const _MIN_BODY_CHARS_TENTATIVE = 100;
const TARGET_SUBAGENTS = new Set(['implementer', 'designer']);
// Heading marker regex (revised 4: bilingual JP/EN, case-insensitive).
//   \u8aac\u660e            = 説明
//   \u4ed5\u7d44\u307f      = 仕組み
//   \u65e2\u5b58\u3068\u306e\u63a5\u7d9a = 既存との接続
//   \u5b9f\u88c5\u30b5\u30de\u30ea       = 実装サマリ
//   English aliases: Explanation / Mechanism / Existing connection /
//                    Implementation summary
const HEADING_MARKER_RE =
  /^#+\s*(\u8aac\u660e|\u4ed5\u7d44\u307f|\u65e2\u5b58\u3068\u306e\u63a5\u7d9a|\u5b9f\u88c5\u30b5\u30de\u30ea|Explanation|Mechanism|Existing connection|Implementation summary)/im;

let raw = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    const remaining = MAX_STDIN - raw.length;
    raw += chunk.substring(0, remaining);
  }
});

process.stdin.on('end', () => {
  try {
    main();
  } catch {
    // Top-level fail-open
    process.exit(0);
  }
});

process.stdin.on('error', () => {
  process.exit(0);
});

function main() {
  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
    return;
  }

  if (!input || typeof input !== 'object') {
    process.exit(0);
    return;
  }

  const response = (input.assistant_response || input.response || '').toString();
  if (!response) {
    process.exit(0);
    return;
  }

  // ── (a) hedging detection ──
  const hedgingHit = detectHedging(response);
  if (hedgingHit) {
    // Blocking. Stderr message lists condition NAME only — never the
    // detection term itself (self-block prevention §4.2.1).
    process.stderr.write(
      '[output-quality] BLOCKED: hedging-language detected.\n' +
      '  condition: no-hedging-language\n' +
      '  action: rewrite response without hedging. State facts, not estimates.\n' +
      '          If uncertain, verify before responding.\n'
    );
    process.exit(2);
    return;
  }

  // ── (b) explanation-required: Phase 3 implementation ──
  const verdict = evaluateExplanationRequired(response);
  if (verdict.blocking) {
    process.stderr.write(verdict.stderr);
    process.exit(2);
    return;
  }

  process.exit(0);
}

/**
 * (b) Explanation-required judgment (revised 5 — F1-iii).
 *
 * Returns { blocking: boolean, stderr: string }.
 * On blocking, stderr contains a concise message listing only the
 * unsatisfied stage (no detection terms).
 *
 * Proximity model (revised 5): flag presence + 5min TTL is itself the
 * proximity signal. Sequence comparison was removed because Stop hook
 * stdin's `sequence` field is not guaranteed in production (was dead
 * code). The writer side restricts flag creation to implementer/designer
 * subagents, so unrelated subagents never produce flags. User
 * interruption is naturally absorbed by the short 5-min TTL.
 *
 * Fail-open paths (return { blocking: false }):
 *   - flag file absent
 *   - flag JSON corrupt / schema invalid
 *   - flag TTL elapsed
 *   - flag.subagent not in target set
 */
function evaluateExplanationRequired(response) {
  const flag = readFlagSafe();
  if (flag === null) {
    return { blocking: false, stderr: '' };
  }

  // TTL check (LOW#1: clock-skew defense via isValidTtl).
  const now = Date.now();
  if (!Number.isFinite(flag.timestamp) || !Number.isFinite(flag.ttl_ms)) {
    return { blocking: false, stderr: '' };
  }
  if (!isValidTtl(flag.timestamp, flag.ttl_ms, now)) {
    // TTL elapsed OR future timestamp (clock skew): auto-clear flag, fail-open.
    deleteFlagSafe();
    return { blocking: false, stderr: '' };
  }

  // Subagent type filter
  if (!flag.subagent || !TARGET_SUBAGENTS.has(flag.subagent)) {
    return { blocking: false, stderr: '' };
  }

  // Mask non-text regions BEFORE structural inspection so that fenced
  // code blocks (e.g., a literal "## fake" inside ```) cannot impersonate
  // a real heading and skew the body-length judgment.
  let masked;
  try {
    masked = maskNonTextRegions(response);
  } catch {
    return { blocking: false, stderr: '' };
  }

  // 段1: heading marker existence (post-mask)
  const hasHeading = HEADING_MARKER_RE.test(masked);

  // 段2: body min char count after first matched heading (post-mask)
  let bodyOk = false;
  if (hasHeading) {
    bodyOk = bodyAfterHeadingMeetsMin(masked, _MIN_BODY_CHARS_TENTATIVE);
  }

  if (!hasHeading || !bodyOk) {
    const missing = [];
    if (!hasHeading) missing.push('heading-marker');
    if (hasHeading && !bodyOk) missing.push('body-min-chars');
    const stderr =
      '[output-quality] BLOCKED: explanation-required for direct-implementer/designer post-handoff.\n' +
      '  condition: explanation-required\n' +
      '  missing: ' + missing.join(', ') + '\n' +
      '  action: include a section heading (\u8aac\u660e / \u4ed5\u7d44\u307f / ' +
      '\u65e2\u5b58\u3068\u306e\u63a5\u7d9a / \u5b9f\u88c5\u30b5\u30de\u30ea) ' +
      'with body of at least ' + _MIN_BODY_CHARS_TENTATIVE + ' characters.\n';
    return { blocking: true, stderr: stderr };
  }

  // All 3 stages satisfied -> pass + atomic flag delete.
  deleteFlagSafe();
  return { blocking: false, stderr: '' };
}

/**
 * Extract body following the first matched heading and check its
 * length against `minChars`. The body extends from after the matched
 * heading line up to the next heading (`^#+\s`) or end of text.
 *
 * Body length excludes leading/trailing whitespace and counts code
 * points using the actual string .length (sufficient for the threshold
 * level used here; planner accepted .length as the metric).
 */
function bodyAfterHeadingMeetsMin(text, minChars) {
  const m = HEADING_MARKER_RE.exec(text);
  if (!m) return false;
  // Find end of the heading line
  const headingStart = m.index;
  const lineEnd = text.indexOf('\n', headingStart);
  if (lineEnd < 0) return false;
  const after = text.substring(lineEnd + 1);
  // Find next heading
  const nextHeadingRe = /^#+\s/m;
  const nextMatch = nextHeadingRe.exec(after);
  const body = nextMatch ? after.substring(0, nextMatch.index) : after;
  const trimmed = body.trim();
  return trimmed.length >= minChars;
}

/**
 * Validate a TTL window. Both stale (past TTL) and future (clock skew)
 * timestamps are treated as invalid -> caller fail-opens + deletes flag.
 *
 * @param {number} timestamp  flag.timestamp (ms since epoch)
 * @param {number} ttl_ms     flag.ttl_ms (positive)
 * @param {number} now        Date.now() at evaluation time
 * @returns {boolean}         true iff 0 <= (now - timestamp) <= ttl_ms
 *
 * Notes:
 *   - 未来 timestamp も無効扱い (clock skew 防止) — system clock がジャンプ
 *     した場合、誤って永続化された flag が無期限有効になるのを防ぐ。
 *   - 同型バグ再発防止のため、TTL 検証は必ずこの関数経由で行う。
 */
function isValidTtl(timestamp, ttl_ms, now) {
  const elapsed = now - timestamp;
  return elapsed >= 0 && elapsed <= ttl_ms;
}

function readFlagSafe() {
  let raw;
  try {
    raw = fs.readFileSync(FLAG_FILE, 'utf8');
  } catch {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  return parsed;
}

function deleteFlagSafe() {
  try {
    fs.unlinkSync(FLAG_FILE);
  } catch {
    // ignore (fail-open)
  }
}

/**
 * Returns true if the assistant response contains hedging-language
 * outside quoted / blockquote regions, after NFKC normalization +
 * zero-width strip + whitespace strip.
 */
function detectHedging(text) {
  let normalized;
  try {
    normalized = normalizeText(text);
  } catch {
    // Normalization failure -> fail-open
    return false;
  }
  const masked = maskNonTextRegions(normalized);
  // Whitespace strip is applied to masked text so spaced terms collapse.
  const stripped = stripWhitespace(masked);

  for (const re of HEDGING_PATTERNS) {
    try {
      if (re.test(stripped)) return true;
    } catch {
      // Regex runtime error -> fail-open for that pattern, continue
      continue;
    }
  }
  return false;
}

/**
 * NFKC normalization + zero-width character removal.
 * Whitespace is NOT stripped here — blockquote line detection requires
 * preserved newlines.
 */
function normalizeText(text) {
  let s = String(text);
  if (typeof s.normalize === 'function') {
    s = s.normalize('NFKC');
  }
  // Remove zero-width characters: U+200B..U+200D, U+FEFF
  s = s.replace(/[\u200B-\u200D\uFEFF]/g, '');
  return s;
}

/**
 * Mask non-text regions where hedging-language detection should NOT fire,
 * AND where structural markdown (e.g., heading markers) inside literal
 * code regions should NOT be interpreted as document structure.
 *
 * Masked regions:
 *   - Triple-backtick fenced code blocks (```...```), multi-line.
 *   - Markdown blockquote lines (line starting with optional whitespace + '>').
 *   - Paired full-width quotes 「...」, 『...』.
 *   - Half-width quotes "...", '...'.
 *   - Inline code spans `...` (single backtick pair, single-line).
 *
 * Mask 順序の重要性:
 *   コードフェンス (複数行 ``` ... ```) を最初に処理する。理由: フェンス内に
 *   blockquote / quote / インラインコード片が含まれると、後段の単一行・短い
 *   パターンが先に部分マッチしてフェンス全体の境界を壊しうる。長く構造的な
 *   ものから先に剥がすことで、内側のノイズに惑わされず安全に除去できる。
 *   順序: コードフェンス → blockquote → 全角/半角quote → インラインコード。
 *
 * Linear time. No backreferences, no nested quantifiers (ReDoS-safe).
 *
 * @param {string} text  正規化済みテキスト
 * @returns {string}     マスク後テキスト (行数は保持)
 */
function maskNonTextRegions(text) {
  // 1. Mask fenced code blocks (multi-line, longest structural region first).
  //    Match ``` then anything (incl. newlines) until next ```. Newlines in
  //    the body are preserved as newlines so line indices stay aligned for
  //    blockquote detection in step 2.
  let s = text.replace(/```[\s\S]*?```/g, function(match) {
    // Replace non-newline chars with 'x' to preserve line structure.
    return match.replace(/[^\n]/g, 'x');
  });

  // 2. Remove blockquote lines.
  const lines = s.split(/\r?\n/);
  const kept = [];
  for (const line of lines) {
    // Trim leading whitespace check; if line starts with '>' it's blockquote
    const trimmed = line.replace(/^[ \t\u3000]+/, '');
    if (trimmed.startsWith('>')) {
      kept.push('');  // preserve line count
    } else {
      kept.push(line);
    }
  }
  s = kept.join('\n');

  // 3. Mask quoted regions. Use non-greedy match without backreferences.
  // Full-width pair 「...」
  s = s.replace(/\u300C[^\u300C\u300D]*\u300D/g, '___QUOTED___');
  // Full-width pair 『...』
  s = s.replace(/\u300E[^\u300E\u300F]*\u300F/g, '___QUOTED___');
  // Half-width double quotes "..."
  s = s.replace(/"[^"]*"/g, '___QUOTED___');
  // Half-width single quotes '...'
  s = s.replace(/'[^']*'/g, '___QUOTED___');

  // 4. Mask inline code spans `...` (single-line; do not cross newlines).
  //    Place last so triple-backtick fences (already removed) don't collide.
  s = s.replace(/`[^`\n]*`/g, '___INLINE___');

  return s;
}

/**
 * Strip ALL whitespace (full-width and half-width).
 * Newlines, tabs, regular spaces all removed.
 */
function stripWhitespace(text) {
  return text.replace(/[\s\u3000]+/g, '');
}

// Hedging detection table.
// ReDoS-safe: no backreferences, no nested quantifiers.
// Pattern terms are encoded via \uXXXX escapes so that this source file
// itself does not contain raw hedging characters that would trigger
// other pattern scanners (defense-in-depth for self-block prevention).
//
// Terms (encoded):
//   \u305f\u3076\u3093    = "tabun" hiragana
//   \u591a\u5206          = "tabun" kanji
//   \u304a\u305d\u3089\u304f = "osoraku" hiragana
//   \u6050\u3089\u304f    = "osoraku" kanji
//   (\u306e|\u306a|\u304f|\u308b|\u3044)\u306f\u305a = "(no|na|ku|ru|i) hazu"
//   — covers verb-end (ku/ru), i-adjective (i), na-adjective (na), noun (no)
const HEDGING_PATTERNS = [
  /\u305f\u3076\u3093/,                                                // tabun hiragana
  /\u591a\u5206/,                                                      // tabun kanji
  /\u304a\u305d\u3089\u304f/,                                          // osoraku hiragana
  /\u6050\u3089\u304f/,                                                // osoraku kanji
  /(?:\u306e|\u306a|\u304f|\u308b|\u3044)\u306f\u305a/,                // (no|na|ku|ru|i) hazu
];
