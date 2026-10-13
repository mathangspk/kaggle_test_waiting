import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import HoursPage from "../src/pages/hours";
import OverviewPage from "../src/pages/overview";
import RoadmapPage from "../src/pages/roadmap";
import { adaptAnalysis, adaptDecision } from "../src/lib/analysis";
import { AnalysisWindows, DecisionPanel } from "../src/components/shared/analysis-panels";
import { contractBucket, contractCandidate } from "./contract-helpers";

const { monitor } = vi.hoisted(() => ({ monitor: { summary: {}, roadmap: {}, analysis: {}, decision: {} } as Record<string, unknown> }));
vi.mock("../src/hooks/use-monitor-data", () => ({ useMonitorData: () => monitor }));
vi.mock("../src/hooks/use-timezone", () => ({ useTimezone: () => ({ mode: "vn" }) }));
vi.mock("../src/hooks/use-analysis", () => ({ useAnalysis: () => monitor.analysis, useDecision: () => monitor.decision }));

const now = new Date("2026-10-13T12:00:00Z");
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  monitor.summary = {
    error: null, loading: false, lastLoaded: now.getTime(),
    data: {
      meta: {}, accounts: [], runs: [], statistics: { sample_count: 178 },
      hours: [{ hour: 7, sample_count: 3, queue_sample_count: 0, unknown_queue: 3, median_queue_seconds: null, day_count: 1, success_5m_rate: 1, rank_eligible: false, rank: null }],
    },
  };
  monitor.analysis = { data: adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [contractBucket({ attempted: 3, resolved: 1, fast: 1, pending: 2, unknown: 2, skipped: 4, day_count: 1, account_count: 1, success_rate: 1, rate_lower: 1 / 3, rate_upper: 1, median_seconds: null, p90_seconds: null, eligible: false })] }), error: null, loading: false, lastLoaded: now.getTime() };
  monitor.decision = { data: null, error: null, loading: false, lastLoaded: null };
});

