#!/usr/bin/env node
/**
 * Behavior Guard - 汎用PreToolUse介入フック
 *
 * behavior-rules.json から検出ルールを読み込み、
 * ツール呼び出しパターンを監視して警告する。
 *
 * PrincipleGuardの後継。単一パターンではなく複数ルールを一括チェック。
 * "blocking": true のルールは exit 2 でツール呼び出しをブロック（stderrがClaude自身に見える）。
 * それ以外は exit 0 で警告（stdoutがClaude自身に見える）。
 *
 * 対応する条件タイプ:
 *   frequency: new_file, same_cmd（時間窓内の回数で発火）
 *   pattern: py_file, git_revert_cmd, task_without_team, py_write_without_doc（パターン一致で発火）
 *   pattern(mcp_tool_guard): requires_session, rate_limit, required_args, requires_prior_tool, context_warning（MCPツールガード）
 */

const fs = require('fs');
const path = require('path');

// Path overrides (test isolation): env vars allow tests to redirect file I/O
// without changing production defaults.  Any unset override falls back to __dirname-based paths.
const HOOKS_DIR = process.env.HOOKS_DIR_OVERRIDE || __dirname;
const STATE_FILE = path.join(HOOKS_DIR, '.behavior-guard-state.json');
const RULES_FILE = process.env.BEHAVIOR_RULES_FILE_OVERRIDE || path.join(HOOKS_DIR, 'behavior-rules.json');
const DATA_DIR = path.join(HOOKS_DIR, '..', 'data');
const OBS_FILE = process.env.OBS_FILE_OVERRIDE || path.join(DATA_DIR, 'observations.jsonl');
const FIRING_LOG_FILE = path.join(DATA_DIR, 'hook_firing_log.jsonl');
const OBS_SCAN_LIMIT = 1000;
const DEPRECATED_SESSION_FLAG = '.session-rea' + 'dy';

// G62: SLO observer paths
const SLO_PENDING_DIR = path.join(HOOKS_DIR, '.slo-pending');
const SLO_VIOLATIONS_FILE = process.env.SLO_VIOLATIONS_FILE || path.join(HOOKS_DIR, '..', 'growth', 'slo_violations.jsonl');
const SLO_VIOLATIONS_MAX_BYTES = 5 * 1024 * 1024;
const GUARD_TYPE_VALIDATION_FLAG = path.join(HOOKS_DIR, '.guard-type-validation.warned');
const SLO_PENDING_TTL_MS = 5 * 60 * 1000;
// Whitelist of guard_type values implemented in mcp_tool_guard handler.
// Used by lazy unwired-guard-type detection to surface configuration drift.
const KNOWN_GUARD_TYPES = new Set([
  'requires_session', 'rate_limit', 'required_args',
  'requires_prior_tool', 'context_warning', 'duration_threshold',
]);

function normalizePath(p) { return p.replace(/\\/g, '/'); }

function isPythonFile(filePath) { return /\.py[w3i]?$/i.test(filePath); }

// Process-local cache for observations.jsonl (issue 1-4: avoid redundant reads)
let _obsLinesCache = null;
function getObservationLines() {
  if (_obsLinesCache !== null) return _obsLinesCache;
  try {
    if (!fs.existsSync(OBS_FILE)) {
      _obsLinesCache = [];
      return _obsLinesCache;
    }
    const content = fs.readFileSync(OBS_FILE, 'utf8');
    _obsLinesCache = content.trim().split('\n').slice(-OBS_SCAN_LIMIT);
  } catch {
    _obsLinesCache = [];
  }
  return _obsLinesCache;
}

// MAX_STDIN sized for memory_search tool_response bodies (G62 plan §改訂6).
const MAX_STDIN = 4 * 1024 * 1024;
let raw = '';
let shouldBlock = false;
let currentToolName = '';
let currentToolInput = {};
let currentHookEvent = '';
let currentToolUseId = '';
let state = {};

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    raw += chunk.substring(0, MAX_STDIN - raw.length);
  }
});

process.stdin.on('end', () => {
  try {
    const input = JSON.parse(raw);
    const toolName = input.tool_name || '';
    const toolInput = input.tool_input || {};
    const hookEvent = input.hook_event_name || 'PreToolUse';
    const toolUseId = input.tool_use_id || '';
    const now = Date.now();
    currentToolName = toolName;
    currentToolInput = toolInput;
    currentHookEvent = hookEvent;
    currentToolUseId = toolUseId;

    // G62: PostToolUse must NOT emit stdout (observation-logger.js owns that channel).
    // Skip context injection on PostToolUse; PreToolUse and other events keep prior behavior.
    if (hookEvent !== 'PostToolUse' && (toolName === 'Agent' || toolName === 'TeamCreate' || toolName.startsWith('mcp__'))) {
      try {
        const { execFileSync } = require('child_process');
        const scriptPath = path.join(HOOKS_DIR, 'skill_executor.py');
        const ctxType = (toolName === 'Agent' || toolName === 'TeamCreate') ? 'agent' : 'mcp';
        const subType = toolInput.subagent_type || '';
        const result = execFileSync(
          'python', [scriptPath, ctxType, toolName, subType],
          { encoding: 'utf8', timeout: 12000, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }
        );
        if (result.trim()) {
          console.log(result.trim());
        }
      } catch { /* ignore timeout/errors */ }
    }

    // Load rules
    let rules = [];
    try {
      const rulesData = JSON.parse(fs.readFileSync(RULES_FILE, 'utf8'));
      rules = rulesData.rules || [];
    } catch (e) {
      process.stderr.write(`[BehaviorGuard] CRITICAL: behavior-rules.json parse failed: ${e.message}. All operations blocked until fixed.\n`);
      process.exit(2);
    }

    // Load state (module-level variable, accessible by warn())
    state = {};
    try {
      if (fs.existsSync(STATE_FILE)) {
        state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      }
    } catch (e) {
      state = {};
      // Backup corrupted state file and reset
      try {
        const backupPath = STATE_FILE + '.corrupted.' + Date.now();
        fs.copyFileSync(STATE_FILE, backupPath);
        fs.writeFileSync(STATE_FILE, '{}', 'utf8');
        process.stderr.write(`[BehaviorGuard] WARNING: state file corrupted, backed up to ${backupPath} and reset. Error: ${e.message}\n`);
      } catch (backupErr) {
        process.stderr.write(`[BehaviorGuard] WARNING: state file corrupted and backup failed: ${backupErr.message}\n`);
      }
    }

    // Flag file writes for self-review-done and docs-read-done removed.
    // All checks now use observations.jsonl exclusively.

    // Hook profile support: minimal/standard/strict (default: strict)
    const HOOK_PROFILE = (process.env.HOOK_PROFILE || 'strict').toLowerCase();
    const MINIMAL_RULES = new Set([
      'git-revert-without-confirm', 'leader-no-code-edit'
    ]);
    const STANDARD_RULES = new Set([
      ...MINIMAL_RULES,
      'impl-without-test', 'commit-without-review', 'task-without-team',
      'agent-without-team', 'linter-config-edit-block'
    ]);

    // G37: Load eligibility config (separate file, fail-open on any error)
    const ELIGIBILITY_FILE = path.join(HOOKS_DIR, 'hook-eligibility.json');
    let ineligibleRuleIds = new Set();
    try {
      if (fs.existsSync(ELIGIBILITY_FILE)) {
        const eligData = JSON.parse(fs.readFileSync(ELIGIBILITY_FILE, 'utf8'));
        const eligRules = eligData.rules || {};
        // Check each eligibility rule via Python helper (fail-open)
        try {
          const { execFileSync } = require('child_process');
          const eligScript = path.join(HOOKS_DIR, 'hook_eligibility.py');
          if (fs.existsSync(eligScript)) {
            const wrapperScript = path.join(HOOKS_DIR, 'hook_eligibility_runner.py');
            const eligResult = execFileSync(
              'python', [wrapperScript, ELIGIBILITY_FILE],
              { encoding: 'utf8', timeout: 5000 }
            );
            const ids = JSON.parse(eligResult.trim());
            ineligibleRuleIds = new Set(ids);
          }
        } catch { /* fail-open: eligibility check error -> all eligible */ }
      }
    } catch { /* fail-open: eligibility file error -> all eligible */ }

    // Check each rule
    for (const rule of rules) {
      if (rule.disabled) continue;  // Support disabled rules
      if (ineligibleRuleIds.has(rule.id)) continue;  // G37: Skip ineligible rules

      // Profile filtering
      if (HOOK_PROFILE === 'minimal' && !MINIMAL_RULES.has(rule.id)) continue;
      if (HOOK_PROFILE === 'standard' && rule.blocking && !STANDARD_RULES.has(rule.id)) continue;
      // strict: all rules apply (default)

      if (!matchesTool(toolName, rule.trigger.tool)) continue;

      if (rule.type === 'frequency') {
        checkFrequencyRule(rule, toolName, toolInput, now, state);
      } else if (rule.type === 'pattern') {
        checkPatternRule(rule, toolName, toolInput, now, state);
      }
    }

    // Save state (atomic: tmp + rename to avoid race condition)
    try {
      const tmp = STATE_FILE + '.tmp.' + process.pid;
      fs.writeFileSync(tmp, JSON.stringify(state));
      fs.renameSync(tmp, STATE_FILE);
    } catch { /* ignore */ }

    // Write .dev-flow-state for R2 (UserPromptSubmit context injection)
    try {
      if (state._dev_flow) {
        const devFlowFile = path.join(HOOKS_DIR, '.dev-flow-state');
        const tmp = devFlowFile + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, JSON.stringify(state._dev_flow));
        fs.renameSync(tmp, devFlowFile);
      }
    } catch { /* ignore */ }

    // Pipeline 3: Coherence Alert (Agent/TeamCreate only, blocking on unsettled/disconnected)
    if (toolName === 'Agent' || toolName === 'TeamCreate') {
      try {
        const { execSync } = require('child_process');
        const alertScript = path.join(HOOKS_DIR, 'coherence_alert_runner.py');
        if (fs.existsSync(alertScript)) {
          try {
            const alertOut = execSync(
              `python "${alertScript}"`,
              { timeout: 5000, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }
            ).trim();
            if (alertOut) {
              process.stderr.write(alertOut + '\n');
            }
          } catch (execErr) {
            // exit(2) from runner means coherence is unsettled/disconnected -> block
            if (execErr.status === 2) {
              const alertOut = (execErr.stdout || '').trim();
              if (alertOut) {
                process.stderr.write(alertOut + '\n');
              }
              shouldBlock = true;
            }
            // Other exit codes (e.g. crash) -> don't block
          }
        }
      } catch { /* fs.existsSync or require failure -> pass through */ }
    }

  } catch {
    // Parse errors: pass through
  }

  process.exit(shouldBlock ? 2 : 0);
});

