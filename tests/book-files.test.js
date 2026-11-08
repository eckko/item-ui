// Tests for js/book-files.js. Run: node --test tests/book-files.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "js", "book-files.js"), "utf8");

/** Run the script on a fake page that has the given meta tags. */
function load(metas) {
  const window = {};
  const document = {
    querySelector: (selector) => {
      const name = /name="([^"]+)"/.exec(selector)[1];
      return name in metas ? { content: metas[name] } : null;
    },
  };
  vm.runInNewContext(SOURCE, { window, document });
  return window.RecallQuizBookFiles;
}

test("without meta tags the path is unchanged", () => {
  assert.equal(load({}).address("data/manifest.enc"), "data/manifest.enc");
});

test("book-base goes in front of the path", () => {
  const files = load({ "book-base": "https://w.dev/p/ab12/" });
  assert.equal(files.address("quiz/parts.enc"),
    "https://w.dev/p/ab12/quiz/parts.enc");
});

test("book-version is added as ?v=", () => {
  const files = load({ "book-base": "/p/x/", "book-version": "3" });
  assert.equal(files.address("media/a.enc"), "/p/x/media/a.enc?v=3");
});

test("the version is escaped", () => {
  const files = load({ "book-version": "a b&c" });
  assert.equal(files.address("f.enc"), "f.enc?v=a%20b%26c");
});
