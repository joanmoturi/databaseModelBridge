import { describe, expect, it, vi, afterEach } from "vitest";
import { authenticatedJson, parseAudit, parseReadiness, parseWorkspaces } from "../lib/api";

afterEach(() => { vi.unstubAllGlobals(); });

describe("API boundary", () => {
  it("accepts real typed readiness and preserves unavailable worker", () => {
    expect(parseReadiness({
      status: "not_ready", checks: { metadata_database: true, worker: false },
    }).checks.worker).toBe(false);
  });
  it("does not infer ready from malformed responses", () => {
    expect(() => parseReadiness({ status: "ready", checks: { worker: "true" } })).toThrow();
  });
  it("rejects malformed workspace and audit data", () => {
    expect(() => parseWorkspaces([{ id: 1 }])).toThrow();
    expect(() => parseAudit([{ action: "unknown" }])).toThrow();
  });
  it("reports authentication failures without echoing tokens", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("denied", { status: 401 })));
    await expect(authenticatedJson("api/v1/workspaces", "synthetic-test-token"))
      .rejects.toThrow("Invalid owner credential");
  });
});
