import { describe, expect, it } from "vitest";
import { apiErrorMessage } from "./api-error";

describe("apiErrorMessage", () => {
  it("flattens a FastAPI 422 validation list", () => {
    const body = {
      detail: [
        { type: "value_error", loc: ["body", "title"], msg: "Value error, required", input: "" },
        { type: "value_error", loc: ["body", "decision"], msg: "Value error, required", input: "" },
      ],
    };
    expect(apiErrorMessage(422, "Unprocessable Entity", body)).toBe(
      "422 Unprocessable Entity: title: Value error, required; decision: Value error, required",
    );
  });
  it("flattens our own {source, error} detail", () => {
    expect(apiErrorMessage(502, "Bad Gateway", { detail: { source: "espn", error: "boom" } })).toBe(
      "502 Bad Gateway: espn: boom",
    );
  });
  it("falls back to the status line", () => {
    expect(apiErrorMessage(500, "Internal Server Error", null)).toBe("500 Internal Server Error");
    expect(apiErrorMessage(400, "Bad Request", { detail: {} })).toBe("400 Bad Request");
  });
  it("takes a bare string detail", () => {
    expect(apiErrorMessage(404, "Not Found", { detail: "no such week" })).toBe(
      "404 Not Found: no such week",
    );
  });
});