// =====================================================================
// G62: SLO observer helper functions (pure where possible).
// All file I/O is fail-open: any write/read error is swallowed and the
// hook returns 0 so the user-facing tool call is never delayed/blocked.
// =====================================================================

function slugifyToolUseId(id) {
  // Filename safety: keep alnum/_/./-, replace others with _.  Also bound length.
  return String(id || '').replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 128);
}

function pendingMemoPath(safeId) {
  return path.join(SLO_PENDING_DIR, safeId + '.json');
}

function ensureDir(dir) {
  try {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  } catch (_) { /* fail-open */ }
}

function writeSloPendingMemo(rule, toolName, now) {
  try {
    ensureDir(SLO_PENDING_DIR);
    let id = currentToolUseId;
    let synthetic = false;
    if (!id) {
      // Fallback synthetic key: tool_name + pid + now.  Cannot disambiguate
      // parallel calls, but observation is best-effort (G62 plan §改訂3).
      id = toolName + '_' + process.pid + '_' + now;
      synthetic = true;
    }
    const safe = slugifyToolUseId(id);
    if (!safe) return;
    const memo = {
      start_ms: now,
      tool_name: toolName,
      rule_id: rule.id,
      synthetic,
    };
    const target = pendingMemoPath(safe);
    const tmp = target + '.tmp.' + process.pid;
    fs.writeFileSync(tmp, JSON.stringify(memo));
    fs.renameSync(tmp, target);
  } catch (_) { /* fail-open: missing memo just means PostToolUse skips this call */ }
}

function loadPendingMemo(safeId) {
  try {
    const p = pendingMemoPath(safeId);
    if (!fs.existsSync(p)) return null;
    const raw = fs.readFileSync(p, 'utf8');
    const body = JSON.parse(raw);
    if (typeof body.start_ms !== 'number') return null;
    return body;
  } catch (_) { return null; }
}

function deletePendingMemo(safeId) {
  try { fs.unlinkSync(pendingMemoPath(safeId)); }
  catch (_) { /* fail-open: file may already be gone */ }
}

function cleanupOrphanPendingMemos(now) {
  // Best-effort sweep of memos older than SLO_PENDING_TTL_MS.  Bounded to
  // a single readdir + per-file stat, so cost is small.  Errors swallowed.
  try {
    if (!fs.existsSync(SLO_PENDING_DIR)) return;
    const entries = fs.readdirSync(SLO_PENDING_DIR);
    for (const name of entries) {
      try {
        const p = path.join(SLO_PENDING_DIR, name);
        const st = fs.statSync(p);
        if (now - st.mtimeMs > SLO_PENDING_TTL_MS) {
          fs.unlinkSync(p);
        }
      } catch (_) { /* skip individual entry */ }
    }
  } catch (_) { /* fail-open */ }
}

function getSessionId() {
  // Mirror observation-logger.js so violation entries align with observation entries.
  let sid = process.env.CLAUDE_SESSION_ID || '';
  if (!sid) {
    try {
      const sstFile = path.join(HOOKS_DIR, '.session-start-time');
      const epoch = fs.readFileSync(sstFile, 'utf8').trim();
      sid = 's' + epoch;
    } catch (_) { sid = 'unknown'; }
  }
  return sid.substring(0, 32);
}

function appendSloViolation(entry) {
  try {
    const dir = path.dirname(SLO_VIOLATIONS_FILE);
    ensureDir(dir);
    let fd;
    try {
      fd = fs.openSync(SLO_VIOLATIONS_FILE, 'a');
      const stats = fs.fstatSync(fd);
      if (stats.size > SLO_VIOLATIONS_MAX_BYTES) {
        fs.closeSync(fd);
        fd = null;
        const archive = SLO_VIOLATIONS_FILE.replace('.jsonl', '.' + Date.now() + '.jsonl');
        try { fs.renameSync(SLO_VIOLATIONS_FILE, archive); } catch (_) { /* race ok */ }
        fd = fs.openSync(SLO_VIOLATIONS_FILE, 'a');
      }
      fs.writeSync(fd, JSON.stringify(entry) + '\n');
    } finally {
      if (fd !== undefined && fd !== null) {
        try { fs.closeSync(fd); } catch (_) { /* ignore */ }
      }
    }
  } catch (_) { /* fail-open */ }
}

function evaluateSloDuration(rule, thresholdSeconds, now, state) {
  // PostToolUse handler: lookup memo by tool_use_id, compute elapsed,
  // persist violation, emit suppressed WARN.  Memo deleted at end (fail-open).
  const id = currentToolUseId;
  if (!id) {
    // Without a stable id we cannot match a memo; fail-open by skipping.
    return;
  }
  const safe = slugifyToolUseId(id);
  if (!safe) return;
  const memo = loadPendingMemo(safe);
  if (!memo) {
    // No matching memo — observation gap.  Fail-open silently.
    return;
  }
  // Always delete memo: even under-threshold, the call has completed.
  deletePendingMemo(safe);

  const elapsedMs = Math.max(0, now - memo.start_ms);
  const elapsedSeconds = elapsedMs / 1000;
  if (elapsedSeconds <= thresholdSeconds) return;

  const entry = {
    ts: new Date(now).toISOString(),
    session: getSessionId(),
    tool_name: memo.tool_name || currentToolName,
    tool_use_id: id,
    elapsed_seconds: elapsedSeconds,
    threshold_seconds: thresholdSeconds,
    rule_id: rule.id,
    message: rule.message || 'SLO threshold exceeded',
    synthetic: !!memo.synthetic,
  };
  appendSloViolation(entry);

  // Suppression window: rule_id-scoped frequency cap (G62 plan #6).
  const ruleId = rule.id;
  const windowMs = (rule.trigger.window_minutes || 10) * 60 * 1000;
  const maxWarns = rule.trigger.max_warns || 3;
  if (!state[ruleId]) state[ruleId] = {};
  if (!Array.isArray(state[ruleId].slo_warns)) state[ruleId].slo_warns = [];
  state[ruleId].slo_warns = state[ruleId].slo_warns.filter(t => now - t < windowMs);
  if (state[ruleId].slo_warns.length < maxWarns) {
    state[ruleId].slo_warns.push(now);
    process.stderr.write('[BehaviorGuard] SLO: ' + (rule.message || 'threshold exceeded') +
      ' (elapsed=' + elapsedSeconds.toFixed(1) + 's, threshold=' + thresholdSeconds + 's)\n');
  }
  // Otherwise: persisted to log but stderr suppressed (asymmetric design).
}

