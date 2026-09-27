import { test, expect } from "vitest";
import { allows } from "./src/probe.js";
test("boundary", () => {
  expect(allows(-1)).toBe(false);
  expect(allows(1)).toBe(true);
  expect(allows(0)).toBe(true);
});
