import assert from "node:assert/strict";
import test from "node:test";

import { firstHeader } from "./request-origin.mjs";

test("empty forwarded host falls back to real Host header", () => {
  assert.equal(
    firstHeader("", "alice-dev-22706bfa6066.example.invalid"),
    "alice-dev-22706bfa6066.example.invalid",
  );
});

test("first non-empty forwarded value wins and is trimmed", () => {
  assert.equal(firstHeader(" https , http", "http"), "https");
});

test("empty forwarded proto can fall back to transport scheme", () => {
  assert.equal(firstHeader("", "https"), "https");
});
