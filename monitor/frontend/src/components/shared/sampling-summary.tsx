import type { Analysis, Source } from "@/lib/analysis";
import { formatCount, formatPercent } from "@/lib/format";

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const count = (value: unknown): number | null => { const n = numeric(value); return n !== null && Number.isInteger(n) ? n : null; };
const rate = (value: unknown): number | null => { const n = numeric(value); return n !== null && n <= 1 ? n : null; };
const dateCount = (value: unknown): number | null => Array.isArray(value) && value.every((v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) ? new Set(value).size : null;
const REASONS: Record<string, string> = {
  insufficient_dates: "Chưa đủ ngày để tách tập chọn khung giờ và tập kiểm tra",
  insufficient_training_bucket_evidence: "Chưa đủ bằng chứng theo tài khoản trong các khung giờ huấn luyện",
  insufficient_heldout_account_coverage: "Thiếu độ phủ tài khoản trong tập kiểm tra",
  insufficient_selected_heldout_dates: "Các khung giờ được chọn chưa phủ đủ ngày kiểm tra",
  noncausal_observational_comparison: "So sánh quan sát, không xác định quan hệ nhân quả",
};

export function SamplingSummary({ data, source }: { data: Analysis; source: Source | null }) {
  const sampling = data.sampling;
  const interval = numeric(sampling.interval_minutes);
  const validInterval = interval !== null && interval > 0;
  const h = record(sampling.heldout);
  const training = dateCount(h.training_dates), heldout = dateCount(h.heldout_dates);
  const minTrain = Math.max(7, count(h.min_training_days) ?? 7), minTest = Math.max(7, count(h.min_heldout_days) ?? 7);
  const selected = record(h.selected), baseline = record(h.baseline);
  const hours = Array.isArray(h.selected_hours) ? h.selected_hours.filter((v): v is number => count(v) !== null && (v as number) <= 22 && (v as number) % 2 === 0) : [];
  const reason = typeof h.reason === "string" ? (Object.prototype.hasOwnProperty.call(REASONS, h.reason) ? REASONS[h.reason] : `Lý do backend: ${h.reason}`) : null;
  function result(label: string, value: Record<string, unknown>) {
    const coverage = record(value.coverage);
    return <p>{label}: nhanh {formatPercent(rate(value.success_rate))} · cận kết quả {formatPercent(rate(value.rate_lower))} – {formatPercent(rate(value.rate_upper))} · {formatCount(count(value.attempted))} attempted / {formatCount(count(value.resolved))} resolved · độ phủ {formatCount(count(coverage.observed))} / {formatCount(count(coverage.configured))} tài khoản</p>;
  }
  return <section className="space-y-2 rounded-lg border p-4 text-xs text-muted-foreground" aria-label="Lấy mẫu và kiểm tra ngoài tập chọn">
    <h3 className="text-sm font-medium text-foreground">Lấy mẫu · tín hiệu sơ bộ</h3>
    {source === "probe" ? <>
      <p>Kế hoạch probe: một cơ hội / tài khoản / cửa sổ lấy mẫu. Với 4 tài khoản cấu hình, tối đa 4 cơ hội tổng cộng mỗi cửa sổ được chọn (không phải 4 lượt cho mỗi tài khoản).</p>
      <p>{validInterval ? `Khoảng lấy mẫu backend: ${formatCount(interval)} phút; tối đa 4 × 1440 / ${formatCount(interval)} = ${formatCount(4 * 1440 / interval)} cơ hội/ngày cho 4 tài khoản nếu phủ đủ ngày.` : "Backend chưa cung cấp khoảng lấy mẫu hợp lệ; chưa tính được số cơ hội/ngày."} Đây là cơ hội theo kế hoạch, không phải số lượt gửi chắc chắn; tài khoản bận, chưa sẵn sàng hoặc skipped có thể làm giảm số lượt gửi.</p>
      <p>Cửa sổ lấy mẫu khác cửa sổ phân tích 2 giờ. Skipped không thuộc attempted; kế hoạch không bảo đảm thành công hoặc cho biết GPU rảnh toàn hệ thống.</p>
    </> : source === "production" ? <p>Production: các lượt gửi từ bên ngoài được ghi nhận riêng, không phải lịch probe. Số cơ hội quan sát không phải hạn mức hay bảo đảm cấp phát; thời gian do bên gửi tự báo cáo không thay thế bằng chứng đã xác minh.</p> : <p>Chưa xác định nguồn lấy mẫu; chưa diễn giải lịch gửi.</p>}
    <p>Đã quan sát: {formatCount(count(sampling.observed_opportunities))} cơ hội · {formatCount(count(sampling.attempted))} attempted · {formatCount(count(sampling.skipped))} skipped · {formatCount(count(sampling.known_successes))} thành công đã biết · {formatCount(count(sampling.day_count))} ngày.</p>
    <h4 className="pt-2 font-medium text-foreground">Kiểm tra ngoài tập chọn (held-out) · sơ bộ</h4>
    <p>Cần ít nhất {minTrain} ngày huấn luyện và {minTest} ngày kiểm tra riêng biệt: ngày cũ dùng chọn khung giờ, {minTest} ngày gần nhất dùng kiểm tra. Chưa đủ ngày thì tiếp tục thu thập, chưa đánh giá hiệu quả.</p>
    <p>Trạng thái: {h.status === "available" ? "Có so sánh quan sát sơ bộ" : h.status === "unavailable" ? "Chưa đủ dữ liệu kiểm tra" : typeof h.status === "string" ? `Backend: ${h.status}` : "Chưa có dữ liệu kiểm tra"}{reason ? ` · ${reason}` : ""}.</p>
    {Object.keys(h).length > 0 && <>
      <p>Tập huấn luyện: {formatCount(training)} ngày · {formatCount(count(h.training_record_count))} bản ghi. Tập kiểm tra: {formatCount(heldout)} ngày · {formatCount(count(h.heldout_record_count))} bản ghi.</p>
      {hours.length > 0 && <p>Khung giờ chọn từ tập huấn luyện: {hours.map((hour) => `${String(hour).padStart(2, "0")}:00–${String(hour + 2).padStart(2, "0")}:00`).join(", ")}.</p>}
      {Object.keys(selected).length > 0 && result("Khung giờ được chọn trên tập kiểm tra", selected)}
      {Object.keys(baseline).length > 0 && result("Baseline toàn bộ tập kiểm tra", baseline)}
      {typeof h.observed_rate_difference === "number" && Number.isFinite(h.observed_rate_difference) && Math.abs(h.observed_rate_difference) <= 1 && <p>Chênh lệch tỷ lệ quan sát (được chọn − baseline): {new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1, signDisplay: "always" }).format(h.observed_rate_difference * 100)} điểm phần trăm.</p>}
    </>}
    <p>Đây là so sánh quan sát theo nhóm, không phải thử nghiệm đối chứng hay ước lượng tiết kiệm thời gian có quan hệ nhân quả. Khung giờ được chọn là tập con của baseline; không chứng minh lợi ích của quyết định gửi, không cung cấp độ tin cậy cho độ trễ.</p>
  </section>;
}
