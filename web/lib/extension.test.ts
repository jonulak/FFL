import { describe, expect, it } from "vitest";
import { extMessage } from "./extension";

const self = {};
const O = "https://ffl.example";

describe("extMessage", () => {
  it("reads the extension's replies from this window and origin", () => {
    expect(extMessage({ "ffl-ext": true, type: "here", paired: true }, self, O, self, O)).toEqual({ type: "here", paired: true });
    expect(extMessage({ "ffl-ext": true, type: "code", code: "ABCD-EFGH" }, self, O, self, O)).toEqual({ type: "code", code: "ABCD-EFGH" });
    expect(extMessage({ "ffl-ext": true, type: "done", paired: false }, self, O, self, O)).toEqual({ type: "done", paired: false });
    expect(extMessage({ "ffl-ext": true, type: "error" }, self, O, self, O)?.type).toBe("error");
  });
  it("ignores another frame, another origin, the page's own asks and anything else", () => {
    const here = { "ffl-ext": true, type: "here", paired: true };
    expect(extMessage(here, {}, O, self, O)).toBeNull();
    expect(extMessage(here, self, "https://evil.example", self, O)).toBeNull();
    expect(extMessage({ "ffl-ext": true, type: "hello" }, self, O, self, O)).toBeNull();
    expect(extMessage({ "ffl-ext": true, type: "code", code: "" }, self, O, self, O)).toBeNull();
    expect(extMessage({ type: "here" }, self, O, self, O)).toBeNull();
    expect(extMessage("here", self, O, self, O)).toBeNull();
    expect(extMessage(null, self, O, self, O)).toBeNull();
  });
});
