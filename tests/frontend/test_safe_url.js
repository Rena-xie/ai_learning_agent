"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { safeExternalUrl } = require(
  "../../src/learning_agent/web/safe-url.js",
);

test("accepts only normalized HTTP(S) links", () => {
  assert.equal(
    safeExternalUrl(" https://Example.com/path?q=1#section "),
    "https://example.com/path?q=1",
  );
  assert.equal(safeExternalUrl("/relative/path"), "http://127.0.0.1/relative/path");
});

test("rejects script-capable and credential-bearing links", () => {
  for (const value of [
    "javascript:alert(document.domain)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
    "https://user:password@example.com/path",
    "https://example.com/a b",
  ]) {
    assert.equal(safeExternalUrl(value), null);
  }
});
