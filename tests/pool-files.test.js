// Tests for js/pool-files.js. Run: node --test tests/pool-files.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "js", "pool-files.js"), "utf8");

const INDEX = {
  format: 2,
  subject: { title: "S" },
  books: [
    { id: "one", title: "One", files: { "data/manifest.enc": "ab/cd.bin" } },
    { id: "two", title: "Two", files: {} },
  ],
};

/** Run the script with a fake page. */
function load({ meta, search, index, status }) {
  const calls = [];
  const sandbox = {
    document: { querySelector: () => (meta ? { content: meta } : null) },
    window: { location: { search: search || "" },
      SubjectUnlock: {
        ready: async () => ({ key: "K" }),
        decrypt: async (buffer) => {
          calls.push("decrypt");
          return Buffer.from(JSON.stringify(index));
        },
      } },
    fetch: async (address) => {
      calls.push(address);
      return { ok: status !== 404, status: status || 200,
        arrayBuffer: async () => new ArrayBuffer(1) };
    },
    URLSearchParams, TextDecoder, Error, JSON, Promise,
  };
  vm.runInNewContext(SOURCE, sandbox);
  return { pool: sandbox.window.RecallQuizPool, calls };
}

test("does nothing without the meta tag", async () => {
  const { pool, calls } = load({});
  assert.equal(pool.enabled, false);
  assert.equal(await pool.ready(), null);
  assert.deepEqual(calls, []);
});

test("reads the index and resolves a book's files", async () => {
  const { pool, calls } = load({ meta: "index.enc", search: "?b=one", index: INDEX });
  const entry = await pool.ready();
  assert.equal(entry.title, "One");
  assert.equal(pool.addressOf("data/manifest.enc"), "ab/cd.bin");
  assert.equal(pool.subject().title, "S");
  assert.deepEqual(calls, ["index.enc", "decrypt"]);
  await pool.ready();
  assert.equal(calls.length, 2, "the index is read once");
});

test("the pool folder follows the index address", async () => {
  const { pool } = load({ meta: "../x/index.enc", search: "?b=one", index: INDEX });
  await pool.ready();
  assert.equal(pool.addressOf("data/manifest.enc"), "../x/ab/cd.bin");
});

test("a file the index does not list is an error", async () => {
  const { pool } = load({ meta: "index.enc", search: "?b=one", index: INDEX });
  await pool.ready();
  assert.throws(() => pool.addressOf("data/other.enc"), /file list/);
});

test("addressOf before ready is an error", () => {
  const { pool } = load({ meta: "index.enc", search: "?b=one", index: INDEX });
  assert.throws(() => pool.addressOf("data/manifest.enc"), /not loaded/);
});

test("an unknown or missing book is an error", async () => {
  for (const search of ["?b=nine", ""]) {
    const { pool } = load({ meta: "index.enc", search, index: INDEX });
    await assert.rejects(pool.ready(), /book/);
  }
});

test("a newer index format is refused", async () => {
  const { pool } = load({ meta: "index.enc", search: "?b=one",
    index: { ...INDEX, format: 3 } });
  await assert.rejects(pool.ready(), /newer version/);
});

test("a missing index file is an error", async () => {
  const { pool } = load({ meta: "index.enc", search: "?b=one", index: INDEX, status: 404 });
  await assert.rejects(pool.ready(), /index 404/);
});