function emitInvalidThresholdWarn(ruleId) {
  // Fire once per session per ruleId via flag file.
  try {
    ensureDir(HOOKS_DIR);
    const flag = path.join(HOOKS_DIR, '.slo-invalid-threshold.' + slugifyToolUseId(ruleId) + '.warned');
    if (fs.existsSync(flag)) return;
    fs.writeFileSync(flag, String(Date.now()));
    process.stderr.write('[BehaviorGuard] CONFIG WARN: rule "' + ruleId +
      '" has invalid/missing threshold_seconds; rule disabled until fixed.\n');
  } catch (_) { /* fail-open */ }
}

function emitUnwiredGuardTypeWarn(guardType, ruleId) {
  // G62 self-validation: rule references a guard_type with no handler branch.
  // One-shot per session via flag file.
  try {
    if (fs.existsSync(GUARD_TYPE_VALIDATION_FLAG)) return;
    ensureDir(HOOKS_DIR);
    fs.writeFileSync(GUARD_TYPE_VALIDATION_FLAG, JSON.stringify({
      guard_type: guardType, rule_id: ruleId, ts: new Date().toISOString(),
    }));
    process.stderr.write('[BehaviorGuard] CONFIG WARN: rule "' + ruleId +
      '" uses unwired guard_type "' + guardType + '" — handler missing.\n');
  } catch (_) { /* fail-open */ }
}

function matchesTool(toolName, pattern) {
  if (pattern === '*') return true;
  return pattern.split('|').some(p => p.trim() === toolName);
}

function checkFrequencyRule(rule, toolName, toolInput, now, state) {
  const ruleId = rule.id;
  const windowMs = (rule.trigger.window_minutes || 5) * 60 * 1000;
  const threshold = rule.trigger.threshold || 3;
  const condition = rule.trigger.condition;

  // Check condition
  if (condition === 'new_file') {
    if (!toolInput.file_path || fs.existsSync(toolInput.file_path)) return;
  }

  // same_cmd: same bash command repeated (first 40 chars as key)
  if (condition === 'same_cmd') {
    const cmd = (toolInput.command || '').substring(0, 40);
    if (!cmd) return;
    // Test execution commands are expected to repeat (TDD workflow)
    if (/pytest|python\s+-m\s+pytest|bash\s+test_|unittest/.test(cmd)) return;
    const stateKey = ruleId + ':' + cmd;
    if (!state[stateKey]) state[stateKey] = { timestamps: [] };
    state[stateKey].timestamps = state[stateKey].timestamps.filter(t => now - t < windowMs);
    if (state[stateKey].timestamps.length >= threshold) {
      warn(rule);
    } else {
      state[stateKey].timestamps.push(now);
    }
    return;
  }

  // Initialize state for this rule
  if (!state[ruleId]) state[ruleId] = { timestamps: [] };

  // Clean old entries
  state[ruleId].timestamps = state[ruleId].timestamps.filter(t => now - t < windowMs);

  // Check threshold first, only record if not exceeded
  if (state[ruleId].timestamps.length >= threshold) {
    warn(rule);
  } else {
    state[ruleId].timestamps.push(now);
  }
}

