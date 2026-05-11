#!/usr/bin/env node
/**
 * MCP Health Check — PreToolUse + PostToolUse hook
 *
 * PreToolUse (mcp__* tools): Check if the target MCP server has responded
 * recently. If no response in 5 minutes, warn via stdout (non-blocking).
 *
 * PostToolUse (mcp__* tools): Record successful response timestamp.
 *
 * Data file: data/mcp_last_response.json
 * Format: { "server_name": timestamp_ms, ... }
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const RESPONSE_FILE = path.join(DATA_DIR, 'mcp_last_response.json');
const STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes

const MAX_STDIN = 256 * 1024;
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
    const now = Date.now();

    // Only process MCP tools
    if (!toolName.startsWith('mcp__')) {
      process.exit(0);
    }

    // Extract server name from tool name: mcp__server-name__tool_name
    const parts = toolName.split('__');
    const serverName = parts.length >= 2 ? parts[1] : '';
    if (!serverName) {
      process.exit(0);
    }

    // Load response tracking data
    let responseData = {};
    try {
      if (fs.existsSync(RESPONSE_FILE)) {
        responseData = JSON.parse(fs.readFileSync(RESPONSE_FILE, 'utf8'));
      }
    } catch { responseData = {}; }

    // Determine if this is a PreToolUse or PostToolUse event
    // PostToolUse has tool_result field
    const isPostToolUse = 'tool_result' in input;

    if (isPostToolUse) {
      // Record successful response
      responseData[serverName] = now;
      try {
        fs.mkdirSync(DATA_DIR, { recursive: true });
        const tmp = RESPONSE_FILE + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, JSON.stringify(responseData, null, 2));
        fs.renameSync(tmp, RESPONSE_FILE);
      } catch { /* ignore write errors */ }
    } else {
      // PreToolUse: check staleness
      const lastResponse = responseData[serverName] || 0;
      if (lastResponse > 0) {
        const elapsed = now - lastResponse;
        if (elapsed > STALE_THRESHOLD_MS) {
          const minutes = Math.floor(elapsed / 60000);
          // Warn via stdout (non-blocking, context injection)
          console.log(
            `[MCPHealthCheck] Warning: MCP server '${serverName}' last responded ${minutes}m ago. ` +
            `It may be unresponsive.`
          );
        }
      }
      // No lastResponse = first call, no warning needed
    }
  } catch {
    // Hook must not crash
  }

  process.exit(0);
});
