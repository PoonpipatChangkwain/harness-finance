import { readFileSync } from "node:fs";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020";
import { describe, expect, it } from "vitest";

import { EVENT_TYPES, parseTs } from "@/lib/events";
import { FIXTURE_NAMES, TRADING_FIXTURES, loadFixture, loadTradingRun } from "@/tests/fixtures";

const SCHEMA_PATH = path.join(import.meta.dirname, "../../../backend/schemas/events.schema.json");

/** Pydantic emits OpenAPI-style `discriminator.mapping`, which Ajv does not support; oneOf alone validates. */
function stripDiscriminator(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(stripDiscriminator);
  if (node && typeof node === "object") {
    return Object.fromEntries(
      Object.entries(node).filter(([k]) => k !== "discriminator").map(([k, v]) => [k, stripDiscriminator(v)]),
    );
  }
  return node;
}

const schema = stripDiscriminator(JSON.parse(readFileSync(SCHEMA_PATH, "utf8"))) as Record<string, unknown>;
const validate = new Ajv2020({ strict: false, validateFormats: false, allErrors: true }).compile(schema);

describe("event envelope schema", () => {
  it("has one TS event type per backend payload model", () => {
    const backendTypes = ((schema.oneOf as { $ref: string }[]) ?? []).map((r) => r.$ref);
    expect(backendTypes).toHaveLength(EVENT_TYPES.length);
  });

  it.each(FIXTURE_NAMES)("every event of fixture %s passes the backend schema", (name) => {
    for (const e of loadFixture(name)) {
      const ok = validate(e);
      expect(ok, `${name} seq ${e.seq} ${e.type}: ${JSON.stringify(validate.errors?.slice(0, 2))}`).toBe(true);
    }
  });

  it.each(TRADING_FIXTURES)("every event of trading fixture %s passes the backend schema", (name) => {
    for (const e of loadTradingRun(name)) {
      expect(validate(e), `${name} seq ${e.seq} ${e.type}`).toBe(true);
    }
  });
});

describe("parseTs", () => {
  it("treats a timestamp without zone as UTC", () => {
    expect(parseTs("2026-09-26T03:54:01.499762")).toBe(Date.parse("2026-09-26T03:54:01.499Z"));
    expect(parseTs("2026-09-26T03:54:01Z")).toBe(Date.parse("2026-09-26T03:54:01Z"));
  });
});
