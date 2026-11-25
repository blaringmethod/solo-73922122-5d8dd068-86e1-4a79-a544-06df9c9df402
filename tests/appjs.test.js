#!/usr/bin/env node
/* Exact-integer behaviour of the browser enhancement script.
 *
 * Run with: node tests/appjs.test.js
 * The script exports its helpers when loaded outside a browser (no `window`),
 * so these checks exercise the same code the page runs, without a DOM.
 */
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

const api = require(path.join(__dirname, "..", "adjcheck", "static", "app.js"));
const {
  INT64_MIN,
  INT64_MAX,
  parseCoord,
  parseBatch,
  stringifyBigInt,
  parseJsonBigInt,
  show,
  stats,
} = api;

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

check("constants are the signed 64-bit bounds", () => {
  assert.equal(INT64_MIN, -(2n ** 63n));
  assert.equal(INT64_MAX, 2n ** 63n - 1n);
});

check("a 1-metre-wide patch above 2^53 keeps width 1 (the reported bug)", () => {
  const base = 2n ** 53n + 1n; // not exactly representable as a Number
  const [entry] = parseBatch(`S9 ${base} 0 ${base + 1n} 10`);
  assert.equal(entry.x1 - entry.x0, 1n);
  assert.equal(entry.y1 - entry.y0, 10n);
});

check("wire serialization emits full integer digits, never scientific form", () => {
  const base = 2n ** 53n + 1n;
  const [entry] = parseBatch(`S9 ${base} 0 ${base + 1n} 10`);
  const wire = stringifyBigInt({ batch: [entry], version: 0n });
  assert.match(wire, /"x1":9007199254740994/);
  assert.doesNotMatch(wire, /e\+/);
  // the page's own BigInt parser reads the exact integer back end to end
  const reparsed = parseJsonBigInt(wire);
  assert.equal(reparsed.batch[0].x1, 9007199254740994n);
  assert.equal(reparsed.batch[0].x1 - reparsed.batch[0].x0, 1n);
});

check("both int64 extremes parse, and adjacent extreme coordinates stay exact", () => {
  const [low] = parseBatch(`S1 ${INT64_MIN} ${INT64_MIN} -1 -1`);
  assert.equal(low.x0, INT64_MIN);
  const [high] = parseBatch(`S1 ${INT64_MAX - 1n} 0 ${INT64_MAX} 1`);
  assert.equal(high.x1 - high.x0, 1n);
});

check("out-of-range coordinates are rejected with the bound in the message", () => {
  for (const bad of [
    "S1 9223372036854775808 0 1 1",
    "S1 -9223372036854775809 0 1 1",
  ]) {
    assert.throws(() => parseBatch(bad), /64 位整数米范围/);
  }
});

check("non-integer and malformed input is rejected", () => {
  assert.throws(() => parseBatch("S1 1.5 0 1 1"), /整数米/);
  assert.throws(() => parseBatch("S1 abc 0 1 1"), /整数米/);
  assert.throws(() => parseBatch("S1 5 0 4 1"), /x0<x1/);
  assert.throws(() => parseBatch("S1 0 0 1"), /行格式/);
});

check("parseCoord standalone validation", () => {
  assert.equal(parseCoord(" -123 ", "x"), -123n);
  assert.throws(() => parseCoord("1e3", "x"), /整数米/);
  assert.throws(() => parseCoord(String(INT64_MAX + 1n), "x"), /64 位/);
});

check("BigInt JSON parser keeps super-64-bit integers exactly", () => {
  const payload = {
    applied: true,
    code: 200,
    area: 9223372036854775809n, // > int64 max, a legitimate area total
    rect: [INT64_MIN, 0n, INT64_MAX, 42n],
    note: "digits 9223372036854775809 inside a string stay a string",
    none: null,
    frac: 3.5,
    nested: { missing_area: 18446744073709551618n, list: [1n, 2n, 3n] },
  };
  const text = stringifyBigInt(payload);
  const back = parseJsonBigInt(text);
  assert.equal(back.area, 9223372036854775809n);
  assert.equal(back.nested.missing_area, 18446744073709551618n);
  assert.deepEqual(back.rect, [INT64_MIN, 0n, INT64_MAX, 42n]);
  assert.equal(back.applied, true);
  assert.equal(back.none, null);
  assert.equal(back.frac, 3.5);
  assert.deepEqual(back.nested.list, [1n, 2n, 3n]);
  assert.match(back.note, /inside a string/);
});

check("parser rejects malformed JSON instead of returning partial data", () => {
  assert.throws(() => parseJsonBigInt("{oops"));
  assert.throws(() => parseJsonBigInt('{"a":1} trailing'));
});

check("formatting helpers render big integers without exponent or 'n'", () => {
  assert.equal(show(18446744073709551615n), "18446744073709551615");
  assert.equal(show(null), "-");
  const line = stats({
    missing_count: 7n,
    missing_area: 18446744073709551615n,
    overlap_count: 0n,
    overlap_area: 0n,
  });
  assert.match(line, /7 区/);
  assert.match(line, /18446744073709551615 m²/);
});

console.log(`\n${passed} app.js checks passed`);
