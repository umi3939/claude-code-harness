#!/usr/bin/env node
/**
 * G68 Phase 1: TDD test-first for hooks/stop-output-quality.js
 *
 * Coverage:
 *   - (a) hedging detection: 13 bypass patterns (B1-B13)
 *   - (b) explanation required: 7 patterns (B-OK1, B-NG1..4, B-TTL, B-RACE)
 *     [Phase 3 implements (b); revised 5 (F1-iii): sequence comparison removed,
 *      flag presence + 5min TTL = proximity. B-NG3 reframed as user-interrupt
 *      beyond 5min TTL (natural expiry).]
 *   - latency: 5 patterns (10-run avg, p95, max, blocking path, fail-open path)
 *
 * Run: node hooks/tests/test_stop_output_quality.js
 *
 * NOTE: All hedging example terms in this test file appear ONLY inside
 *       JS string literals (which become "external-untrusted" stdin payload
 *       to the hook, not assistant_response visible to Claude). The hook
 *       itself uses quote-mask to avoid self-blocking when surfaced as
 *       assistant_response — that path is asserted in B6/B8.
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log('  PASS: ' + msg);
  } else {
    failed++;
    failures.push(msg);
    console.log('  FAIL: ' + msg);
  }
}

const HOOK_SCRIPT = path.join(__dirname, '..', 'stop-output-quality.js');

/**
 * Run the hook with given assistant_response payload.
 * Returns { status, stderr, stdout, durationMs }.
 *   status === 0   => pass
 *   status === 2   => blocking
 *   status === null => process killed (timeout)
 */
function runHook(payload, opts) {
  const input = JSON.stringify(payload);
  const start = Date.now();
  let status = 0;
  let stderr = '';
  let stdout = '';
  try {
    const out = execFileSync('node', [HOOK_SCRIPT], {
      input: input,
      timeout: (opts && opts.timeout) || 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, opts && opts.env || {}),
    });
    stdout = out.toString();
  } catch (e) {
    status = e.status === undefined || e.status === null ? null : e.status;
    if (e.stderr) stderr = e.stderr.toString();
    if (e.stdout) stdout = e.stdout.toString();
  }
  const durationMs = Date.now() - start;
  return { status, stderr, stdout, durationMs };
}

// ──────────────────────────────────────────────────────────
// (a) hedging detection: 13 bypass patterns
// ──────────────────────────────────────────────────────────
console.log('\n=== (a) hedging detection: 13 patterns ===');

// B1: hiragana baseline → blocking
{
  const r = runHook({ assistant_response: '\u305f\u3076\u3093\u6210\u529f\u3059\u308b' });
  assert(r.status === 2, 'B1: hiragana hedging baseline -> blocking (status=2)');
}

// B2: kanji variant → blocking
{
  const r = runHook({ assistant_response: '\u591a\u5206\u52d5\u304f' });
  assert(r.status === 2, 'B2: kanji variant -> blocking');
}

// B3: katakana → pass (N7: hiragana<->katakana NOT unified)
{
  const r = runHook({ assistant_response: '\u30bf\u30d6\u30f3' });
  assert(r.status === 0, 'B3: katakana -> pass (N7 separation)');
}

// B4: full-width whitespace inserted → blocking (NFKC + whitespace strip)
{
  const r = runHook({ assistant_response: '\u305f\u3000\u3076\u3000\u3093' });
  assert(r.status === 2, 'B4: full-width whitespace inserted -> blocking');
}

// B5: half-width spaces in alphabet → pass (not in detection list)
{
  const r = runHook({ assistant_response: 't a b u n' });
  assert(r.status === 0, 'B5: half-width spaced alphabet -> pass');
}

// B6: full-width quoted mention → pass (quote mask)
{
  const r = runHook({ assistant_response: '\u30e6\u30fc\u30b6\u30fc\u306f\u300c\u305f\u3076\u3093\u300d\u3068\u66f8\u3044\u305f' });
  assert(r.status === 0, 'B6: full-width quoted hedging term -> pass (quote mask)');
}

// B7: blockquote line → pass
{
  const r = runHook({ assistant_response: '> \u305f\u3076\u3093\u6210\u529f\n\u30b3\u30e1\u30f3\u30c8' });
  assert(r.status === 0, 'B7: blockquote line -> pass (blockquote mask)');
}

// B8: meta-mention with quoted term → pass
{
  const r = runHook({ assistant_response: '\u300c\u305f\u3076\u3093\u300d\u3092\u4f7f\u3046\u306a' });
  assert(r.status === 0, 'B8: meta-mention quoted -> pass (quote mask)');
}

