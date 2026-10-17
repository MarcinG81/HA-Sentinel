import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDate, translator } from "../src/i18n.ts";

test("every English key has a Polish translation and vice versa", async () => {
  const src = await import("node:fs").then((fs) => fs.readFileSync(new URL("../src/i18n.ts", import.meta.url), "utf8"));
  const block = (name: string) => {
    const start = src.indexOf(`const ${name}`);
    const end = src.indexOf("};", start);
    return new Set([...src.slice(start, end).matchAll(/^\s+"?([\w.]+)"?:/gm)].map((m) => m[1]));
  };
  const en = block("en");
  const pl = block("pl");
  assert.ok(en.size > 50);
  assert.deepEqual([...en].filter((k) => !pl.has(k)), []);
  assert.deepEqual([...pl].filter((k) => !en.has(k)), []);
});

test("translator picks Polish for pl and falls back to the key", () => {
  assert.equal(translator("pl")("tab.overview"), "Przegląd");
  assert.equal(translator("en-GB")("tab.overview"), "Overview");
  assert.equal(translator("pl")("no.such.key"), "no.such.key");
});

test("formatDate renders in the HA time zone with offset", () => {
  const out = formatDate("2026-10-17T09:42:03Z", "en-GB", "Europe/Warsaw", true);
  assert.match(out, /11:42:03/);
  assert.match(out, /GMT\+2/);
  assert.equal(formatDate(null, "en", "UTC"), "—");
});