it("shows wait plus start comparison and warns about decision day scope", () => {
  const baseline = contractCandidate({ total_p50_seconds: 180, total_p90_seconds: 300, bucket: contractBucket({ median_seconds: 180, p90_seconds: 300 }) });
  const recommendation = contractCandidate({ at: "2026-10-17T01:00:00Z", hour_start: 8, waiting_seconds: 60, total_p50_seconds: 90, total_p90_seconds: 180, bucket: contractBucket({ hour_start: 8, median_seconds: 30, p90_seconds: 120 }) });
  monitor.decision = { data: adaptDecision({ status: "tentative", recommended_at: recommendation.at, candidates: [baseline, recommendation], selection_basis: "descriptive_p50_p90_pareto", limitations: ["no_latency_confidence"], certainty: "descriptive_only" }), error: null, loading: false, lastLoaded: now.getTime() };
  render(<DecisionPanel filters={{ source: "probe", account: "", day_type: "weekend" }} />);
  expect(screen.getByText(/Gợi ý sơ bộ backend:/)).toBeTruthy();
  expect(screen.getByText(/Tentative · so sánh lịch sử/)).toBeTruthy();
  expect(screen.getAllByText("90 giây / 180 giây").length).toBeGreaterThan(0);
  expect(screen.getByText(/backend dùng loại ngày thực tế/)).toBeTruthy();
  expect(screen.getByText(/loại thất bại và kết quả chưa rõ; không phải dự đoán thống kê/)).toBeTruthy();
  expect(screen.getByText(/Wilson không đo độ tin cậy/)).toBeTruthy();
  expect(screen.getByText(/Pareto mô tả: chỉ gợi ý/)).toBeTruthy();
  expect(screen.getByText(/no_latency_confidence/)).toBeTruthy();
  expect(screen.getByText(/Chỉ mô tả \(descriptive_only\)/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Gửi ngay" })).toBeNull();
});

it.each([
  ["latency_tradeoff", "Đánh đổi độ trễ"],
  ["tradeoff", "Đánh đổi độ trễ"],
  ["equivalent_total_latency", "Tổng độ trễ tương đương"],
  ["equivalent", "Tổng độ trễ tương đương"],
  ["insufficient_evidence", "Chưa đủ bằng chứng để so sánh độ trễ"],
  ["missing_baseline", "Thiếu bằng chứng cho phương án gửi ngay"],
  ["future_reason", "Lý do backend chưa có nhãn: future_reason"],
])("labels backend abstention reason %s without reclassifying it", (reason, label) => {
  monitor.decision = { data: adaptDecision({ status: "abstain", reason, recommended_at: null, candidates: [] }), error: null, loading: false, lastLoaded: now.getTime() };
  render(<DecisionPanel filters={{ source: "probe", account: "", day_type: "all" }} />);
  expect(screen.getByText((text) => text.startsWith("Không đưa khuyến nghị:") && text.includes(label))).toBeTruthy();
});

it("renders missing recommendation evidence as abstention with unknown timings", () => {
  monitor.decision = { data: adaptDecision({ status: "abstain", candidates: [{ hour_start: 8, waiting_seconds: null, bucket: { eligible: false } }] }), error: null, loading: false, lastLoaded: now.getTime() };
  render(<DecisionPanel filters={{ source: "production", account: "", day_type: "all" }} />);
  expect(screen.getByText(/Không đưa khuyến nghị:/)).toBeTruthy();
  expect(screen.getAllByText("Chưa có dữ liệu / Chưa có dữ liệu").length).toBe(2);
  expect(screen.queryByText("0 giây")).toBeNull();
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

function roadmap(syncedAt: string | null, error: string | null = null) {
  monitor.roadmap = {
    data: { repo: "test/repo", synced_at: syncedAt, generated_at: now.toISOString(), issues: [], dependencies: [], blocked: [] },
    lastLoaded: now.getTime(), loading: false, error,
  };
  render(<RoadmapPage />);
}

it("separates resolved point, unresolved outcome bounds and secondary queue", () => {
  render(<HoursPage />);
  const window = screen.getByText("06:00 – 08:00").closest(".rounded-xl") as HTMLElement;
  expect(within(window).getByText(/Nhanh 100%/)).toBeTruthy();
  expect(within(window).getByText("Cận kết quả: 33,3% – 100%")).toBeTruthy();
  expect(within(window).getByText("Trung vị Chưa có dữ liệu")).toBeTruthy();
  const cell = screen.getByText("07:00").closest(".rounded-lg") as HTMLElement;
  expect(within(cell).getByText("3 mẫu gửi · 0 mẫu queue")).toBeTruthy();
  expect(within(cell).getByText("Queue chưa rõ")).toBeTruthy();
  expect(within(cell).queryByText("0 giây")).toBeNull();
});

it("shows production absence honestly without a probe or old aggregate rate", () => {
  monitor.analysis = { data: adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [], accounts: [] }), error: null, loading: false, lastLoaded: now.getTime() };
  render(<MemoryRouter><OverviewPage /></MemoryRouter>);
  expect(screen.getByText("Chưa có dữ liệu cho nguồn và nhóm đã chọn.")).toBeTruthy();
  expect(screen.getByText("Theo cửa sổ 2 giờ")).toBeTruthy();
  expect(screen.queryByText("100%")).toBeNull();
});

it("renders weighted 50% and keeps missing coverage null instead of showing pooled 100%", () => {
  const data = adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [
    contractBucket({ attempted: 120, resolved: 120, fast: 100, success_rate: 0.5, rate_lower: 0.5, rate_upper: 0.5 }),
    contractBucket({ hour_start: 8, label: "08:00 – 10:00", attempted: 100, resolved: 100, fast: 100, success_rate: null, rate_lower: 0.5, rate_upper: 1, eligible: false, reason: "missing_account_coverage" }),
  ] });
  render(<AnalysisWindows data={data} />);
  expect(screen.getByText(/Nhanh 50%/)).toBeTruthy();
  expect(screen.getByText(/Nhanh —/)).toBeTruthy();
  expect(screen.queryByText(/Nhanh 100%/)).toBeNull();
  expect(screen.queryByText(/Nhanh 83,3%/)).toBeNull();
});

it("labels Overview sample_count as submission attempts rather than evidence", () => {
  render(<MemoryRouter><OverviewPage /></MemoryRouter>);
  expect(screen.getByText("178 lượt thử gửi")).toBeTruthy();
  expect(screen.queryByText(/mẫu có bằng chứng/)).toBeNull();
});

it("keeps a ten-minute-old deployment snapshot fresh under the 24-hour roadmap policy", () => {
  roadmap(new Date(now.getTime() - 10 * 60 * 1000).toISOString());
  expect(screen.getByText("Ảnh chụp lộ trình · đồng bộ trong 24 giờ")).toBeTruthy();
  expect(screen.getByText(/Đồng bộ ảnh chụp \(bản triển khai\)/)).toBeTruthy();
  expect(screen.queryByText(/Dữ liệu cũ/)).toBeNull();
});

it("marks an old sync snapshot stale even when its API fetch/generated_at is current", () => {
  roadmap(new Date(now.getTime() - 25 * 60 * 60 * 1000).toISOString());
  expect(screen.getByText("Ảnh chụp lộ trình cũ · đồng bộ đã quá 24 giờ")).toBeTruthy();
});

it("does not invent a sync timestamp from a successful fetch", () => {
  roadmap(null);
  expect(screen.getByText("Chưa rõ tuổi ảnh chụp lộ trình")).toBeTruthy();
});

it("shows a roadmap fetch failure separately from snapshot age while retaining issues", () => {
  roadmap(now.toISOString(), "HTTP 503");
  expect(screen.getByText("Ảnh chụp lộ trình · đồng bộ trong 24 giờ")).toBeTruthy();
  expect(screen.getByText("Lỗi tải · giữ ảnh chụp lộ trình gần nhất")).toBeTruthy();
  expect(screen.getByText("test/repo")).toBeTruthy();
});
