import test from 'node:test';
import assert from 'node:assert/strict';
import { sampleAlpha, sampleTriangles } from '../../../plugins/optional/sakura_spine/hit-test.mjs';
import { attachDynamicHitTest } from '../pet/dynamic-hit-test.js';

const mask = { width: 2, height: 2, alpha: new Uint8Array([0, 255, 0, 255]) };
const vertex = (x, y, u, v, a = 1) => [x, y, 1, 1, 1, a, u, v, 0, 0, 0, 1];
const vertices = [...vertex(0, 0, 0, 0), ...vertex(10, 0, 1, 0), ...vertex(10, 10, 1, 1), ...vertex(0, 10, 0, 1)];
const triangles = [0, 1, 2, 2, 3, 0];

test('texture holes, linear filtering, transformed triangles and slot alpha determine input', () => {
  assert.equal(sampleAlpha(mask, 0.5, 0.5), 0.5);
  assert.equal(sampleTriangles(mask, vertices, triangles, 1, 5), 0);
  assert.equal(sampleTriangles(mask, vertices, triangles, 9, 5), 1);
  assert.equal(sampleTriangles(mask, vertices, triangles, 12, 5), 0);
  const faded = vertices.map((value, i) => i % 12 === 5 ? 0.5 : value);
  assert.equal(sampleTriangles(mask, faded, triangles, 9, 5), 0.5);
  assert.equal(sampleTriangles(mask, faded, triangles, 9, 5, 0.5), 0.75);
  const clipped = [0, 1, 2];
  assert.equal(sampleTriangles(mask, vertices, clipped, 9, 9.5), 0);
});

test('shared mesh edge is sampled once and rotated atlas UVs retain transparent holes', () => {
  const half = { width: 1, height: 1, alpha: new Uint8Array([128]) };
  assert.equal(sampleTriangles(half, vertices, triangles, 5, 5), 128 / 255);
  const twice = sampleTriangles(half, [...vertices, ...vertices], [...triangles, ...triangles.map(i => i + 4)], 5, 5);
  assert.ok(Math.abs(twice - (128 / 255 + 128 / 255 * (1 - 128 / 255))) < 1e-10,
    'overlapping mesh faces still composite twice');
  const rotated = [...vertex(0, 0, 0, 1), ...vertex(10, 0, 0, 0), ...vertex(10, 10, 1, 0), ...vertex(0, 10, 1, 1)];
  assert.equal(sampleTriangles(mask, rotated, triangles, 5, 1), 0);
  assert.equal(sampleTriangles(mask, rotated, triangles, 5, 9), 1);
});

test('late native activation is disabled after its acknowledgement even if the binding already aborted', async () => {
  let activate, calls = [];
  const abort = new AbortController();
  const attaching = attachDynamicHitTest({ signal: abort.signal, container: {}, hitTest() {},
    listen: async () => () => {}, invoke: async (_command, params) => {
      calls.push(params.enabled);
      if (params.enabled) return new Promise(resolve => { activate = resolve; });
      return true;
    } });
  await Promise.resolve();
  abort.abort(); activate(true);
  assert.equal(await attaching, false);
  assert.deepEqual(calls, [true, false, false]);
});

test('host maps CSS coordinates and never submits results from a retired renderer', async () => {
  let handler, release, queries = [], stopped = 0;
  const signal = new AbortController();
  const invoke = async (command, params) => { queries.push({ command, ...params }); return true; };
  await attachDynamicHitTest({ invoke, listen: async (_name, callback) => { handler = callback; return () => stopped++; },
    container: { getBoundingClientRect: () => ({ left: 20, top: 30, width: 200, height: 400 }) }, signal: signal.signal,
    hitTest: point => { assert.deepEqual(point, [0.5, 0.5]); return new Promise(resolve => { release = resolve; }); } });
  const session = queries[0].session;
  const pending = handler({ payload: { session, id: 1, point: [120, 230] } });
  signal.abort(); release(false); await pending;
  assert.equal(stopped, 1);
  assert.equal(queries.filter(q => q.command === 'submit_dynamic_hit_test').length, 0);
  assert.equal(queries.at(-1).enabled, false);
});
