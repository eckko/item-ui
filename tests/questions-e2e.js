// Used by tests/test_split_questions.py: opens a site written by build_pool.py
// with the real js/unlock.js, js/pool-files.js and js/early-questions.js and
// prints the question bank the quiz would get.
// usage: node tests/questions-e2e.js SITE KEYHEX BOOK_ID
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const [site, keyHex, bookId] = process.argv.slice(2);
const js = (name) => fs.readFileSync(path.join(__dirname, "..", "js", name), "utf8");

const requested = [];
const window = { location: { search: "?b=" + bookId },
  RecallQuizEncrypted: { questions: "quiz/questions.enc" } };
const sandbox = {
  window, crypto: webcrypto, Blob, Response, DecompressionStream,
  URLSearchParams, TextDecoder, TextEncoder, Uint8Array, Error, JSON, Promise,
  Object, Array,
  document: { querySelector: () => ({ content: "index.enc" }) },
  fetch: async (address) => {
    requested.push(address);
    const file = path.join(site, address);
    if (!fs.existsSync(file)) return { ok: false, status: 404 };
    const bytes = fs.readFileSync(file);
    return { ok: true, status: 200,
      arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) };
  },
};
vm.createContext(sandbox);
vm.runInContext(js("unlock.js"), sandbox);
vm.runInContext(js("pool-files.js"), sandbox);

(async () => {
  const key = await webcrypto.subtle.importKey(
    "raw", Buffer.from(keyHex, "hex"), "AES-GCM", false, ["decrypt"]);
  window.SubjectUnlock.ready = async () => ({ key });
  window.RecallQuizPool.enabled = true;
  vm.runInContext(js("early-questions.js"), sandbox);
  const bank = await window.RecallQuizQuestions.promise;
  console.log(JSON.stringify({ bank, requests: requested.length }));
})().catch((error) => { console.error(error.stack); process.exit(1); });