function checkPatternRule(rule, toolName, toolInput, now, state) {
  const condition = rule.trigger.condition;

  // G62 follow-up: PostToolUse only allows duration_threshold mcp_tool_guard.
  // Other pattern conditions (memory_search_without_session_start, suggest_*,
  // task_without_team, etc.) are PreToolUse-only by design.  matchesTool() upstream
  // narrows to rules whose tool matcher matched the PostToolUse target; their
  // conditions are semantically pre-call and skipped here.
  if (currentHookEvent === 'PostToolUse') {
    if (condition !== 'mcp_tool_guard') return;
    if ((rule.trigger.guard_type || '') !== 'duration_threshold') return;
  }

  if (condition === 'py_file') {
    const filePath = toolInput.file_path || '';
    if (isPythonFile(filePath)) {
      // Skip if implementer was recently launched (same check as leader_no_code_edit)
      const leaderState = state['leader-no-code-edit'] || {};
      const lastImpl = leaderState.last_implementer_time || 0;
      const implWindowMs = 30 * 60 * 1000; // 30 minutes
      if (now - lastImpl < implWindowMs) return; // implementer active, allow
      warn(rule);
    }
  } else if (condition === 'git_revert_cmd') {
    const cmd = (toolInput.command || '').toLowerCase();
    if (cmd.includes('git checkout') || cmd.includes('git restore')) {
      if (cmd.includes('--') || cmd.includes('head') || cmd.includes('git restore')) {
        warn(rule);
      }
    }
  } else if (condition === 'commit_without_review') {
    const cmd = (toolInput.command || '');
    if (/^\s*git\s+commit/.test(cmd)) {
      // Access shared _dev_flow state
      const stateKey = '_dev_flow';
      if (!state[stateKey]) state[stateKey] = {};
      const df = state[stateKey];
      const implTime = df.impl || 0;
      const postAnalysisTime = df.post_analysis || 0;
      const reviewerTime = df.reviewer || 0;
      // Exception: if impl_time is 0 (no impl in this session), allow commit
      if (implTime > 0) {
        const missing = [];
        if (postAnalysisTime < implTime) missing.push('post-impl analysis');
        if (reviewerTime < implTime) missing.push('reviewer');
        if (missing.length > 0) {
          const dynamicMsg = `git commitをブロック。実装後の${missing.join(' + ')}が未実施です。正規フロー: ...→実装→解析→レビュー→コミット`;
          warn(rule, dynamicMsg);
        }
      }
    }
  } else if (condition === 'commit_with_review_issues') {
    const cmd = (toolInput.command || '');
    if (/^\s*git\s+commit/.test(cmd)) {
      const stateKey = '_dev_flow';
      if (!state[stateKey]) state[stateKey] = {};
      const df = state[stateKey];
      const pending = df.review_issues_pending;
      if (pending && pending.count > 0) {
        const summary = pending.summary || `${pending.count} issues`;
        const dynamicMsg = `git commitをブロック。reviewerが検出した問題が未解決です（${summary}）。修正後にreviewerを再実行してください`;
        warn(rule, dynamicMsg);
      }
    }
  } else if (condition === 'task_without_team') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};
    if (toolName === 'TeamCreate') {
      // Record TeamCreate time, no warning
      state[ruleId].last_team_create_time = now;
    } else {
      // TaskCreate or TaskUpdate: warn if no recent TeamCreate
      const lastTeam = state[ruleId].last_team_create_time || 0;
      const windowMs = (rule.trigger.window_minutes || 5) * 60 * 1000;
      if (now - lastTeam > windowMs) {
        warn(rule);
      }
    }
  } else if (condition === 'agent_no_claude_read') {
    const prompt = toolInput.prompt || '';
    const subagentType = toolInput.subagent_type || '';
    // Skip custom agents (they have their own guides)
    const customAgents = ['thinker', 'reviewer', 'planner', 'claude-code-guide'];
    if (customAgents.includes(subagentType)) return;
    // Check if prompt mentions CLAUDE.md
    if (!prompt.includes('CLAUDE.md')) {
      warn(rule);
    }
  } else if (condition === 'write_after_reference') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};
    const windowMs = (rule.trigger.window_minutes || 10) * 60 * 1000;
    if (toolName === 'WebFetch' || toolName === 'WebSearch') {
      state[ruleId].last_reference_time = now;
    } else if (toolName === 'Write') {
      const lastRef = state[ruleId].last_reference_time || 0;
      if (now - lastRef < windowMs && toolInput.file_path && !fs.existsSync(toolInput.file_path)) {
        warn(rule);
      }
    }
  } else if (condition === 'designer_before_planner' || condition === 'impl_without_analysis' ||
             condition === 'planner_before_impl' ||
             condition === 'post_impl_analysis_required' || condition === 'reviewer_required' ||
             condition === 'thinker_before_fix') {
    // Shared dev-flow state with namespaced sub-keys per phase
    // Fields: { design, planner, pre_analysis, impl, post_analysis, reviewer }
    const stateKey = '_dev_flow';
    if (!state[stateKey]) state[stateKey] = {};
    const df = state[stateKey];

    const prompt = (toolInput.prompt || '').toLowerCase();
    const desc = (toolInput.description || '').toLowerCase();
    const subType = (toolInput.subagent_type || '').toLowerCase();
    const combined = prompt + ' ' + desc;

    // When subType is non-empty, use ONLY subType for detection (avoid keyword false positives).
    // Keyword fallback only when subType is empty.
    const isDesign = subType ? subType === 'designer' : /設計|design/.test(combined);
    const isPlanner = subType ? subType === 'planner' : /計画|plan/.test(combined);
    const isAnalysis = subType ? subType === 'analyzer' : /解析|analy|リスク/.test(combined);
    const isImpl = subType ? subType === 'implementer' : /実装|implement|コード実装/.test(combined);
    const isReviewer = subType ? subType === 'reviewer' : /レビュー|review|品質/.test(combined);
    const isThinker = subType === 'thinker';
    let isFix = /修正|fix|レビュー指摘|review issue/.test(combined);
    // Only apply isFix to implementer or unknown subType (keyword fallback).
    // Non-implementation agents (analyzer, designer, planner, reviewer, thinker)
    // must not trigger thinker_before_fix to avoid self-referential blocking.
    if (subType && subType !== 'implementer') isFix = false;

    // --- Phase state updates (shared across all rules) ---
    if (isDesign) {
      df.design = now;
      df.pre_analysis = 0; // Reset: new cycle needs fresh pre-analysis
      df.planner = 0;      // Reset: new cycle needs fresh planner
      df.impl = 0;          // New cycle: previous impl is irrelevant
      df.post_analysis = 0;  // New cycle: previous post_analysis is irrelevant
      df.reviewer = 0;       // New cycle: previous reviewer is irrelevant
    } else if (isPlanner) {
      df.planner = now;
    } else if (isAnalysis) {
      df.pre_analysis = now;
      df.post_analysis = now;
    } else if (isImpl) {
      df.impl = now;
      df.reviewer = 0; // Reset: new impl needs fresh review
      df.review_issues_pending = null; // New impl cycle: previous review issues invalidated
    } else if (isReviewer) {
      df.reviewer = now;
      df.review_issues_pending = null; // Reviewer re-run: clear pending (assume clean until externally set)
    }
    if (isThinker) {
      df.thinker = now;
    }

    // --- Per-rule blocking evaluation ---
    if (condition === 'designer_before_planner') {
      if (isPlanner) {
        const lastDesign = df.design || 0;
        if (lastDesign === 0) {
          warn(rule);
        }
      }
    } else if (condition === 'impl_without_analysis') {
      if (isImpl) {
        const lastDesign = df.design || 0;
        const lastPreAnalysis = df.pre_analysis || 0;
        if (lastDesign > 0 && lastPreAnalysis < lastDesign) {
          warn(rule);
        }
      }
    } else if (condition === 'planner_before_impl') {
      if (isImpl) {
        const lastDesign = df.design || 0;
        const lastPlanner = df.planner || 0;
        if (lastDesign > 0 && lastPlanner < lastDesign) {
          warn(rule);
        }
      }
    } else if (condition === 'post_impl_analysis_required') {
      if (isDesign) {
        const lastImpl = df.impl || 0;
        const lastPostAnalysis = df.post_analysis || 0;
        if (lastImpl > 0 && lastPostAnalysis < lastImpl) {
          warn(rule);
        }
      }
    } else if (condition === 'reviewer_required') {
      if (isDesign) {
        const lastImpl = df.impl || 0;
        const lastPostAnalysis = df.post_analysis || 0;
        const lastReviewer = df.reviewer || 0;
        if (lastImpl > 0 && lastPostAnalysis > lastImpl && lastReviewer < lastPostAnalysis) {
          warn(rule);
        }
      }
    } else if (condition === 'thinker_before_fix') {
      if (isFix) {
        const lastPostAnalysis = df.post_analysis || 0;
        const lastThinker = df.thinker || 0;
        if (lastPostAnalysis > 0 && lastThinker < lastPostAnalysis) {
          warn(rule);
        }
      }
    }
  } else if (condition === 'agent_without_team') {
    if (toolName === 'TeamCreate') {
      return; // TeamCreate itself should not be blocked
    }
    // Agent tool: check observations.jsonl for TeamCreate + teams/ directory fallback
    if (toolName === 'Agent') {
      // Skip lightweight exploration agents
      const subType = (toolInput.subagent_type || '').toLowerCase();
      const skipAgents = ['explore', 'thinker'];
      if (skipAgents.includes(subType)) return;

      let hasTeam = false;

      // Primary: check observations.jsonl for TeamCreate in current session (cached)
      try {
        const lines = getObservationLines();
        if (lines.length > 0) {
          const sessionTimeFile = path.join(HOOKS_DIR, '.session-start-time');
          let sessionStartTime = 0;
          try {
            if (fs.existsSync(sessionTimeFile)) {
              sessionStartTime = parseInt(fs.readFileSync(sessionTimeFile, 'utf8').trim(), 10) || 0;
            }
          } catch { /* ignore */ }
          const sessionFilterTime = sessionStartTime > 0 ? sessionStartTime - 300000 : 0;

          for (const line of lines) {
            try {
              const obs = JSON.parse(line);
              if (obs.tool === 'TeamCreate') {
                const obsTime = new Date(obs.ts).getTime();
                if (sessionFilterTime === 0 || obsTime >= sessionFilterTime) {
                  hasTeam = true;
                  break;
                }
              }
            } catch { /* skip */ }
          }
        }
      } catch { /* ignore */ }

      // Fallback: check if any team directory exists with session-aware validation
      if (!hasTeam) {
        try {
          const teamsDir = path.join(HOOKS_DIR, '..', 'teams');
          if (fs.existsSync(teamsDir)) {
            const teams = fs.readdirSync(teamsDir);
            if (teams.length > 0) {
              const sessionTimeFile = path.join(HOOKS_DIR, '.session-start-time');
              let sessionStartTime = 0;
              let hasSessionFlag = false;
              try {
                if (fs.existsSync(sessionTimeFile)) {
                  const parsed = parseInt(fs.readFileSync(sessionTimeFile, 'utf8').trim(), 10);
                  if (parsed > 0) {
                    hasSessionFlag = true;
                    sessionStartTime = parsed;
                  }
                }
              } catch { /* ignore */ }

              if (!hasSessionFlag) {
                hasTeam = true;
              } else {
                for (const teamName of teams) {
                  try {
                    const configPath = path.join(teamsDir, teamName, 'config.json');
                    if (fs.existsSync(configPath)) {
                      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
                      if (config.createdAt) {
                        const createdAtMs = new Date(config.createdAt).getTime();
                        if (createdAtMs >= sessionStartTime) {
                          hasTeam = true;
                          break;
                        }
                      }
                    }
                  } catch { /* skip unreadable config */ }
                }
              }
            }
          }
        } catch { /* ignore */ }
      }
      if (!hasTeam) {
        warn(rule);
      }
    }
  } else if (condition === 'mcp_server_limit') {
    const filePath = toolInput.file_path || '';
    if (path.basename(filePath) === '.mcp.json' && toolInput.content) {
      try {
        const newConfig = JSON.parse(toolInput.content);
        const servers = Object.keys(newConfig.mcpServers || {});
        const maxServers = rule.trigger.max_servers || 10;
        if (servers.length > maxServers) {
          warn(rule);
        }
      } catch { /* invalid JSON, let it through */ }
    }
  } else if (condition === 'memory_search_without_session_start') {
    // Check observations.jsonl for session_start in current session (cached)
    let hasSessionStart = false;
    try {
      const lines = getObservationLines();
      if (lines.length > 0) {
        const sessionTimeFile = path.join(HOOKS_DIR, '.session-start-time');
        let sessionStartTime = 0;
        try {
          if (fs.existsSync(sessionTimeFile)) {
            sessionStartTime = parseInt(fs.readFileSync(sessionTimeFile, 'utf8').trim(), 10) || 0;
          }
        } catch { /* ignore */ }
        const sessionFilterTime = sessionStartTime > 0 ? sessionStartTime - 300000 : 0;

        for (const line of lines) {
          try {
            const obs = JSON.parse(line);
            if (obs.tool === 'mcp__memory-tools__session_start') {
              const obsTime = new Date(obs.ts).getTime();
              if (sessionFilterTime === 0 || obsTime >= sessionFilterTime) {
                hasSessionStart = true;
                break;
              }
            }
          } catch { /* skip */ }
        }
      }
    } catch { /* ignore */ }

    if (!hasSessionStart) {
      warn(rule);
    }
  } else if (condition === 'agent_without_memory_search') {
    // Skip only for lightweight exploration agents
    const subType = (toolInput.subagent_type || '').toLowerCase();
    const skipAgents = ['explore', 'thinker'];
    if (skipAgents.includes(subType)) return;

    const windowMs = (rule.trigger.window_minutes || 15) * 60 * 1000;
    let lastMemorySearch = 0;

    // Check observations.jsonl only (cached)
    try {
      const lines = getObservationLines();
      for (const line of lines) {
        try {
          const obs = JSON.parse(line);
          if (obs.tool === 'mcp__memory-tools__memory_search') {
            const t = new Date(obs.ts).getTime();
            if (t > lastMemorySearch) lastMemorySearch = t;
          }
        } catch { /* skip */ }
      }
    } catch { /* ignore */ }

    if (now - lastMemorySearch > windowMs) {
      warn(rule);
    }
  } else if (condition === 'agent_without_docs_read') {
    // Skip only for lightweight exploration agents
    const subType = (toolInput.subagent_type || '').toLowerCase();
    const skipAgents = ['explore', 'thinker'];
    if (skipAgents.includes(subType)) return;

    const windowMs = (rule.trigger.window_minutes || 60) * 60 * 1000;
    let lastDocsRead = 0;

    // Check observations.jsonl only (cached)
    try {
      const lines = getObservationLines();
      for (const line of lines) {
        try {
          const obs = JSON.parse(line);
          if (obs.tool === 'Read') {
            const fp = (obs.params && obs.params.file) || '';
            if (normalizePath(fp).includes('docs/INDEX.md') || fp.includes('gap_analysis')) {
              const t = new Date(obs.ts).getTime();
              if (t > lastDocsRead) lastDocsRead = t;
            }
          }
        } catch { /* skip */ }
      }
    } catch { /* ignore */ }

    if (now - lastDocsRead > windowMs) {
      warn(rule);
    }
  } else if (condition === 'agent_without_self_review') {
    // Skip only for lightweight exploration agents
    const subType = (toolInput.subagent_type || '').toLowerCase();
    const skipAgents = ['explore', 'thinker'];
    if (skipAgents.includes(subType)) return;

    const windowMs = (rule.trigger.window_minutes || 30) * 60 * 1000;
    let lastSelfReview = 0;

    // Check observations.jsonl for stm_write with category=self_review (cached)
    try {
      const lines = getObservationLines();
      for (const line of lines) {
        try {
          const obs = JSON.parse(line);
          if (obs.tool === 'mcp__memory-tools__stm_write' && obs.params && obs.params.category === 'self_review') {
            const t = new Date(obs.ts).getTime();
            if (t > lastSelfReview) lastSelfReview = t;
          }
        } catch { /* skip */ }
      }
    } catch { /* ignore */ }

    if (now - lastSelfReview > windowMs) {
      warn(rule);
    }
  } else if (condition === 'impl_without_test') {
    const filePath = toolInput.file_path || '';
    const baseName = path.basename(filePath);
    // Only check .py files that are NOT test files
    if (isPythonFile(filePath) && !baseName.startsWith('test_') && !baseName.startsWith('conftest')) {
      const ruleId = rule.id;
      if (!state[ruleId]) state[ruleId] = {};
      const lastTestEdit = state[ruleId].last_test_edit_time || 0;
      const windowMs = (rule.trigger.window_minutes || 10) * 60 * 1000;
      if (now - lastTestEdit > windowMs) {
        warn(rule);
      }
    }
    // Track test file edits
    if (isPythonFile(filePath) && (path.basename(filePath).startsWith('test_') || path.basename(filePath).startsWith('conftest'))) {
      const ruleId = rule.id;
      if (!state[ruleId]) state[ruleId] = {};
      state[ruleId].last_test_edit_time = now;
    }
  } else if (condition === 'leader_no_code_edit') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};
    const windowMs = (rule.trigger.window_minutes || 30) * 60 * 1000;

    // Track implementer launches from Agent tool
    if (toolName === 'Agent' || toolName === 'TeamCreate') {
      const subType = (toolInput.subagent_type || '').toLowerCase();
      const desc = (toolInput.description || '').toLowerCase();
      const prompt = (toolInput.prompt || '').toLowerCase();
      if (subType === 'implementer' || /実装|implement/.test(desc + ' ' + prompt)) {
        state[ruleId].last_implementer_time = now;
      }
      return; // Agent/TeamCreate itself should not be blocked
    }

    // Check file extension for Edit/Write
    const filePath = toolInput.file_path || '';
    const ext = path.extname(filePath).toLowerCase();
    const codeExtensions = ['.js', '.py', '.ts', '.sh'];
    if (codeExtensions.includes(ext)) {
      const lastImpl = state[ruleId].last_implementer_time || 0;
      if (now - lastImpl > windowMs) {
        warn(rule);
      }
    }
  } else if (condition === 'linter_config_edit') {
    const filePath = (toolInput.file_path || '').replace(/\\/g, '/');
    const basename = path.basename(filePath);
    const isRuffConfig = basename === 'ruff.toml';
    const isPyprojectRuff = basename === 'pyproject.toml' &&
      (toolInput.new_string || '').includes('[tool.ruff]');
    if (isRuffConfig || isPyprojectRuff) {
      warn(rule);
    }
  } else if (condition === 'protected_file_edit') {
    if (process.env.BEHAVIOR_GUARD_SELF_UPDATE === '1') return;
    const filePath = (toolInput.file_path || '').replace(/\\/g, '/');
    const basename = path.basename(filePath);
    const protectedPaths = rule.trigger.protected_paths || [];
    for (const protectedName of protectedPaths) {
      if (basename === protectedName || filePath.endsWith('/' + protectedName)) {
        warn(rule);
        break;
      }
    }
  } else if (condition === 'py_write_without_doc') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = { pending_py: [] };
    if (!state[ruleId].pending_py) state[ruleId].pending_py = [];
    const filePath = toolInput.file_path || '';
    const timeoutMs = (rule.trigger.timeout_minutes || 10) * 60 * 1000;

    // Check if editing CLAUDE.md or MEMORY.md → clear matching pendings
    if (filePath.endsWith('CLAUDE.md') || filePath.endsWith('MEMORY.md')) {
      state[ruleId].pending_py = [];
      return;
    }

    // If writing a new .py file, add to pending (exclude test files and coding_tests/)
    const normalizedFilePath = normalizePath(filePath);
    const isTestFile = path.basename(filePath).startsWith('test_') || normalizedFilePath.includes('coding_tests/') || normalizedFilePath.includes('/tests/');
    if (toolName === 'Write' && isPythonFile(filePath) && !fs.existsSync(filePath) && !isTestFile) {
      state[ruleId].pending_py.push({ path: filePath, time: now });
    }

    // Check for expired pendings
    const expired = state[ruleId].pending_py.filter(p => now - p.time > timeoutMs);
    if (expired.length > 0) {
      warn(rule);
      // Keep only non-expired (don't re-warn endlessly)
      state[ruleId].pending_py = state[ruleId].pending_py.filter(p => now - p.time <= timeoutMs);
    }
  } else if (condition === 'completion_evidence_required') {
    // Check SendMessage for implementer completion without test evidence
    if (toolName === 'SendMessage') {
      const content = (toolInput.content || toolInput.message || '').toLowerCase();
      const to = (toolInput.to || '').toLowerCase();
      // Only check messages that look like completion reports
      const completionKeywords = ['完了', 'done', 'complete', 'finished', '実装完了'];
      const isCompletion = completionKeywords.some(kw => content.includes(kw));
      if (isCompletion) {
        // Check for test evidence
        const hasTestEvidence = /(\d+\s*(passed|failed|pass|fail))|test.*result|テスト.*結果|(passed|failed).*\d+/.test(content);
        if (!hasTestEvidence) {
          warn(rule);
        }
      }
    }
  } else if (condition === 'commit_without_diff') {
    const cmd = (toolInput.command || '');
    if (/^\s*git\s+commit/.test(cmd)) {
      const ruleId = rule.id;
      if (!state[ruleId]) state[ruleId] = {};
      const lastDiffTime = state[ruleId].last_diff_time || 0;
      // Block if no git diff/show was run in this session
      if (lastDiffTime === 0) {
        warn(rule);
      }
    }
    // Track git diff / git show commands
    if (/^\s*git\s+(diff|show)\b/.test(cmd)) {
      const ruleId = rule.id;
      if (!state[ruleId]) state[ruleId] = {};
      state[ruleId].last_diff_time = now;
    }
  } else if (condition === 'hook_change_without_fire_test') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};

    // Track hooks file edits (Edit/Write to hooks/*.js or hooks/*.py)
    if (toolName === 'Edit' || toolName === 'Write') {
      const filePath = (toolInput.file_path || '').replace(/\\/g, '/');
      if (filePath.includes('/hooks/') && (/\.js$/.test(filePath) || /\.py$/.test(filePath))) {
        state[ruleId].hooks_changed = true;
        state[ruleId].hooks_changed_time = now;
      }
    }
    // Track test execution
    if (toolName === 'Bash') {
      const cmd = (toolInput.command || '');
      if (cmd.includes('test_behavior_guard')) {
        state[ruleId].last_test_time = now;
      }
      // Check on git commit
      if (/^\s*git\s+commit/.test(cmd)) {
        const hooksChanged = state[ruleId].hooks_changed || false;
        const lastTest = state[ruleId].last_test_time || 0;
        const changedTime = state[ruleId].hooks_changed_time || 0;
        if (hooksChanged && lastTest < changedTime) {
          warn(rule);
        }
      }
    }
  } else if (condition === 'team_without_task') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = { team_created: false, task_count: 0 };
    if (toolName === 'TeamCreate') {
      state[ruleId].team_created = true;
      state[ruleId].task_count = 0;
    } else if (toolName === 'TaskCreate') {
      state[ruleId].task_count = (state[ruleId].task_count || 0) + 1;
    } else if (toolName === 'Agent') {
      // Skip lightweight agents
      const subType = (toolInput.subagent_type || '').toLowerCase();
      const skipAgents = ['explore', 'thinker'];
      if (!skipAgents.includes(subType)) {
        if (state[ruleId].team_created && state[ruleId].task_count === 0) {
          // Fallback: check if tasks directory has task files
          // Check both project-local and ~/.claude/tasks/
          const tasksDirs = [
            path.join(HOOKS_DIR, '..', 'tasks'),
            path.join(process.env.HOME || '', '.claude', 'tasks')
          ];
          let hasTaskFiles = false;
          for (const tasksDir of tasksDirs) {
            try {
              if (fs.existsSync(tasksDir)) {
                const teamDirs = fs.readdirSync(tasksDir);
                for (const dir of teamDirs) {
                  const teamTaskDir = path.join(tasksDir, dir);
                  const stats = fs.statSync(teamTaskDir);
                  if (stats.isDirectory()) {
                    const files = fs.readdirSync(teamTaskDir);
                    if (files.some(f => f.endsWith('.json'))) {
                      hasTaskFiles = true;
                      break;
                    }
                  }
                }
              }
            } catch { /* ignore */ }
            if (hasTaskFiles) break;
          }
          if (!hasTaskFiles) {
            warn(rule);
          }
        }
      }
    }
  } else if (condition === 'lesson_without_prevention') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};

    // Track lessons_registry.md edits
    if (toolName === 'Edit' || toolName === 'Write') {
      const filePath = (toolInput.file_path || '').replace(/\\/g, '/');
      if (filePath.includes('lessons_registry') && filePath.endsWith('.md')) {
        state[ruleId].lesson_edited = true;
        state[ruleId].lesson_edited_time = now;
      }
      // Track prevention measures: behavior-rules.json or hooks/ file edits
      if (filePath.includes('behavior-rules.json') ||
          (filePath.includes('/hooks/') && (/\.js$/.test(filePath) || /\.py$/.test(filePath) || /\.sh$/.test(filePath)))) {
        state[ruleId].prevention_added = true;
        state[ruleId].prevention_time = now;
      }
    }
    // Check on git commit
    if (toolName === 'Bash') {
      const cmd = (toolInput.command || '');
      if (/^\s*git\s+commit/.test(cmd)) {
        const lessonEdited = state[ruleId].lesson_edited || false;
        const preventionAdded = state[ruleId].prevention_added || false;
        if (lessonEdited && !preventionAdded) {
          warn(rule);
        }
      }
    }
  } else if (condition === 'deprecated_pattern_check') {
    const filePath = normalizePath(toolInput.file_path || '');
    // Exclude documentation (.md) and config (.json) files — only check code files
    if (filePath.endsWith('.md') || filePath.endsWith('.json')) return;
    // Check file content for deprecated flag file reference (only new introductions)
    const newContent = toolInput.new_string || toolInput.content || '';
    const oldContent = toolInput.old_string || '';
    if (newContent.includes(DEPRECATED_SESSION_FLAG) && !oldContent.includes(DEPRECATED_SESSION_FLAG)) {
      warn(rule);
    }
  } else if (condition === 'defer_check') {
    // Only fire when reviewer has been run (review findings exist to defer)
    if (state._dev_flow && state._dev_flow.reviewer > 0) {
      let text = '';
      if (toolName === 'Agent') {
        text = ((toolInput.prompt || '') + ' ' + (toolInput.description || '')).toLowerCase();
      } else if (toolName === 'Bash') {
        const cmd = (toolInput.command || '');
        if (/^\s*git\s+commit/.test(cmd)) {
          text = cmd.toLowerCase();
        }
      }
      if (text && /follow[\s-]?up|\blater\b|次サイクル|低優先/.test(text)) {
        warn(rule);
      }
    }
  } else if (condition === 'cycle_completion_continue') {
    // Block session_end when cycle is complete but gaps remain
    // Reads .dev-flow-state: reviewer > 0 && impl > 0 => cycle complete
    // Then checks gap_analysis_*.md for remaining (non-completed) gaps
    try {
      const stateKey = '_dev_flow';
      if (!state[stateKey]) state[stateKey] = {};
      const df = state[stateKey];

      // Also read from file in case state is empty (process restarted)
      let implTime = df.impl || 0;
      let reviewerTime = df.reviewer || 0;
      if (implTime === 0 || reviewerTime === 0) {
        try {
          const devFlowFile = path.join(HOOKS_DIR, '.dev-flow-state');
          if (fs.existsSync(devFlowFile)) {
            const dfData = JSON.parse(fs.readFileSync(devFlowFile, 'utf8'));
            implTime = dfData.impl || 0;
            reviewerTime = dfData.reviewer || 0;
          }
        } catch { /* ignore */ }
      }

      if (implTime > 0 && reviewerTime > 0) {
        // G43: Cycle is complete. Check review_issues_pending before reset.
        const pending = df.review_issues_pending;
        const hasPendingIssues = pending && pending.count > 0;
        if (!hasPendingIssues) {
          // Cycle confirmed complete: reset dev-flow-state for next cycle
          resetDevFlowState(state);
        }

        // Check if gaps remain in gap_analysis_*.md
        const docsDir = path.join(HOOKS_DIR, '..', 'docs');
        let hasRemainingGaps = false;
        try {
          if (fs.existsSync(docsDir)) {
            const gapFiles = fs.readdirSync(docsDir)
              .filter(f => f.startsWith('gap_analysis') && f.endsWith('.md'))
              .sort()
              .reverse();
            if (gapFiles.length > 0) {
              const gapContent = fs.readFileSync(path.join(docsDir, gapFiles[0]), 'utf8');
              // Match gap rows: | G<num> <title> | desc | priority | status |
              const gapLines = gapContent.split('\n').filter(line => {
                const m = line.match(/^\|\s*G\d+\s+.+?\|.+?\|.+?\|\s*(.+?)\s*\|/);
                if (m) {
                  const status = m[1].trim();
                  return !status.includes('完了') && !status.toLowerCase().includes('done') && !status.includes('完成');
                }
                return false;
              });
              hasRemainingGaps = gapLines.length > 0;
            }
          }
        } catch { /* ignore */ }

        if (hasRemainingGaps) {
          warn(rule);
        }
      }
    } catch (e) {
      process.stderr.write(`[BehaviorGuard] cycle_completion_continue error: ${e.message}\n`);
    }
  } else if (condition === 'mcp_tool_guard') {
    // ===========================================
    // 汎用MCPツールガードハンドラ（Phase 0 + G62 duration_threshold）
    // guard_type: requires_session | rate_limit | required_args | requires_prior_tool | context_warning | duration_threshold
    // ===========================================
    try {
      const guardType = rule.trigger.guard_type;

      // G62 follow-up: function-entry skip already filters PostToolUse non-duration rules.
      // Symmetric: duration_threshold splits across hook events.
      //   PreToolUse → write a short-lived memo (start time) and return.
      //   PostToolUse → read memo, compute elapsed, persist + WARN.
      if (currentHookEvent !== 'PostToolUse' && guardType === 'duration_threshold') {
        // PreToolUse: validate threshold once (so we don't write orphans for misconfigured rules),
        // then record start_ms keyed by tool_use_id (or synthetic fallback).
        const _ths = rule.trigger.threshold_seconds;
        if (typeof _ths !== 'number' || !isFinite(_ths) || _ths <= 0) {
          emitInvalidThresholdWarn(rule.id);
          return;
        }
        writeSloPendingMemo(rule, toolName, now);
        cleanupOrphanPendingMemos(now);
        return;
      }

      // G62 self-validation: detect unwired guard_types and surface a one-shot WARN.
      if (guardType && !KNOWN_GUARD_TYPES.has(guardType)) {
        emitUnwiredGuardTypeWarn(guardType, rule.id);
        return;
      }

      if (guardType === 'requires_session') {
        // Check observations.jsonl for session_start completion (cached per process)
        if (typeof checkPatternRule._sessionStartCache === 'undefined') {
          checkPatternRule._sessionStartCache = null; // null = not yet checked
        }
        let hasSessionStart = checkPatternRule._sessionStartCache;
        if (hasSessionStart === null) {
          hasSessionStart = false;
          try {
            const lines = getObservationLines();
            if (lines.length > 0) {
              const sessionTimeFile = path.join(HOOKS_DIR, '.session-start-time');
              let sessionStartTime = 0;
              try {
                if (fs.existsSync(sessionTimeFile)) {
                  sessionStartTime = parseInt(fs.readFileSync(sessionTimeFile, 'utf8').trim(), 10) || 0;
                }
              } catch { /* ignore */ }
              const sessionFilterTime = sessionStartTime > 0 ? sessionStartTime - 300000 : 0;

              for (const line of lines) {
                try {
                  const obs = JSON.parse(line);
                  if (obs.tool === 'mcp__memory-tools__session_start') {
                    const obsTime = new Date(obs.ts).getTime();
                    if (sessionFilterTime === 0 || obsTime >= sessionFilterTime) {
                      hasSessionStart = true;
                      break;
                    }
                  }
                } catch { /* skip malformed line */ }
              }
            }
          } catch (e) {
            process.stderr.write(`[BehaviorGuard] mcp_tool_guard requires_session scan error: ${e.message}\n`);
          }
          checkPatternRule._sessionStartCache = hasSessionStart;
        }
        if (!hasSessionStart) {
          warn(rule);
        }

      } else if (guardType === 'rate_limit') {
        // Track timestamps per ruleId, check count within window
        const ruleId = rule.id;
        const windowMs = (rule.trigger.window_minutes || 30) * 60 * 1000;
        const maxCount = rule.trigger.max_count || 2;
        if (!state[ruleId]) state[ruleId] = { timestamps: [] };
        state[ruleId].timestamps = state[ruleId].timestamps.filter(t => now - t < windowMs);
        if (state[ruleId].timestamps.length > maxCount) {
          warn(rule);
        } else {
          state[ruleId].timestamps.push(now);
        }

      } else if (guardType === 'required_args') {
        // Check that specified fields in toolInput are not empty/undefined
        const requiredFields = rule.trigger.required_fields || [];
        const missing = [];
        for (const field of requiredFields) {
          const val = toolInput[field];
          if (val === undefined || val === null || (typeof val === 'string' && val.trim() === '')) {
            missing.push(field);
          }
        }
        if (missing.length > 0) {
          const dynamicMsg = `${rule.message} (missing/empty: ${missing.join(', ')})`;
          warn(rule, dynamicMsg);
        }

      } else if (guardType === 'requires_prior_tool') {
        // Check observations.jsonl for a specific tool's prior execution (cached)
        const priorTool = rule.trigger.prior_tool || '';
        if (!priorTool) return; // misconfigured rule, fail-open avoided by returning early
        let hasPriorTool = false;
        try {
          const lines = getObservationLines();
          if (lines.length > 0) {
            const sessionTimeFile = path.join(HOOKS_DIR, '.session-start-time');
            let sessionStartTime = 0;
            try {
              if (fs.existsSync(sessionTimeFile)) {
                sessionStartTime = parseInt(fs.readFileSync(sessionTimeFile, 'utf8').trim(), 10) || 0;
              }
            } catch { /* ignore */ }
            const sessionFilterTime = sessionStartTime > 0 ? sessionStartTime - 300000 : 0;

            for (const line of lines) {
              try {
                const obs = JSON.parse(line);
                if (obs.tool === priorTool) {
                  const obsTime = new Date(obs.ts).getTime();
                  if (sessionFilterTime === 0 || obsTime >= sessionFilterTime) {
                    hasPriorTool = true;
                    break;
                  }
                }
              } catch { /* skip malformed line */ }
            }
          }
        } catch (e) {
          process.stderr.write(`[BehaviorGuard] mcp_tool_guard requires_prior_tool scan error: ${e.message}\n`);
        }
        if (!hasPriorTool) {
          warn(rule);
        }

      } else if (guardType === 'context_warning') {
        // Check if required_prior_tool was called within last 5 minutes
        const requiredPriorTool = rule.trigger.required_prior_tool || '';
        if (requiredPriorTool) {
          let hasPriorTool = false;
          try {
            const lines = getObservationLines();
            const fiveMinAgo = Date.now() - 5 * 60 * 1000;
            for (const line of lines) {
              try {
                const obs = JSON.parse(line);
                if (obs.tool === requiredPriorTool) {
                  const obsTime = new Date(obs.ts).getTime();
                  if (obsTime >= fiveMinAgo) {
                    hasPriorTool = true;
                    break;
                  }
                }
              } catch { /* skip malformed line */ }
            }
          } catch (e) {
            process.stderr.write(`[BehaviorGuard] context_warning prior_tool scan error: ${e.message}\n`);
          }
          if (hasPriorTool) {
            // Prior tool was called recently — pass through
          } else if (!rule.blocking) {
            process.stderr.write(`[BehaviorGuard] CONTEXT WARNING: ${rule.message}\n`);
          } else {
            warn(rule);
          }
        } else if (!rule.blocking) {
          process.stderr.write(`[BehaviorGuard] CONTEXT WARNING: ${rule.message}\n`);
        } else {
          warn(rule);
        }
      } else if (guardType === 'duration_threshold') {
        // G62: SLO violation observer (PostToolUse only).
        // Reads short-lived memo, computes elapsed seconds, persists violation,
        // emits stderr WARN under suppression window.  Strictly read-only on
        // tool_input/tool_response — body never touches the violation log.
        const thresholdSeconds = rule.trigger.threshold_seconds;
        if (typeof thresholdSeconds !== 'number' || !isFinite(thresholdSeconds) || thresholdSeconds <= 0) {
          // Misconfigured rule: disable + one-shot WARN per rule per session.
          emitInvalidThresholdWarn(rule.id);
          return;
        }
        evaluateSloDuration(rule, thresholdSeconds, now, state);
      }
      // Note: unrecognized guardType is filtered by KNOWN_GUARD_TYPES preamble check.
    } catch (e) {
      // Guard evaluation error: log and pass through (don't block on internal errors)
      process.stderr.write(`[BehaviorGuard] mcp_tool_guard error for ${rule.id}: ${e.message}\n`);
    }
  } else if (condition === 'bugfix_commit_with_state') {
    const ruleId = rule.id;
    if (!state[ruleId]) state[ruleId] = {};

    // Track prevention structure edits (Edit/Write to hooks/rules/constants/skills/agents/tools files)
    if (toolName === 'Edit' || toolName === 'Write') {
      const filePath = (toolInput.file_path || '').replace(/\\/g, '/');
      if (filePath.includes('behavior-rules.json') ||
          filePath.includes('behavior-guard') ||
          (filePath.includes('/hooks/') && (/\.js$|\.py$|\.sh$/.test(filePath))) ||
          filePath.includes('/commands/') ||
          filePath.includes('/agents/') ||
          (filePath.includes('/tools/') && /\.py$/.test(filePath))) {
        state[ruleId].prevention = { added: true, time: now };
      }
    }
    // Check on git commit with fix/bugfix/修正 in message
    if (toolName === 'Bash') {
      const cmd = (toolInput.command || '');
      if (/^\s*git\s+commit/.test(cmd)) {
        // Extract -m argument value only (not branch names, file paths, etc.)
        const mMatch = cmd.match(/-m\s+(?:"([^"]*)"|'([^']*)'|(\S+))/);
        const commitMsg = (mMatch ? (mMatch[1] || mMatch[2] || mMatch[3]) : '').toLowerCase();
        if (/fix|bugfix|修正/.test(commitMsg)) {
          const prevention = state[ruleId].prevention || {};
          if (!prevention.added) {
            warn(rule);
          }
        }
      }
    }
  } else if (condition === 'tier_declaration_required') {
    // Only applies to TeamCreate
    if (toolName !== 'TeamCreate') return;

    const windowMs = (rule.trigger.window_minutes || 30) * 60 * 1000;
    let hasTierDeclaration = false;

    // Check observations.jsonl for stm_write with category=self_review containing tier keyword
    try {
      const lines = getObservationLines();
      const tierPattern = /規模[:：]|tier[:：]/i;
      for (const line of lines) {
        try {
          const obs = JSON.parse(line);
          if (obs.tool === 'mcp__memory-tools__stm_write' &&
              obs.params && obs.params.category === 'self_review') {
            const t = new Date(obs.ts).getTime();
            if (now - t <= windowMs) {
              const content = (obs.params.content || '').toLowerCase();
              if (tierPattern.test(obs.params.content || '')) {
                hasTierDeclaration = true;
                break;
              }
            }
          }
        } catch { /* skip */ }
      }
    } catch { /* ignore */ }

    if (!hasTierDeclaration) {
      warn(rule);
    }
  }
}

