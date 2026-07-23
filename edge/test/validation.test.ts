import { describe, expect, it } from "vitest";

import { secureEqual, validateMarketEvent } from "../src/index";

describe("edge validation", () => {
  it("uses exact credential equality", () => {
    expect(secureEqual("same-secret", "same-secret")).toBe(true);
    expect(secureEqual("same-secret", "different!!")).toBe(false);
  });

  it("accepts only completed internally consistent bars", () => {
    const event = validateMarketEvent({
      schema_version: 1,
      kind: "bar",
      symbol: "ASX:OOO",
      timeframe: "1D",
      timestamp: "2026-07-23T06:00:00Z",
      open: 8.4,
      high: 8.6,
      low: 8.3,
      close: 8.5,
      volume: 100,
      confirmed: true,
    });
    expect(event.price).toBe(8.5);
    expect(() => validateMarketEvent({ ...event, confirmed: false })).toThrow(
      "only completed bars",
    );
  });
});
