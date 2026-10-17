import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { sha256Hex } from "../src/sha256.ts";

test("pure sha256 matches node:crypto across block boundaries and UTF-8", () => {
  const samples = ["", "abc", "a".repeat(55), "a".repeat(56), "a".repeat(64), "a".repeat(1000),
    "- alias: Ogrzewanie łazienki ✓\n  mode: single\n"];
  for (const s of samples) {
    const bytes = new TextEncoder().encode(s);
    assert.equal(sha256Hex(bytes), createHash("sha256").update(bytes).digest("hex"), s.slice(0, 20));
  }
});