function summarizeToolInput(toolInput, maxLen) {
  if (!toolInput || typeof toolInput !== 'object') return '';
  // Prefer file_path, then command, then first string value
  const fp = toolInput.file_path || '';
  if (fp) return fp.substring(0, maxLen);
  const cmd = toolInput.command || '';
  if (cmd) return cmd.substring(0, maxLen);
  // For Agent: show subagent_type
  const sub = toolInput.subagent_type || '';
  if (sub) return `subagent_type=${sub}`.substring(0, maxLen);
  return '';
}

function resetDevFlowState(state) {
  // G43: Reset dev-flow-state when cycle is complete
  // (reviewer done + no pending issues + impl > 0)
  const stateKey = '_dev_flow';
  if (state[stateKey]) {
    state[stateKey] = {
      impl: 0,
      reviewer: 0,
      review_issues_pending: null,
      design: 0,
      pre_analysis: 0,
      planner: 0,
      post_analysis: 0,
      thinker: 0,
    };
  }
  // Also reset the .dev-flow-state file
  try {
    const devFlowFile = path.join(HOOKS_DIR, '.dev-flow-state');
    const resetData = JSON.stringify({
      impl: 0,
      reviewer: 0,
      review_issues_pending: null,
      design: 0,
      pre_analysis: 0,
      planner: 0,
      post_analysis: 0,
      thinker: 0,
    });
    const tmp = devFlowFile + '.tmp.' + process.pid;
    fs.writeFileSync(tmp, resetData);
    fs.renameSync(tmp, devFlowFile);
  } catch { /* ignore */ }
}

