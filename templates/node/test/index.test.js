import { describe, expect, it } from "vitest";
import { placeholder } from "../src/index.js";

describe("smoke", () => {
  it("returns the project title", () => {
    expect(placeholder()).toBe("__PROJECT_TITLE__");
  });
});
