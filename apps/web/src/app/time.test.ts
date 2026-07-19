import { describe, expect, it } from "vitest";
import { clamp, formatTime, parseTimecode } from "./time";

describe("formatTime", () => {
  it("clamps negatives to zero", () => expect(formatTime(-5)).toBe("00:00"));
  it("renders MM:SS below an hour", () => expect(formatTime(95)).toBe("01:35"));
  it("pads seconds and minutes", () => expect(formatTime(9)).toBe("00:09"));
  it("renders HH:MM:SS at and above an hour", () => expect(formatTime(3661)).toBe("01:01:01"));
  it("rounds to the nearest second", () => expect(formatTime(59.6)).toBe("01:00"));
});

describe("parseTimecode", () => {
  it("parses bare seconds", () => expect(parseTimecode("42")).toBe(42));
  it("parses MM:SS", () => expect(parseTimecode("01:35")).toBe(95));
  it("parses HH:MM:SS", () => expect(parseTimecode("01:01:01")).toBe(3661));
  it("accepts fractional seconds", () => expect(parseTimecode("00:01.5")).toBe(1.5));
  it("rejects out-of-range seconds", () => expect(parseTimecode("01:75")).toBeNull());
  it("rejects out-of-range minutes in HH:MM:SS", () => expect(parseTimecode("01:75:00")).toBeNull());
  it("rejects non-numeric parts", () => expect(parseTimecode("aa:bb")).toBeNull());
  it("rejects too many segments", () => expect(parseTimecode("1:2:3:4")).toBeNull());
  it("is a round-trip with formatTime for whole seconds", () => expect(parseTimecode(formatTime(3661))).toBe(3661));
});

describe("clamp", () => {
  it("returns the value inside range", () => expect(clamp(5, 0, 10)).toBe(5));
  it("clamps below the minimum", () => expect(clamp(-3, 0, 10)).toBe(0));
  it("clamps above the maximum", () => expect(clamp(42, 0, 10)).toBe(10));
});