// B9: synonyms not in initial set → pass
{
  const r = runHook({ assistant_response: '\u3068\u601d\u3046\u3001\u304b\u3082\u3057\u308c\u306a\u3044' });
  assert(r.status === 0, 'B9: synonyms (to omou / kamoshirenai) -> pass (N7)');
}

// B10: multiple hedging terms → blocking once (no excessive stderr)
{
  const r = runHook({ assistant_response: '\u305f\u3076\u3093\u591a\u5206\u304a\u305d\u3089\u304f' });
  assert(r.status === 2, 'B10: multiple hedging in one response -> blocking');
  // Stderr length should not be obscenely long (loose bound)
  assert(r.stderr.length < 2000, 'B10: stderr is concise (<2000 chars)');
}

// B11: zero-width chars inserted → blocking (NFKC absorbs)
// Note: NFKC does NOT absorb zero-width joiner; spec says "ゼロ幅文字除去".
// Implementation must explicitly strip \u200B-\u200D and \uFEFF.
{
  const r = runHook({ assistant_response: '\u305f\u200b\u3076\u200b\u3093' });
  assert(r.status === 2, 'B11: zero-width chars inserted -> blocking (zero-width strip)');
}

// B12: "〜のはず" / "〜なはず" → blocking
{
  const r = runHook({ assistant_response: '\u52d5\u304f\u306f\u305a\u3060' });
  assert(r.status === 2, 'B12: -no hazu (no-hazu) -> blocking');
}

// B13: "おそらく" / "恐らく" → blocking
{
  const r = runHook({ assistant_response: '\u304a\u305d\u3089\u304f\u6210\u529f' });
  assert(r.status === 2, 'B13a: osoraku (hiragana) -> blocking');
  const r2 = runHook({ assistant_response: '\u6050\u3089\u304f\u6210\u529f' });
  assert(r2.status === 2, 'B13b: osoraku (kanji) -> blocking');
}

// B14 (LOW#2/#3): code-fence containing hedging term → pass (code-fence mask)
//   ``` ... \u305f\u3076\u3093 ... ```  inside a fenced code block.
//   The fenced region must be masked like quotes/blockquote.
{
  const resp =
    'Some prose without hedging.\n' +
    '```\n' +
    '\u305f\u3076\u3093\u6210\u529f\u3059\u308b\n' +
    '```\n' +
    'More plain prose.';
  const r = runHook({ assistant_response: resp });
  assert(r.status === 0, 'B14: code-fence containing hedging term -> pass (code-fence mask)');
}

// B14b: inline code containing hedging term → pass (inline-code mask)
{
  const resp = 'See `\u305f\u3076\u3093` literal as identifier example.';
  const r = runHook({ assistant_response: resp });
  assert(r.status === 0, 'B14b: inline-code containing hedging term -> pass (inline-code mask)');
}

// ──────────────────────────────────────────────────────────
// (b) explanation required: 7 patterns (revised 5 — F1-iii)
// Sequence comparison removed; proximity = flag presence + 5min TTL.
// B-NG3 (sequence skip) replaced with TTL-elapsed equivalent.
// ──────────────────────────────────────────────────────────
console.log('\n=== (b) explanation required: 7 patterns ===');

const FLAG_FILE = path.join(__dirname, '..', '.b-flag-stop-output-quality');
function writeFlag(content) {
  try { fs.writeFileSync(FLAG_FILE, content); } catch {}
}
function deleteFlag() {
  try { fs.unlinkSync(FLAG_FILE); } catch {}
}
function flagExists() {
  return fs.existsSync(FLAG_FILE);
}

// B-OK1: heading + body >=100chars + flag set + valid TTL → pass + flag cleared
{
  deleteFlag();
  const flag = {
    subagent: 'implementer',
    timestamp: Date.now(),
    ttl_ms: 5 * 60 * 1000,
  };
  writeFlag(JSON.stringify(flag));
  const body = 'A'.repeat(120);
  const resp = '## Explanation\n' + body + '\n## End\n';
  const r = runHook({ assistant_response: resp });
  assert(r.status === 0, 'B-OK1: heading + body 100+ + flag valid -> pass');
  assert(!flagExists(), 'B-OK1: flag cleared after pass');
}

// B-NG1: heading present, body 0 chars → blocking
{
  deleteFlag();
  writeFlag(JSON.stringify({ subagent: 'implementer', timestamp: Date.now(), ttl_ms: 5 * 60 * 1000 }));
  const resp = '## Explanation\n\n## Done';
  const r = runHook({ assistant_response: resp });
  assert(r.status === 2, 'B-NG1: heading + empty body -> blocking');
  deleteFlag();
}

