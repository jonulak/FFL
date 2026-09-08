import { describe, expect, it, vi, afterEach } from "vitest";
import { API_TIMEOUT_MS, ApiError, apiGet } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiGet", () => {
  it("sends an abort signal so a hung API cannot hold a page open", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await apiGet("/health");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(API_TIMEOUT_MS).toBe(20_000);
  });

  it("reports a timeout as a readable ApiError", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    });
    await expect(apiGet("/week")).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      message: "cannot reach the API: TimeoutError",
    });
    await expect(apiGet("/week")).rejects.toBeInstanceOf(ApiError);
  });

  it("still names ECONNREFUSED when the API is down", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } });
    });
    await expect(apiGet("/week")).rejects.toMatchObject({
      message: "cannot reach the API: ECONNREFUSED",
    });
  });
});