function warn(rule, dynamicMessage) {
  const outcome = rule.blocking ? 'blocked' : 'warned';
  const msg = dynamicMessage || rule.message;
  const ruleId = rule.id || 'unknown';
  const ESCALATION_THRESHOLD = 3;

  // Update block count for blocking rules
  let count = 0;
  let escalated = false;
  if (rule.blocking) {
    if (!state._block_counts) state._block_counts = {};
    if (!state._block_counts[ruleId]) {
      state._block_counts[ruleId] = { count: 0, first_at: Date.now() };
    }
    state._block_counts[ruleId].count++;
    count = state._block_counts[ruleId].count;
    escalated = count >= ESCALATION_THRESHOLD;
  }

  if (rule.blocking) {
    if (escalated) {
      // Escalation message: tell Claude to use WebSearch to find a solution
      process.stderr.write(`[BehaviorGuard] ESCALATION: ルール "${ruleId}" が${count}回ブロックされています。\n`);
      process.stderr.write(`自力での解決を中止してください。\n`);
      process.stderr.write(`WebSearchツールを使って以下の解決策を検索してください:\n`);
      process.stderr.write(`1. ブロックされている問題: ${msg}\n`);
      process.stderr.write(`2. ルールID: ${ruleId}\n`);
      process.stderr.write(`3. 検索結果に基づいて解決策を実行してください\n`);
      process.stderr.write(`WebSearchで解決策が見つからない場合のみ、ユーザーに報告してください。\n`);
    } else {
      // Normal block message
      process.stderr.write(`[BehaviorGuard] BLOCKED: ${msg}\n`);
      if (rule.lesson) {
        process.stderr.write(`[BehaviorGuard] Lesson: ${rule.lesson}\n`);
      }
    }
    shouldBlock = true;
  } else {
    // exit 0 + stdout = Claude sees the warning via context injection
    const severity = rule.severity === 'info' ? 'INFO' : 'WARNING';
    console.log(`[BehaviorGuard] ${severity}: ${msg}`);
    if (rule.lesson) {
      console.log(`[BehaviorGuard] Lesson: ${rule.lesson}`);
    }
  }

  // Log firing event to hook_firing_log.jsonl (write failure is silently ignored)
  try {
    const firingEntry = JSON.stringify({
      ts: new Date().toISOString(),
      rule_id: ruleId,
      rule_type: rule.type || 'unknown',
      blocking: !!rule.blocking,
      tool_name: currentToolName,
      tool_input_summary: summarizeToolInput(currentToolInput, 100),
      outcome: outcome,
      count: count,
      escalated: escalated,
    });
    // Ensure data directory exists
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }
    fs.appendFileSync(FIRING_LOG_FILE, firingEntry + '\n');
  } catch { /* silently ignore write failures */ }
}
