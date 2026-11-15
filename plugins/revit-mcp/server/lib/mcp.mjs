// Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0). Written without
// dependencies on purpose: the plugin must run on an offline workstation straight
// from a git checkout, with nothing to `npm install`.
import { createInterface } from 'node:readline';
import { BridgeError } from './bridge.mjs';
import { findTool, visibleTools } from './tools.mjs';

const SUPPORTED_PROTOCOLS = ['2025-06-18', '2025-03-26', '2024-11-05'];
export const SERVER_INFO = { name: 'revit-mcp', title: 'Revit MCP', version: '0.1.0' };

const INSTRUCTIONS =
  'Tools for working with the Autodesk Revit model open on this computer. Start with revit_status. ' +
  'All lengths and coordinates are millimetres. Element ids are integers. Read tools are always available; ' +
  'tools that change the model work only after the user turns on "Allow changes" in Revit. ' +
  'When a tool reports that Revit is busy or changes are switched off, tell the user what to do in Revit ' +
  'instead of retrying in a loop.';

export class McpServer {
  constructor({ bridge, readOnly = false, input = process.stdin, output = process.stdout, log = () => {} }) {
    this.bridge = bridge;
    this.readOnly = readOnly;
    this.input = input;
    this.output = output;
    this.log = log;
  }

  start() {
    const lines = createInterface({ input: this.input, crlfDelay: Infinity });
    lines.on('line', (line) => {
      if (!line.trim()) return;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        this.send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
        return;
      }
      this.handle(message).catch((err) => this.log(`unhandled: ${err?.stack ?? err}`));
    });
    return new Promise((resolve) => lines.on('close', resolve));
  }

  send(message) {
    this.output.write(JSON.stringify(message) + '\n');
  }

  async handle(message) {
    if (message === null || typeof message !== 'object' || Array.isArray(message) || typeof message.method !== 'string') {
      // Responses to requests we never send, batches and junk are ignored.
      if (message && message.id !== undefined && message.method === undefined) return;
      this.send({ jsonrpc: '2.0', id: null, error: { code: -32600, message: 'Invalid Request' } });
      return;
    }
    const isNotification = message.id === undefined;
    try {
      const result = await this.dispatch(message.method, message.params ?? {});
      if (!isNotification) this.send({ jsonrpc: '2.0', id: message.id, result });
    } catch (err) {
      if (isNotification) return;
      const code = Number.isInteger(err?.rpcCode) ? err.rpcCode : -32603;
      this.send({ jsonrpc: '2.0', id: message.id, error: { code, message: err?.message ?? 'Internal error' } });
    }
  }

  async dispatch(method, params) {
    switch (method) {
      case 'initialize':
        return {
          protocolVersion: SUPPORTED_PROTOCOLS.includes(params.protocolVersion) ? params.protocolVersion : SUPPORTED_PROTOCOLS[0],
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
          instructions: INSTRUCTIONS,
        };
      case 'ping':
        return {};
      case 'tools/list':
        return {
          tools: visibleTools({ readOnly: this.readOnly }).map(({ name, title, description, inputSchema, annotations }) => ({
            name,
            title,
            description,
            inputSchema,
            annotations: { title, ...annotations },
          })),
        };
      case 'tools/call':
        return this.callTool(params);
      case 'resources/list':
        return { resources: [] };
      case 'prompts/list':
        return { prompts: [] };
      default:
        if (method.startsWith('notifications/')) return {};
        throw rpcError(-32601, `Method not found: ${method}`);
    }
  }

  async callTool(params) {
    const tool = typeof params.name === 'string' ? findTool(params.name) : undefined;
    if (!tool || (this.readOnly && tool.write)) throw rpcError(-32602, `Unknown tool: ${params.name}`);
    const args = params.arguments ?? {};
    if (args === null || typeof args !== 'object' || Array.isArray(args)) {
      throw rpcError(-32602, 'Tool arguments must be an object.');
    }

    try {
      const problem = checkArguments(tool.inputSchema, args);
      if (problem) return failure(`Invalid arguments for ${tool.name}: ${problem}`);

      if (tool.name === 'revit_status') return text(await this.status());
      if (tool.name === 'revit_use_instance') {
        const target = this.bridge.use(args.pid);
        return text({ using: { pid: target.pid, revitVersion: target.revitVersion } });
      }

      const result = await this.bridge.call(tool.method, args);
      if (tool.name === 'revit_export_view_image' && result?.imageBase64) {
        const { imageBase64, mimeType, ...meta } = result;
        return {
          content: [
            { type: 'image', data: imageBase64, mimeType: mimeType ?? 'image/png' },
            { type: 'text', text: JSON.stringify(meta) },
          ],
        };
      }
      return text(result);
    } catch (err) {
      if (err instanceof BridgeError) return failure(err.message);
      this.log(`tool ${tool.name} failed: ${err?.stack ?? err}`);
      return failure(`Unexpected error in the MCP server: ${err?.message ?? err}`);
    }
  }

  async status() {
    const sessions = this.bridge.instances().map((i) => ({
      pid: i.pid,
      revitVersion: i.revitVersion,
      addinVersion: i.addinVersion,
      startedAt: i.startedAt,
    }));
    if (sessions.length === 0) {
      // Not an error: the model needs this answer to guide the user.
      return {
        connected: false,
        revitSessions: [],
        message:
          'Revit is not running, or the Revit MCP add-in is not loaded in it. Ask the user to start Revit and look for ' +
          'the "Revit MCP" panel on the Add-Ins ribbon tab.',
      };
    }
    const current = await this.bridge.call('status');
    return {
      connected: true,
      serverReadOnly: this.readOnly,
      ...current,
      revitSessions: sessions,
      ...(sessions.length > 1
        ? { note: `Several Revit sessions are running; tools talk to pid ${current.pid}. Use revit_use_instance to switch.` }
        : {}),
    };
  }
}

