// End to end: MCP client -> index.mjs (child process) -> named pipe -> the real C#
// pipe server from RevitMcp.Bridge, hosted by addin/tests/BridgeTestHost instead of
// Revit. Skipped when the test host has not been built:
//   dotnet build addin/tests/BridgeTestHost -c Release
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const hostDll = path.join(root, 'addin/tests/BridgeTestHost/bin/Release/net8.0/BridgeTestHost.dll');
const skip = fs.existsSync(hostDll) ? false : 'BridgeTestHost is not built';

function lineReader(stream, onLine) {
  let buffer = '';
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    buffer += chunk;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      onLine(buffer.slice(0, i));
      buffer = buffer.slice(i + 1);
    }
  });
}

async function startHost(home) {
  const host = spawn('dotnet', [hostDll], { env: { ...process.env, REVIT_MCP_HOME: home }, stdio: ['pipe', 'pipe', 'inherit'] });
  await new Promise((resolve, reject) => {
    lineReader(host.stdout, (line) => line.startsWith('ready') && resolve());
    host.once('exit', (code) => reject(new Error(`test host exited with ${code}`)));
  });
  return host;
}

function startServer(home, extraEnv = {}) {
  const child = spawn(process.execPath, [path.join(root, 'server/index.mjs')], {
    env: { ...process.env, REVIT_MCP_HOME: home, ...extraEnv },
    stdio: ['pipe', 'pipe', 'inherit'],
  });
  const waiting = new Map();
  lineReader(child.stdout, (line) => {
    const message = JSON.parse(line);
    waiting.get(message.id)?.(message);
  });
  let id = 0;
  const request = (method, params) =>
    new Promise((resolve) => {
      const mine = ++id;
      waiting.set(mine, resolve);
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: mine, method, params }) + '\n');
    });
  const call = async (name, args = {}) => (await request('tools/call', { name, arguments: args })).result;
  return { child, request, call };
}

test('MCP server talks to the C# bridge over the pipe', { skip }, async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'revit-mcp-test-'));
  const host = await startHost(home);
  const server = startServer(home);
  t.after(() => {
    server.child.kill();
    host.kill();
    fs.rmSync(home, { recursive: true, force: true });
  });

  const init = await server.request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'test', version: '0' } });
  assert.equal(init.result.serverInfo.name, 'revit-mcp');

  const status = JSON.parse((await server.call('revit_status')).content[0].text);
  assert.equal(status.connected, true);
  assert.equal(status.revitVersion, '2026');
  assert.equal(status.writeEnabled, false);
  assert.equal(status.revitSessions.length, 1);

  // Arguments survive the round trip, including non-ASCII text.
  const args = { category: 'Несущие колонны', limit: 3, parameterFilter: { name: 'Марка', op: 'contains', value: 'К-1' } };
  const query = JSON.parse((await server.call('revit_query_elements', args)).content[0].text);
  assert.deepEqual(query.echo, args);

  // The add-in, not the server, refuses writes while "Allow changes" is off.
  const refused = await server.call('revit_delete_elements', { ids: [1], dryRun: false });
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /switched off in Revit/);

  // A command the add-in does not have is reported, not hung.
  const missing = await server.call('revit_list_sheets');
  assert.equal(missing.isError, true);
  assert.match(missing.content[0].text, /no command 'list_sheets'/);
});

test('the pipe rejects a wrong token and survives handler crashes', { skip }, async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'revit-mcp-test-'));
  const host = await startHost(home);
  t.after(() => {
    host.kill();
    fs.rmSync(home, { recursive: true, force: true });
  });

  const file = fs.readdirSync(path.join(home, 'instances'))[0];
  const info = JSON.parse(fs.readFileSync(path.join(home, 'instances', file), 'utf8'));
  assert.match(info.token, /^[0-9A-F]{64}$/);

  const socket = net.connect(path.join(os.tmpdir(), `CoreFxPipe_${info.pipe}`));
  const replies = [];
  let wake;
  lineReader(socket, (line) => {
    replies.push(JSON.parse(line));
    wake?.();
  });
  const send = async (message) => {
    socket.write((typeof message === 'string' ? message : JSON.stringify(message)) + '\n');
    await new Promise((resolve) => (wake = resolve));
    return replies.at(-1);
  };
  t.after(() => socket.destroy());

  assert.equal((await send({ id: 1, token: 'wrong', method: 'status' })).error.code, 'unauthorized');
  assert.equal((await send({ id: 2, method: 'status' })).error.code, 'unauthorized');
  assert.equal((await send('not json')).error.code, 'bad_request');
  assert.equal((await send({ id: 3, token: info.token, method: 'crash' })).error.code, 'internal_error');
  const ok = await send({ id: 4, token: info.token, method: 'status' });
  assert.equal(ok.ok, true);
  assert.equal(ok.id, 4);
});

test('a stale instance file of a dead process is ignored and removed', { skip: false }, async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'revit-mcp-test-'));
  fs.mkdirSync(path.join(home, 'instances'));
  const stale = path.join(home, 'instances', '999999999.json');
  fs.writeFileSync(stale, JSON.stringify({ protocol: 1, pid: 999999999, pipe: 'RevitMcp-999999999', token: 'x', revitVersion: '2026' }));
  const server = startServer(home);
  t.after(() => {
    server.child.kill();
    fs.rmSync(home, { recursive: true, force: true });
  });

  const status = JSON.parse((await server.call('revit_status')).content[0].text);
  assert.equal(status.connected, false);
  assert.equal(fs.existsSync(stale), false);

  const query = await server.call('revit_query_elements', { category: 'Walls' });
  assert.equal(query.isError, true);
  assert.match(query.content[0].text, /No running Revit session/);
});
