// Client side of the add-in bridge: finds running Revit sessions through their
// discovery files and talks to one of them over its named pipe.
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const PROTOCOL_VERSION = 1;

export class BridgeError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function homeDir(env = process.env) {
  if (env.REVIT_MCP_HOME) return env.REVIT_MCP_HOME;
  const local = env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(local, 'RevitMcp');
}

function pipePath(name) {
  // .NET maps named pipes to Unix sockets in the temp folder on Linux/macOS;
  // that mapping is only used by the tests.
  return process.platform === 'win32' ? `\\\\.\\pipe\\${name}` : path.join(os.tmpdir(), `CoreFxPipe_${name}`);
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM';
  }
}

/** Running Revit sessions that have the add-in loaded, newest first. */
export function listInstances(env = process.env) {
  const dir = path.join(homeDir(env), 'instances');
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  const found = [];
  for (const name of names) {
    if (!name.endsWith('.json')) continue;
    const file = path.join(dir, name);
    try {
      const info = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (!Number.isInteger(info.pid) || typeof info.pipe !== 'string' || typeof info.token !== 'string') continue;
      if (!/^[A-Za-z0-9._-]+$/.test(info.pipe)) continue;
      if (!processAlive(info.pid)) {
        // Revit crashed without cleaning up.
        try { fs.unlinkSync(file); } catch { /* not ours to delete */ }
        continue;
      }
      found.push(info);
    } catch {
      // Half-written or foreign file: ignore.
    }
  }
  return found.sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)));
}

const NOT_RUNNING =
  'No running Revit session with the Revit MCP add-in was found. Ask the user to start Revit and check that the ' +
  '"Revit MCP" panel is on the Add-Ins ribbon tab. If the panel is missing, the add-in is not installed for that ' +
  'Revit version (see the plugin README, section "Установка надстройки").';

export class Bridge {
  constructor({ env = process.env, timeoutMs } = {}) {
    this.env = env;
    this.timeoutMs = timeoutMs ?? (Number(env.REVIT_MCP_TIMEOUT_MS) || 150_000);
    this.preferredPid = Number(env.REVIT_MCP_PID) || null;
    this.socket = null;
    this.instance = null;
    this.buffer = '';
    this.pending = new Map();
    this.nextId = 1;
  }

  instances() {
    return listInstances(this.env);
  }

  use(pid) {
    const target = this.instances().find((i) => i.pid === pid);
    if (!target) {
      throw new BridgeError('not_found', `No running Revit session with pid ${pid}. Call revit_status to list sessions.`);
    }
    this.preferredPid = pid;
    this.disconnect();
    return target;
  }

  pick() {
    const all = this.instances();
    if (all.length === 0) throw new BridgeError('not_running', NOT_RUNNING);
    if (this.preferredPid) {
      const chosen = all.find((i) => i.pid === this.preferredPid);
      if (chosen) return chosen;
      this.preferredPid = null;
    }
    return all[0];
  }

  async connect() {
    if (this.socket) return;
    const instance = this.pick();
    if (instance.protocol !== PROTOCOL_VERSION) {
      throw new BridgeError(
        'version_mismatch',
        `The Revit add-in (v${instance.addinVersion}) and this MCP server use different bridge protocols. ` +
          'Ask the user to install the add-in and the server from the same release.',
      );
    }
    const socket = await new Promise((resolve, reject) => {
      const s = net.connect(pipePath(instance.pipe));
      s.once('connect', () => resolve(s));
      s.once('error', (err) =>
        reject(new BridgeError('not_running', `Cannot reach Revit (pid ${instance.pid}): ${err.message}. ${NOT_RUNNING}`)),
      );
    });
    socket.setEncoding('utf8');
    socket.on('data', (chunk) => this.onData(chunk));
    const drop = () => {
      if (this.socket === socket) this.disconnect(new BridgeError('disconnected', 'The connection to Revit was lost (Revit closed?). Retry once Revit is running.'));
    };
    socket.on('close', drop);
    socket.on('error', drop);
    this.socket = socket;
    this.instance = instance;
  }

  disconnect(error) {
    const socket = this.socket;
    this.socket = null;
    this.instance = null;
    this.buffer = '';
    if (socket) socket.destroy();
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(error ?? new BridgeError('disconnected', 'Disconnected from Revit.'));
    }
    this.pending.clear();
  }

  onData(chunk) {
    this.buffer += chunk;
    let newline;
    while ((newline = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (!line.trim()) continue;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }
      const waiter = this.pending.get(message.id);
      if (!waiter) continue;
      this.pending.delete(message.id);
      clearTimeout(waiter.timer);
      if (message.ok) waiter.resolve(message.result);
      else waiter.reject(new BridgeError(message.error?.code ?? 'internal_error', message.error?.message ?? 'Unknown add-in error.'));
    }
  }

  /** Calls an add-in command and returns its result, or throws BridgeError. */
  async call(method, params = {}) {
    await this.connect();
    const id = this.nextId++;
    const socket = this.socket;
    const token = this.instance.token;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new BridgeError(
            'timeout',
            `Revit did not answer within ${Math.round(this.timeoutMs / 1000)} s. The command may still be running in Revit; ` +
              'check with revit_status before repeating a change.',
          ),
        );
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      socket.write(JSON.stringify({ id, token, method, params }) + '\n');
    });
  }
}
