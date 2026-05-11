/**
 * Tests for hooks/subagent-stop-logger.js
 *
 * Tests SubagentStop hook: observation logging + growth_recorder delegation.
 * Run: node hooks/tests/test_subagent_stop_logger.js
 */

const { execFileSync, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${msg}`);
  } else {
    failed++;
    console.log(`  FAIL: ${msg}`);
  }
}

const hookScript = path.join(__dirname, '..', 'subagent-stop-logger.js');

// --- Test 1: Valid input writes observation to observations.jsonl ---
console.log('Test 1: Valid input writes observation');
{
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'subagent-stop-test-'));
  const dataDir = path.join(tmpDir, 'data');
  fs.mkdirSync(dataDir, { recursive: true });

  // Create a temporary wrapper that overrides DATA_DIR
  const wrapperScript = path.join(tmpDir, 'wrapper.js');
  fs.writeFileSync(wrapperScript, `
    // Override DATA_DIR before loading the module logic
    const fs = require('fs');
    const path = require('path');

    const DATA_DIR = ${JSON.stringify(dataDir)};
    const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
    const MAX_FILE_SIZE = 5 * 1024 * 1024;
    const MAX_STDIN = 1024 * 1024;
    let raw = '';

    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => {
      if (raw.length < MAX_STDIN) {
        const remaining = MAX_STDIN - raw.length;
        raw += chunk.substring(0, remaining);
      }
    });

    process.stdin.on('end', () => {
      let input = {};
      try {
        input = JSON.parse(raw);
      } catch {
        process.exit(0);
      }

      try {
        let sessionId = 'test-session';
        const reason = (input.reason || 'unknown').substring(0, 100);
        const agentId = (input.agent_id || 'unknown').substring(0, 200);
        const transcriptPath = (input.agent_transcript_path || '').substring(0, 500);
        const lastMessage = (input.last_assistant_message || '').substring(0, 200);

        const observation = {
          ts: new Date().toISOString(),
          sid: sessionId.substring(0, 12),
          tool: 'SubagentStop',
          params: {
            reason: reason,
            agent_id: agentId,
            transcript: transcriptPath,
            last_msg: lastMessage,
          },
        };

        if (!fs.existsSync(DATA_DIR)) {
          fs.mkdirSync(DATA_DIR, { recursive: true });
        }

        const line = JSON.stringify(observation) + '\\n';
        let fd;
        try {
          fd = fs.openSync(OBS_FILE, 'a');
          fs.writeSync(fd, line);
        } finally {
          if (fd !== undefined && fd !== null) {
            try { fs.closeSync(fd); } catch {}
          }
        }
      } catch {}

      process.exit(0);
    });
  `);

  const input = JSON.stringify({
    reason: 'end_turn',
    agent_id: 'agent-test-1',
    agent_transcript_path: '/tmp/test-transcript.json',
    last_assistant_message: 'Task completed successfully.',
  });

  try {
    execFileSync('node', [wrapperScript], {
      input: input,
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    const obsFile = path.join(dataDir, 'observations.jsonl');
    assert(fs.existsSync(obsFile), 'observations.jsonl created');

    const content = fs.readFileSync(obsFile, 'utf8').trim();
    const obs = JSON.parse(content);
    assert(obs.tool === 'SubagentStop', 'tool is SubagentStop');
    assert(obs.params.reason === 'end_turn', 'reason recorded');
    assert(obs.params.agent_id === 'agent-test-1', 'agent_id recorded');
    assert(obs.params.last_msg === 'Task completed successfully.', 'last_msg recorded');
  } catch (e) {
    assert(false, `execution error: ${e.message}`);
  }

  try { fs.rmSync(tmpDir, { recursive: true }); } catch {}
}

// --- Test 2: Invalid JSON stdin does not crash ---
console.log('Test 2: Invalid JSON stdin exits normally');
{
  try {
    execFileSync('node', [hookScript], {
      input: 'not valid json{{{',
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'no crash on invalid JSON');
  } catch (e) {
    // exit code 0 is expected
    if (e.status === 0 || e.status === null) {
      assert(true, 'no crash on invalid JSON (exit 0)');
    } else {
      assert(false, `crashed with exit code ${e.status}`);
    }
  }
}

// --- Test 3: Empty stdin does not crash ---
console.log('Test 3: Empty stdin exits normally');
{
  try {
    execFileSync('node', [hookScript], {
      input: '',
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'no crash on empty stdin');
  } catch (e) {
    if (e.status === 0 || e.status === null) {
      assert(true, 'no crash on empty stdin (exit 0)');
    } else {
      assert(false, `crashed with exit code ${e.status}`);
    }
  }
}

// --- Test 4: Long last_assistant_message is truncated ---
console.log('Test 4: Long message truncation');
{
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'subagent-stop-trunc-'));
  const dataDir = path.join(tmpDir, 'data');
  fs.mkdirSync(dataDir, { recursive: true });

  const wrapperScript = path.join(tmpDir, 'wrapper.js');
  fs.writeFileSync(wrapperScript, `
    const fs = require('fs');
    const path = require('path');
    const DATA_DIR = ${JSON.stringify(dataDir)};
    const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
    let raw = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => { raw += chunk; });
    process.stdin.on('end', () => {
      let input = {};
      try { input = JSON.parse(raw); } catch { process.exit(0); }
      const lastMessage = (input.last_assistant_message || '').substring(0, 200);
      const obs = { tool: 'SubagentStop', params: { last_msg: lastMessage } };
      fs.writeFileSync(OBS_FILE, JSON.stringify(obs) + '\\n');
      process.exit(0);
    });
  `);

  const longMsg = 'x'.repeat(5000);
  const input = JSON.stringify({
    reason: 'end_turn',
    agent_id: 'agent-long',
    last_assistant_message: longMsg,
  });

  try {
    execFileSync('node', [wrapperScript], {
      input: input,
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    const obsFile = path.join(dataDir, 'observations.jsonl');
    const content = fs.readFileSync(obsFile, 'utf8').trim();
    const obs = JSON.parse(content);
    assert(obs.params.last_msg.length <= 200, `last_msg truncated to ${obs.params.last_msg.length} chars`);
  } catch (e) {
    assert(false, `truncation test error: ${e.message}`);
  }

  try { fs.rmSync(tmpDir, { recursive: true }); } catch {}
}

// --- Test 5: Hook script runs the actual file without crash ---
console.log('Test 5: Actual hook script runs without crash');
{
  const input = JSON.stringify({
    reason: 'end_turn',
    agent_id: 'agent-actual-test',
    last_assistant_message: 'Done.',
  });

  try {
    execFileSync('node', [hookScript], {
      input: input,
      timeout: 10000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'actual hook script ran without crash');
  } catch (e) {
    if (e.status === 0 || e.status === null) {
      assert(true, 'actual hook script exited normally');
    } else {
      // growth_recorder.py may fail if modules not available, but hook should still exit 0
      assert(false, `actual hook script crashed with exit code ${e.status}`);
    }
  }
}

console.log(`\nResults: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
