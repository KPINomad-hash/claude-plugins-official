// The tool catalog (Node) and the command registry (C#) are separate lists.
// This test keeps them in sync without needing Revit or .NET.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { tools } from '../lib/tools.mjs';

const registry = fs.readFileSync(
  fileURLToPath(new URL('../../addin/src/RevitMcp.Addin/CommandInfrastructure.cs', import.meta.url)),
  'utf8',
);
const commands = new Map();
for (const match of registry.matchAll(/\b(Read|Write)\("([a-z_]+)",/g)) commands.set(match[2], match[1] === 'Write');

test('every tool maps to an add-in command and back', () => {
  const methods = tools.filter((t) => t.method).map((t) => t.method);
  assert.deepEqual([...methods].sort(), [...commands.keys()].sort());
});

test('write flags agree between server and add-in', () => {
  for (const tool of tools.filter((t) => t.method)) {
    assert.equal(Boolean(tool.write), commands.get(tool.method), `${tool.name}: write flag differs from the add-in`);
  }
});

test('tool definitions are well formed', () => {
  const names = new Set();
  for (const tool of tools) {
    assert.match(tool.name, /^revit_[a-z_]+$/);
    assert.ok(!names.has(tool.name), `duplicate ${tool.name}`);
    names.add(tool.name);
    assert.ok(tool.description.length > 40, `${tool.name}: description too short`);
    assert.equal(tool.inputSchema.type, 'object');
    assert.equal(typeof tool.annotations.readOnlyHint, 'boolean');
    if (tool.write) assert.equal(tool.annotations.readOnlyHint, false, `${tool.name}: write tool marked read-only`);
    for (const key of tool.inputSchema.required ?? []) {
      assert.ok(tool.inputSchema.properties[key], `${tool.name}: required '${key}' is not a property`);
    }
  }
});