// B-NG2: no heading, body present → blocking
{
  deleteFlag();
  writeFlag(JSON.stringify({ subagent: 'designer', timestamp: Date.now(), ttl_ms: 5 * 60 * 1000 }));
  const body = 'B'.repeat(120);
  const r = runHook({ assistant_response: body });
  assert(r.status === 2, 'B-NG2: no heading + body -> blocking');
  deleteFlag();
}

// B-NG3 (revised 5): user interrupt past 5min TTL → pass (TTL absorbs interruption).
// Sequence-skip pattern removed; structural replacement: flag older than TTL = proximity expired.
{
  deleteFlag();
  const expired = {
    subagent: 'implementer',
    timestamp: Date.now() - (10 * 60 * 1000),  // 10min ago, TTL=5min => elapsed
    ttl_ms: 5 * 60 * 1000,
  };
  writeFlag(JSON.stringify(expired));
  const r = runHook({ assistant_response: 'No explanation needed' });
  assert(r.status === 0, 'B-NG3 (revised): TTL elapsed -> pass (user interrupt absorbed)');
  assert(!flagExists(), 'B-NG3 (revised): TTL-elapsed flag auto-cleared');
}

// B-NG4: SubagentStop(reviewer) → no flag → pass
{
  deleteFlag();
  // Writer side restricts flag creation to implementer/designer only;
  // reviewer never produces a flag.
  const r = runHook({ assistant_response: 'No explanation needed' });
  assert(r.status === 0, 'B-NG4: no flag (reviewer excluded by writer) -> pass');
}

// B-TTL: flag with elapsed TTL → pass + flag auto-clear
{
  deleteFlag();
  const expired = {
    subagent: 'implementer',
    timestamp: Date.now() - (60 * 60 * 1000),
    ttl_ms: 5 * 60 * 1000,
  };
  writeFlag(JSON.stringify(expired));
  const r = runHook({ assistant_response: 'No explanation' });
  assert(r.status === 0, 'B-TTL: TTL elapsed -> pass');
  deleteFlag();
}

// B-RACE: parallel SubagentStop -> last-write-wins flag → pass with last-wins evaluation
{
  deleteFlag();
  writeFlag(JSON.stringify({
    subagent: 'designer',
    timestamp: Date.now(),
    ttl_ms: 5 * 60 * 1000,
  }));
  const body = 'C'.repeat(120);
  const resp = '## Explanation\n' + body;
  const r = runHook({ assistant_response: resp });
  assert(r.status === 0, 'B-RACE: last-wins flag -> evaluated correctly');
  deleteFlag();
}

// B-CLOCK (LOW#1): future timestamp (clock skew) -> fail-open + flag deletion.
//   isValidTtl rule: 0 <= (now - timestamp) <= ttl_ms.
//   Future timestamp (now - timestamp < 0) is treated as invalid -> fail-open.
{
  deleteFlag();
  const future = {
    subagent: 'implementer',
    timestamp: Date.now() + 60000,  // 60s in the future
    ttl_ms: 5 * 60 * 1000,
  };
  writeFlag(JSON.stringify(future));
  const r = runHook({ assistant_response: 'No explanation needed' });
  assert(r.status === 0, 'B-CLOCK (LOW#1): future timestamp -> fail-open (clock skew defense)');
  assert(!flagExists(), 'B-CLOCK (LOW#1): future-timestamp flag auto-cleared');
}

// B-NG3-CF (LOW#2/#3): real heading + 100+ body, then code-fence with fake heading + short body
//   Real body is long enough; the fenced fake "## fake" heading must be masked
//   so it does not become the "first matched heading" used for body-length judgment.
{
  deleteFlag();
  writeFlag(JSON.stringify({ subagent: 'implementer', timestamp: Date.now(), ttl_ms: 5 * 60 * 1000 }));
  const realBody = 'D'.repeat(120);
  const resp =
    '## Explanation\n' +
    realBody + '\n' +
    '```\n' +
    '## fake\n' +
    'short\n' +
    '```\n';
  const r = runHook({ assistant_response: resp });
  assert(r.status === 0, 'B-NG3-CF (LOW#2/#3): real heading + body, fenced fake heading masked -> pass');
  deleteFlag();
}

// ──────────────────────────────────────────────────────────
// latency: 5 patterns
// ──────────────────────────────────────────────────────────
console.log('\n=== latency: 5 patterns ===');

