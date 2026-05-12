#!/usr/bin/env node
/**
 * Secret Detector - PreToolUse hook
 *
 * Edit, Write, Bash ツール入力からAPIキー・トークン・パスワードを検出する。
 * 検出時: exit 2 + stderr警告（Claudeにフィードバック）
 * 非検出時: exit 0（通過）
 */

const path = require('path');

const MAX_STDIN = 1024 * 1024;

// Target tools
const TARGET_TOOLS = new Set(['Edit', 'Write', 'Bash']);

// Known secret patterns (prefix-based, require minimum length to avoid false positives)
const PREFIX_PATTERNS = [
  { name: 'OpenAI API Key', regex: /sk-[a-zA-Z0-9_-]{20,}/ },
  { name: 'GitHub Personal Access Token', regex: /ghp_[a-zA-Z0-9]{36,}/ },
  { name: 'GitHub OAuth Token', regex: /gho_[a-zA-Z0-9]{36,}/ },
  { name: 'GitHub Server Token', regex: /ghs_[a-zA-Z0-9]{36,}/ },
  { name: 'AWS Access Key', regex: /AKIA[0-9A-Z]{16}/ },
  { name: 'Slack Bot Token', regex: /xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+/ },
  { name: 'Slack User Token', regex: /xoxp-[0-9]+-[0-9]+-[a-zA-Z0-9]+/ },
];

// Generic assignment patterns: key = "value" or key="value"
// Matches: password, PASSWORD, secret, SECRET, api_key, API_KEY, token, TOKEN
// followed by assignment (= or :) and a quoted string value
const GENERIC_PATTERNS = [
  {
    name: 'Password assignment',
    regex: /(?:^|[^a-zA-Z_])(?:password|passwd)\s*[=:]\s*["'][^"']{4,}["']/i,
  },
  {
    name: 'Secret assignment',
    regex: /(?:^|[^a-zA-Z_])secret\s*[=:]\s*["'][^"']{4,}["']/i,
  },
  {
    name: 'API key assignment',
    regex: /(?:^|[^a-zA-Z_])api[_-]?key\s*[=:]\s*["'][^"']{4,}["']/i,
  },
  {
    name: 'Token assignment',
    regex: /(?:^|[^a-zA-Z_])token\s*[=:]\s*["'][^"']{8,}["']/i,
  },
];

function extractText(toolName, toolInput) {
  const parts = [];
  if (toolName === 'Edit') {
    if (toolInput.new_string) parts.push(toolInput.new_string);
    // old_string is being replaced, no need to check
  } else if (toolName === 'Write') {
    if (toolInput.content) parts.push(toolInput.content);
  } else if (toolName === 'Bash') {
    if (toolInput.command) parts.push(toolInput.command);
  }
  return parts.join('\n');
}

// Base64 candidate detection: 40+ chars of [A-Za-z0-9+/=]
const BASE64_CANDIDATE_REGEX = /[A-Za-z0-9+/=]{40,}/g;
const MAX_BASE64_CANDIDATES = 5;
const MAX_BASE64_LENGTH = 1000;

function detectSecrets(text) {
  const findings = [];

  for (const pat of PREFIX_PATTERNS) {
    if (pat.regex.test(text)) {
      findings.push(pat.name);
    }
  }

  for (const pat of GENERIC_PATTERNS) {
    if (pat.regex.test(text)) {
      findings.push(pat.name);
    }
  }

  return findings;
}

function detectBase64Secrets(text) {
  const findings = [];
  BASE64_CANDIDATE_REGEX.lastIndex = 0;
  const candidates = text.match(BASE64_CANDIDATE_REGEX);
  if (!candidates) return findings;

  let checked = 0;
  for (const candidate of candidates) {
    if (checked >= MAX_BASE64_CANDIDATES) break;
    checked++;

    const truncated = candidate.substring(0, MAX_BASE64_LENGTH);
    let decoded;
    try {
      decoded = Buffer.from(truncated, 'base64').toString('utf8');
    } catch {
      continue;
    }

    // Skip if decoded is not printable text (binary data, not an encoded secret)
    if (!/^[\x20-\x7E\r\n\t]+$/.test(decoded)) continue;

    const innerFindings = detectSecrets(decoded);
    for (const f of innerFindings) {
      findings.push('Base64-encoded: ' + f);
    }
  }

  return findings;
}

// --- Main ---
let raw = '';
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

    // Self-test fixture file is exempt to break circular dependency.
    // Use canonical absolute path equality (path.resolve) to avoid bypass
    // via attacker-controlled paths like "attacker/hooks/test_secret_detector.sh".
    const filePath = toolInput.file_path || '';
    if (filePath && (toolName === 'Edit' || toolName === 'Write')) {
      try {
        const SELF_TEST_PATH = path.resolve(__dirname, 'test_secret_detector.sh');
        if (path.resolve(filePath) === SELF_TEST_PATH) {
          process.exit(0);
          return;
        }
      } catch {
        // path.resolve failure (e.g., non-string file_path): fall through to detection (fail-closed)
      }
    }

    // Only check target tools
    if (!TARGET_TOOLS.has(toolName)) {
      process.exit(0);
      return;
    }

    const text = extractText(toolName, toolInput);
    if (!text) {
      process.exit(0);
      return;
    }

    const findings = detectSecrets(text);
    const base64Findings = detectBase64Secrets(text);
    findings.push(...base64Findings);

    if (findings.length > 0) {
      const uniqueFindings = [...new Set(findings)];
      process.stderr.write(
        `[SecretDetector] BLOCKED: Potential secret detected in ${toolName} input!\n`
      );
      for (const f of uniqueFindings) {
        process.stderr.write(`[SecretDetector]   - ${f}\n`);
      }
      process.stderr.write(
        `[SecretDetector] Remove the secret and use environment variables or a secrets manager instead.\n`
      );
      process.exit(2);
      return;
    }
  } catch {
    // Parse error or other issue: pass through
  }

  process.exit(0);
});
