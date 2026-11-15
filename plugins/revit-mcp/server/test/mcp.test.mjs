// Protocol behaviour of the MCP server with a fake bridge (no Revit, no .NET).
import test from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { McpServer, checkArguments } from '../lib/mcp.mjs';
import { BridgeError } from '../lib/bridge.mjs';
import { findTool } from '../lib/tools.mjs';

function harness({ readOnly = false, bridge } = {}) {
  const input = new PassThrough();
  const output = new PassThrough();
  const calls = [];
  const fake = bridge ?? {
    instances: () => [{ pid: 7, revitVersion: '2026', addinVersion: '0.1.0', startedAt: 'now' }],
    use: (pid) => ({ pid, revitVersion: '2026' }),
    call: async (method, params) => {
      calls.push({ method, params });
      if (method === 'export_view_image') return { viewId: 1, viewName: 'L1', mimeType: 'image/png', imageBase64: 'QUJD' };
      if (method === 'delete_elements') throw new BridgeError('write_disabled', 'Model changes are switched off in Revit.');
      return { pid: 7, echo: params };
    },
  };
  new McpServer({ bridge: fake, readOnly, input, output }).start();

  let buffer = '';
  const waiting = new Map();
  output.on('data', (chunk) => {
    buffer += chunk;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const message = JSON.parse(buffer.slice(0, i));
      buffer = buffer.slice(i + 1);
      waiting.get(message.id)?.(message);
    }
  });
  let id = 0;
  const request = (method, params) =>
    new Promise((resolve) => {
      const mine = ++id;
      waiting.set(mine, resolve);
      input.write(JSON.stringify({ jsonrpc: '2.0', id: mine, method, params }) + '\n');
    });
  return { request, calls, input, output };
}

test('initialize negotiates the protocol version', async () => {
  const { request } = harness();
  const same = await request('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 't', version: '1' } });
  assert.equal(same.result.protocolVersion, '2024-11-05');
  assert.equal(same.result.serverInfo.name, 'revit-mcp');
  const unknown = await request('initialize', { protocolVersion: '1999-01-01' });
  assert.equal(unknown.result.protocolVersion, '2025-06-18');
});

test('tools/list hides write tools in read-only mode', async () => {
  const all = (await harness().request('tools/list')).result.tools.map((t) => t.name);
  const readOnly = (await harness({ readOnly: true }).request('tools/list')).result.tools.map((t) => t.name);
  assert.ok(all.includes('revit_delete_elements'));
  assert.ok(!readOnly.includes('revit_delete_elements'));
  assert.ok(readOnly.includes('revit_query_elements'));
  assert.ok(readOnly.length < all.length);
});

test('a write tool cannot be called in read-only mode', async () => {
  const { request, calls } = harness({ readOnly: true });
  const reply = await request('tools/call', { name: 'revit_delete_elements', arguments: { ids: [1], dryRun: false } });
  assert.equal(reply.error.code, -32602);
  assert.equal(calls.length, 0);
});

test('tools/call forwards arguments to the add-in command', async () => {
  const { request, calls } = harness();
  const reply = await request('tools/call', { name: 'revit_query_elements', arguments: { category: 'Walls', limit: 5 } });
  assert.equal(reply.result.isError, undefined);
  assert.deepEqual(calls, [{ method: 'query_elements', params: { category: 'Walls', limit: 5 } }]);
  assert.deepEqual(JSON.parse(reply.result.content[0].text).echo, { category: 'Walls', limit: 5 });
});

test('invalid arguments are explained and never reach Revit', async () => {
  const { request, calls } = harness();
  const cases = [
    [{ ids: [] }, /at least 1/],
    [{ ids: ['12'] }, /ids\[0\] must be integer/],
    [{ ids: [1], everything: true }, /not a known field/],
    [{}, /ids is required/],
  ];
  for (const [args, pattern] of cases) {
    const reply = await request('tools/call', { name: 'revit_get_elements', arguments: args });
    assert.equal(reply.result.isError, true);
    assert.match(reply.result.content[0].text, pattern);
  }
  assert.equal(calls.length, 0);
});

test('add-in errors become tool errors with the add-in message', async () => {
  const { request } = harness();
  const reply = await request('tools/call', { name: 'revit_delete_elements', arguments: { ids: [5] } });
  assert.equal(reply.result.isError, true);
  assert.match(reply.result.content[0].text, /switched off in Revit/);
});

test('view export returns an image block', async () => {
  const { request } = harness();
  const reply = await request('tools/call', { name: 'revit_export_view_image', arguments: {} });
  assert.deepEqual(reply.result.content[0], { type: 'image', data: 'QUJD', mimeType: 'image/png' });
  assert.equal(JSON.parse(reply.result.content[1].text).viewName, 'L1');
});

test('status without Revit is an answer, not an error', async () => {
  const bridge = { instances: () => [], call: async () => assert.fail('must not call') };
  const reply = await harness({ bridge }).request('tools/call', { name: 'revit_status', arguments: {} });
  assert.equal(reply.result.isError, undefined);
  assert.equal(JSON.parse(reply.result.content[0].text).connected, false);
});

test('unknown methods, unknown tools and junk input', async () => {
  const { request, input, output } = harness();
  assert.equal((await request('nope/nope')).error.code, -32601);
  assert.equal((await request('tools/call', { name: 'revit_nope' })).error.code, -32602);
  assert.deepEqual((await request('ping')).result, {});

  const parseError = new Promise((resolve) => output.once('data', (d) => resolve(JSON.parse(String(d)))));
  input.write('{not json\n');
  assert.equal((await parseError).error.code, -32700);

  // Notifications get no reply: the next reply belongs to the ping after it.
  input.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  assert.deepEqual((await request('ping')).result, {});
});

test('checkArguments accepts valid nested input', () => {
  const schema = findTool('revit_create_walls').inputSchema;
  assert.equal(checkArguments(schema, { walls: [{ startMm: [0, 0], endMm: [5000, 0, 0], level: 'Level 1', heightMm: 3000 }] }), null);
  assert.equal(checkArguments(schema, { walls: [{ startMm: [0, 0], endMm: [5000, 0], level: 311, heightMm: 3000 }] }), null);
  assert.match(checkArguments(schema, { walls: [{ startMm: [0], endMm: [5000, 0], level: 'L1', heightMm: 3000 }] }), /at least 2/);
  assert.match(checkArguments(schema, { walls: [{ startMm: [0, 0], endMm: [1, 1], level: 'L1', heightMm: 0 }] }), /greater than 0/);
});