// L1: 10-run average for plain hedging-free input
{
  const durations = [];
  for (let i = 0; i < 10; i++) {
    const r = runHook({ assistant_response: 'OK plain text without hedging.' });
    durations.push(r.durationMs);
  }
  const avg = durations.reduce((a, b) => a + b, 0) / durations.length;
  console.log('  L1 avg=' + avg.toFixed(1) + 'ms');
  // Generous bound: node startup alone is ~50-150ms on Windows.
  // The +200ms budget is for the hook addition vs baseline; we assert <500ms total.
  assert(avg < 500, 'L1: 10-run average < 500ms (got ' + avg.toFixed(1) + ')');
}

// L2: p95 latency on 10 runs
{
  const durations = [];
  for (let i = 0; i < 10; i++) {
    const r = runHook({ assistant_response: 'A'.repeat(2000) });
    durations.push(r.durationMs);
  }
  durations.sort((a, b) => a - b);
  const p95 = durations[Math.floor(durations.length * 0.95)];
  console.log('  L2 p95=' + p95 + 'ms');
  assert(p95 < 800, 'L2: p95 < 800ms (got ' + p95 + ')');
}

// L3: max latency on 10 runs
{
  const durations = [];
  for (let i = 0; i < 10; i++) {
    const r = runHook({ assistant_response: 'medium length text ' + 'X'.repeat(500) });
    durations.push(r.durationMs);
  }
  const max = Math.max(...durations);
  console.log('  L3 max=' + max + 'ms');
  assert(max < 1500, 'L3: max < 1500ms (got ' + max + ')');
}

// L4: blocking path latency
{
  const r = runHook({ assistant_response: '\u305f\u3076\u3093\u3067\u3059' });
  console.log('  L4 blocking duration=' + r.durationMs + 'ms');
  assert(r.status === 2, 'L4: blocking path returns status 2');
  assert(r.durationMs < 1500, 'L4: blocking path latency < 1500ms');
}

// L5: fail-open path latency (invalid JSON)
{
  const start = Date.now();
  let status = 0;
  try {
    execFileSync('node', [HOOK_SCRIPT], {
      input: 'not valid json{{{',
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  } catch (e) {
    status = e.status === undefined || e.status === null ? -1 : e.status;
  }
  const dur = Date.now() - start;
  console.log('  L5 fail-open duration=' + dur + 'ms');
  assert(status === 0, 'L5: invalid JSON -> fail-open exit 0');
  assert(dur < 1500, 'L5: fail-open path latency < 1500ms');
}

// ──────────────────────────────────────────────────────────
// Emergency stop switch (Lesson #22 bootstrap escape)
// ──────────────────────────────────────────────────────────
console.log('\n=== emergency stop switch ===');

// E1: env STOP_OUTPUT_QUALITY_DISABLE=1 -> fail-open even on hedging hit
{
  const r = runHook(
    { assistant_response: '\u305f\u3076\u3093\u6210\u529f\u3059\u308b' },
    { env: { STOP_OUTPUT_QUALITY_DISABLE: '1' } }
  );
  assert(r.status === 0, 'E1: env STOP_OUTPUT_QUALITY_DISABLE=1 -> fail-open even on hedging');
}

// E2: hooks/.stop-output-quality.disable file -> fail-open even on hedging hit
{
  const flagPath = path.join(__dirname, '..', '.stop-output-quality.disable');
  let preExisted = false;
  try { preExisted = fs.existsSync(flagPath); } catch {}
  try {
    fs.writeFileSync(flagPath, '');
    const r = runHook({ assistant_response: '\u305f\u3076\u3093\u6210\u529f\u3059\u308b' });
    assert(r.status === 0, 'E2: disable flag file -> fail-open even on hedging');
  } finally {
    if (!preExisted) {
      try { fs.unlinkSync(flagPath); } catch {}
    }
  }
}

// E3: without disable switch -> blocking still works (sanity check)
{
  const r = runHook(
    { assistant_response: '\u305f\u3076\u3093\u6210\u529f\u3059\u308b' },
    { env: { STOP_OUTPUT_QUALITY_DISABLE: '' } }
  );
  assert(r.status === 2, 'E3: no disable switch -> hedging still blocking (sanity)');
}

// ──────────────────────────────────────────────────────────
// Summary
// ──────────────────────────────────────────────────────────
console.log('\n=== Summary ===');
console.log('passed: ' + passed);
console.log('failed: ' + failed);
if (failed > 0) {
  console.log('\nFailures:');
  failures.forEach(f => console.log('  - ' + f));
}
process.exit(failed > 0 ? 1 : 0);