function rpcError(code, message) {
  const err = new Error(message);
  err.rpcCode = code;
  return err;
}

function text(value) {
  return { content: [{ type: 'text', text: JSON.stringify(value) }] };
}

function failure(message) {
  return { content: [{ type: 'text', text: message }], isError: true };
}

/**
 * Checks arguments against the subset of JSON Schema used by tools.mjs, so that
 * mistakes are explained before anything reaches Revit. Returns a message or null.
 */
export function checkArguments(schema, value, where = 'arguments') {
  const types = schema.type === undefined ? [] : [].concat(schema.type);
  if (types.length && !types.some((t) => matchesType(t, value))) {
    return `${where} must be ${types.join(' or ')}.`;
  }
  if (schema.enum && !schema.enum.includes(value)) return `${where} must be one of: ${schema.enum.join(', ')}.`;
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) return `${where} must be at least ${schema.minimum}.`;
    if (schema.maximum !== undefined && value > schema.maximum) return `${where} must be at most ${schema.maximum}.`;
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) return `${where} must be greater than ${schema.exclusiveMinimum}.`;
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) return `${where} needs at least ${schema.minItems} item(s).`;
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return `${where} allows at most ${schema.maxItems} items; split the call.`;
    if (schema.items) {
      for (let i = 0; i < value.length; i++) {
        const problem = checkArguments(schema.items, value[i], `${where}[${i}]`);
        if (problem) return problem;
      }
    }
  } else if (value !== null && typeof value === 'object' && schema.properties) {
    for (const key of schema.required ?? []) {
      if (value[key] === undefined) return `${where}.${key} is required.`;
    }
    for (const [key, item] of Object.entries(value)) {
      const sub = schema.properties[key];
      if (!sub) {
        if (schema.additionalProperties === false) {
          return `${where}.${key} is not a known field. Known fields: ${Object.keys(schema.properties).join(', ') || '(none)'}.`;
        }
        continue;
      }
      if (item === undefined) continue;
      const problem = checkArguments(sub, item, `${where}.${key}`);
      if (problem) return problem;
    }
  }
  return null;
}

function matchesType(type, value) {
  switch (type) {
    case 'object': return value !== null && typeof value === 'object' && !Array.isArray(value);
    case 'array': return Array.isArray(value);
    case 'string': return typeof value === 'string';
    case 'boolean': return typeof value === 'boolean';
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'integer': return Number.isSafeInteger(value);
    case 'null': return value === null;
    default: return true;
  }
}
