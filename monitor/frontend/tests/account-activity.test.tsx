import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountActivityEvidence, activityStatus } from "../src/components/shared/account-activity";
import type { AccountActivity } from "../src/types";

vi.mock("../src/hooks/use-timezone", () => ({ useTimezone: () => ({ mode: "vn" }) }));
const at = "2026-10-14T12:00:00Z";
const activity = (status = "observed_idle"): AccountActivity => ({
  status, coverage: "full", observed_at: at,
  sources: [{ source: "browser_discovery", observed_at: at, last_attempt_at: at,
    coverage: "full", freshness: "fresh", stale_after_seconds: 300, error: null, auth_error: null }],
  observations: [],
});
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(at)); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("does not use missing legacy probe evidence to imply account idle", () => {
  expect(activityStatus()).toBe("unknown");
  render(<AccountActivityEvidence />);
  expect(screen.getByText("Chưa rõ")).toBeTruthy();
  expect(screen.getByText(/không suy ra rảnh từ probe hoàn tất/)).toBeTruthy();
});

it("expires full idle evidence based on observation rather than response timestamp", () => {
  const data = activity();
  expect(activityStatus(data)).toBe("observed_idle");
  vi.advanceTimersByTime(300001);
  expect(activityStatus(data)).toBe("stale");
  render(<AccountActivityEvidence activity={data} />);
  expect(screen.getByText("Bằng chứng cũ")).toBeTruthy();
  expect(screen.queryByText("Quan sát không có hoạt động")).toBeNull();
});

it("does not certify idle from a retained snapshot after a failed summary fetch", () => {
  render(<AccountActivityEvidence activity={activity()} collectionError="HTTP 503" />);
  expect(screen.getByText("Chưa rõ")).toBeTruthy();
  expect(screen.queryByText("Quan sát không có hoạt động")).toBeNull();
  expect(screen.getByText(/browser_discovery · full · fresh/)).toBeTruthy();
});

it("preserves source, timestamp, partial coverage, failure and GPU provenance", () => {
  const data = activity("unknown");
  data.coverage = "partial";
  data.sources[0] = { ...data.sources[0], coverage: "none", auth_error: "logged_out" };
  data.observations = [{ identity: "event:v3", account: "a", source: "browser_discovery", run_type: "production",
    provider_state: "RUNNING", state: "running", observed_at: at, coverage: "none", freshness: "fresh", gpu_requested: "T4x2" }];
  render(<AccountActivityEvidence activity={data} />);
  expect(screen.getByText(/event:v3 · production · browser_discovery/)).toBeTruthy();
  expect(screen.getByText(/logged_out · giữ bằng chứng gần nhất/)).toBeTruthy();
  expect(screen.getByText(/GPU yêu cầu: T4x2 · thiết bị xác minh: chưa rõ/)).toBeTruthy();
  expect(screen.getByText(/Độ phủ: partial/)).toBeTruthy();
  expect(screen.getAllByText(/14\/10\/2026/).length).toBeGreaterThan(0);
});

it("expires running evidence but keeps unresolved submissions explicit", () => {
  const data = activity("running");
  data.observations = [{ identity: "v3", account: "a", source: "browser_discovery", run_type: "production",
    provider_state: "running", state: "running", observed_at: at, coverage: "partial", freshness: "fresh" }];
  expect(activityStatus(data)).toBe("running");
  vi.advanceTimersByTime(301000);
  expect(activityStatus(data)).toBe("stale");
  expect(activityStatus({ ...data, status: "unresolved" })).toBe("unresolved");
});

it("bounds displayed history and prioritizes nonterminal evidence without implying full display", () => {
  const data = activity("unresolved");
  data.observations = Array.from({ length: 12 }, (_, index) => ({
    identity: `completed-${index}`, account: "a", source: "browser_discovery", run_type: "production",
    provider_state: "complete", state: "terminal", observed_at: at, coverage: "partial", freshness: "fresh",
  }));
  data.observations.push({ ...data.observations[0], identity: "unresolved-old", state: "unresolved" });
  render(<AccountActivityEvidence activity={data} />);
  expect(screen.getByText(/Hiển thị 10\/13 bằng chứng/)).toBeTruthy();
  expect(screen.getByText(/unresolved-old · production/)).toBeTruthy();
  expect(screen.queryByText(/completed-11 · production/)).toBeNull();
});
