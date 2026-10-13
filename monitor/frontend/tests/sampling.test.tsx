import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { adaptAnalysis } from "../src/lib/analysis";
import { AnalysisWindows } from "../src/components/shared/analysis-panels";

afterEach(cleanup);
function renderSampling(source: "probe" | "production", sampling: Record<string, unknown>) {
  return render(<AnalysisWindows data={adaptAnalysis({ schema_version: 2, metric: "submit_to_start", source, buckets: [], sampling })} />);
}

it.each([[30, "192"], [60, "96"], [120, "48"]])("describes one/account/window and derives daily opportunities at %s minutes", (interval, daily) => {
  renderSampling("probe", { interval_minutes: interval });
  expect(screen.getByText(/một cơ hội \/ tài khoản \/ cửa sổ lấy mẫu/)).toBeTruthy();
  expect(screen.getByText(/tối đa 4 cơ hội tổng cộng mỗi cửa sổ/)).toBeTruthy();
  expect(screen.getByText((text) => text.includes(`4 × 1440 / ${interval} = ${daily} cơ hội/ngày`))).toBeTruthy();
  expect(screen.getByText(/không phải số lượt gửi chắc chắn/)).toBeTruthy();
  expect(screen.getByText(/Cửa sổ lấy mẫu khác cửa sổ phân tích 2 giờ/)).toBeTruthy();
  expect(screen.queryByText(/4 lượt \/ tài khoản/)).toBeNull();
});
it.each([null, 0, -30, "30"])("does not assume a scheduler interval when backend reports %s", (interval) => {
  renderSampling("probe", { interval_minutes: interval });
  expect(screen.getByText(/chưa tính được số cơ hội\/ngày/)).toBeTruthy();
  expect(screen.queryByText(/192/)).toBeNull();
});
it("describes production external submissions without applying probe schedule quotas", () => {
  renderSampling("production", { interval_minutes: 30, attempted: 2, observed_opportunities: 2 });
  expect(screen.getByText(/Production: các lượt gửi từ bên ngoài/)).toBeTruthy();
  expect(screen.queryByText(/Kế hoạch probe:/)).toBeNull();
  expect(screen.queryByText(/4 × 1440/)).toBeNull();
  expect(screen.queryByText(/192/)).toBeNull();
  expect(screen.queryByText(/4 tài khoản cấu hình/)).toBeNull();
});
it("explains preliminary unavailable heldout and seven training/seven test days without JSON", () => {
  const { container } = renderSampling("probe", { phase: "preliminary", interval_minutes: 60, heldout: { status: "unavailable", reason: "insufficient_dates", min_training_days: 7, min_heldout_days: 7, training_dates: [], heldout_dates: [] } });
  expect(screen.getByText(/Cần ít nhất 7 ngày huấn luyện và 7 ngày kiểm tra riêng biệt/)).toBeTruthy();
  expect(screen.getByText(/Chưa đủ dữ liệu kiểm tra · Chưa đủ ngày/)).toBeTruthy();
  expect(screen.getByText(/không phải thử nghiệm đối chứng/)).toBeTruthy();
  expect(container.querySelector("pre")).toBeNull();
});
it("shows heldout weighted comparison and negative observed difference without causal savings", () => {
  const dates = (start: number) => Array.from({ length: 7 }, (_, i) => `2026-09-${String(start + i).padStart(2, "0")}`);
  renderSampling("probe", { heldout: { status: "available", reason: "noncausal_observational_comparison", training_dates: dates(1), heldout_dates: dates(8), training_record_count: 84, heldout_record_count: 84, selected_hours: [8], selected: { success_rate: 0, rate_lower: 0, rate_upper: 0, attempted: 42, resolved: 42, coverage: { observed: 2, configured: 2 } }, baseline: { success_rate: 0.5, rate_lower: 0.5, rate_upper: 0.5, attempted: 84, resolved: 84, coverage: { observed: 2, configured: 2 } }, observed_rate_difference: -0.5, savings_seconds: 1000 } });
  expect(screen.getByText(/Tập huấn luyện: 7 ngày · 84 bản ghi. Tập kiểm tra: 7 ngày · 84 bản ghi/)).toBeTruthy();
  expect(screen.getByText(/08:00–10:00/)).toBeTruthy();
  expect(screen.getByText(/Khung giờ được chọn trên tập kiểm tra: nhanh 0%/)).toBeTruthy();
  expect(screen.getByText(/Baseline toàn bộ tập kiểm tra: nhanh 50%/)).toBeTruthy();
  expect(screen.getByText(/−?\-?50 điểm phần trăm/)).toBeTruthy();
  expect(screen.queryByText(/1000/)).toBeNull();
});
it("keeps missing heldout point and unknown status/reason honest", () => {
  renderSampling("production", { heldout: { status: "new_status", reason: "new_reason", selected: { success_rate: null, rate_lower: 0, rate_upper: 1 } } });
  expect(screen.getByText(/Backend: new_status · Lý do backend: new_reason/)).toBeTruthy();
  expect(screen.getByText(/Khung giờ được chọn trên tập kiểm tra: nhanh —/)).toBeTruthy();
});
