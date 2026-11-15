#!/usr/bin/env node
// Revit MCP server (stdio). Talks to the Revit MCP add-in running inside Revit.
//
// Environment:
//   REVIT_MCP_READ_ONLY=1    hide every tool that changes the model
//   REVIT_MCP_PID=<pid>      prefer this Revit session when several are running
//   REVIT_MCP_TIMEOUT_MS     how long to wait for Revit (default 150000)
import { Bridge } from './lib/bridge.mjs';
import { McpServer } from './lib/mcp.mjs';

const truthy = (value) => /^(1|true|yes|on)$/i.test(String(value ?? '').trim());

// stdout carries the protocol: diagnostics go to stderr only.
const log = (message) => process.stderr.write(`[revit-mcp] ${message}\n`);

const bridge = new Bridge();
const server = new McpServer({ bridge, readOnly: truthy(process.env.REVIT_MCP_READ_ONLY), log });

server.start().then(() => {
  bridge.disconnect();
  process.exit(0);
});
