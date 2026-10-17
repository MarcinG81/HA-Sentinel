import assert from "node:assert/strict";
import { test } from "node:test";
import { uuid4 } from "../src/dom.ts";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("uuid4 works without crypto.randomUUID (plain HTTP installs)", () => {
  const original = globalThis.crypto.randomUUID;
  Object.defineProperty(globalThis.crypto, "randomUUID", { value: undefined, configurable: true });
  try {
    const ids = new Set(Array.from({ length: 100 }, () => uuid4()));
    assert.equal(ids.size, 100);
    for (const id of ids) assert.match(id, V4);
  } finally {
    Object.defineProperty(globalThis.crypto, "randomUUID", { value: original, configurable: true });
  }
});
