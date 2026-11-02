import React from "react";
import { act, render, screen } from "@testing-library/react";
import { expect, it, vi, afterEach } from "vitest";
import Dashboard, { ServiceStatus } from "../components/dashboard";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("shows missing worker as unavailable, not successful", () => {
  render(<ServiceStatus readiness={{
    status: "not_ready", checks: { metadata_database: true, worker: false },
  }} />);
  expect(screen.getByText("Missing or stale")).toBeInTheDocument();
  expect(screen.getByText("Connected · schema current")).toBeInTheDocument();
});

it("has masked access input and directs migration controls to the API", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    status: "ready", checks: { metadata_database: true, worker: true },
  }))));
  render(<Dashboard />);
  expect(screen.getByLabelText("Owner access credential")).toHaveAttribute("type", "password");
  expect(screen.getByText("Migration controls use the API.", { exact: false })).toBeInTheDocument();
  expect(await screen.findByText("Fresh · available")).toBeInTheDocument();
});

it("does not retain a ready badge when the API becomes unreachable", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      status: "ready", checks: { metadata_database: true, worker: true },
    })))
    .mockRejectedValue(new Error("disconnected")));
  await act(async () => { render(<Dashboard />); });
  expect(screen.getByText("Ready", { exact: true })).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(screen.getByText("Not ready", { exact: true })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("API unreachable");
});
