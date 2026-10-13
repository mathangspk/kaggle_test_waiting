import { useState } from "react";
import { useDecision } from "@/hooks/use-analysis";
import type { Analysis, AnalysisFilters, DecisionCandidate, Source } from "@/lib/analysis";
import { SamplingSummary } from "@/components/shared/sampling-summary";
import { decisionReasonLabel } from "@/lib/analysis";
import { formatCount, formatDateTime, formatPercent, formatSeconds } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { SectionTitle } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice } from "@/components/shared/status-badge";
import { DataFreshness } from "@/components/shared/data-freshness";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const LIMITATION_LABELS: Record<string, string> = {
  no_latency_confidence: "Không có đánh giá độ tin cậy thống kê cho độ trễ (no_latency_confidence)",
  no_statistical_guarantee: "Không có bảo đảm thống kê (no_statistical_guarantee)",
  confirmed_successes_only: "Chỉ tính thành công đã xác nhận; loại thất bại và chưa rõ (confirmed_successes_only)",
  pooled_latency_percentiles_may_reflect_account_imbalance: "Phân vị độ trễ gộp có thể chịu ảnh hưởng của số mẫu không cân bằng giữa tài khoản",
};

function Choice({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) {
  return <div className="min-w-40 space-y-1"><div className="text-xs text-muted-foreground">{label}</div>
    <Select value={value} onValueChange={onChange}><SelectTrigger aria-label={label}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map(([id, name]) => <SelectItem key={id} value={id}>{name}</SelectItem>)}</SelectContent>
    </Select></div>;
}
export function AnalysisControls({ filters, onChange, accounts }: { filters: AnalysisFilters; onChange: (filters: AnalysisFilters) => void; accounts: { id: string; name: string }[] }) {
  return <div className="mb-5 flex flex-wrap gap-3">
    <Choice label="Nguồn dữ liệu" value={filters.source} onChange={(source) => onChange({ ...filters, source: source as AnalysisFilters["source"] })} options={[["probe", "Probe"], ["production", "Production"]]} />
    <Choice label="Tài khoản" value={filters.account || "__all"} onChange={(account) => onChange({ ...filters, account: account === "__all" ? "" : account })} options={[["__all", "Tất cả tài khoản"], ...accounts.map((a): [string, string] => [a.id, a.name])]} />
    <Choice label="Loại ngày" value={filters.day_type} onChange={(day_type) => onChange({ ...filters, day_type: day_type as AnalysisFilters["day_type"] })} options={[["all", "Tất cả ngày"], ["weekday", "Ngày trong tuần"], ["weekend", "Cuối tuần"]]} />
  </div>;
}
export function AnalysisWindows({ data, source }: { data: Analysis; source?: Source }) {
  return <section className="space-y-3">
    <SectionTitle>Gửi → bắt đầu · cửa sổ 2 giờ</SectionTitle>
    <p className="text-sm text-muted-foreground">Mỗi tài khoản: tỷ lệ nhanh = fast / resolved. Tổng hợp dùng trọng số bằng nhau giữa các tài khoản cấu hình, không lấy fast / resolved gộp; thiếu tài khoản làm điểm ước lượng chưa rõ. Cận kết quả cũng theo trọng số tài khoản, giả sử unknown đều không nhanh hoặc đều nhanh; tài khoản chưa có mẫu đóng góp [0, 1]. Pending là một phần của unknown. Skipped không phải thành công.</p>
    {data.buckets.length === 0 ? <NoData message={data.reason ?? "Chưa có dữ liệu cho nguồn và nhóm đã chọn."} /> :
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{data.buckets.map((b) => <Card key={b.hour_start}><CardContent className="space-y-2 pt-5">
        <h3 className="font-semibold">{b.label}</h3>
        <div className="text-lg tabular-nums">Trung vị {formatSeconds(b.median_seconds)}</div>
        <div className="text-sm">P90 {formatSeconds(b.p90_seconds)} · Nhanh {formatPercent(b.success_rate)}</div>
        <div className="text-sm">Cận kết quả: {formatPercent(b.rate_lower)} – {formatPercent(b.rate_upper)}</div>
        <div className="text-xs text-muted-foreground">Wilson sơ bộ: {formatPercent(b.wilson_low)} – {formatPercent(b.wilson_high)}</div>
        <div className="text-xs text-muted-foreground">Số đếm gộp (không phải tỷ lệ): {formatCount(b.fast)} nhanh / {formatCount(b.resolved)} resolved · {formatCount(b.attempted)} attempted</div>
        <div className="text-xs text-muted-foreground">{formatCount(b.unknown)} unknown (gồm {formatCount(b.pending)} pending, {formatCount(b.overdue_unconfirmed)} quá hạn chưa xác nhận) · {formatCount(b.skipped)} skipped</div>
        <div className="text-xs text-muted-foreground">{formatCount(b.slow)} chậm · {formatCount(b.over10)} &gt;10 phút · {formatCount(b.failures)} lỗi</div>
        <div className="text-xs text-muted-foreground">{formatCount(b.resolved_day_count)} ngày resolved / {formatCount(b.day_count)} ngày quan sát · {formatCount(b.account_count)} tài khoản</div>
        <p className="text-xs">{b.eligible ? "Đủ điều kiện backend; chỉ so sánh nhóm tương đương" : `Chưa đủ điều kiện: ${b.reason}`}</p>
      </CardContent></Card>)}</div>}
    <p className="text-xs text-muted-foreground">Trung vị/P90 chỉ mô tả các lượt thành công T4×2 đã xác nhận (gộp), không bảo đảm thời gian hoàn thành. Wilson giả định các quan sát độc lập; probe cùng tài khoản/ngày có thể tương quan. Tổng hợp Wilson là trung bình chẩn đoán từng tài khoản, không phải khoảng tin cậy gộp. Mỗi tài khoản được chọn trong mỗi cửa sổ cần ≥{data.policy.min_days} ngày resolved, ≥{data.policy.min_resolved} resolved. Không xếp hạng nhóm khác nguồn, tài khoản hoặc loại ngày.</p>
    {data.accounts.length > 0 && <div className="text-xs text-muted-foreground">{data.accounts.map((a) => <div key={a.id}>{a.name}: {formatCount(a.attempted)} attempted · {formatCount(a.resolved)} resolved · {formatCount(a.day_count)} ngày</div>)}</div>}
    <SamplingSummary data={data} source={source ?? data.source} />
    {Object.entries(data.definitions).map(([key, value]) => <p key={key} className="text-xs text-muted-foreground">{key}: {value}</p>)}
  </section>;
}

export function DecisionPanel({ filters }: { filters: AnalysisFilters }) {
  const [mode, setMode] = useState<"earliest" | "schedule">("earliest");
  const [horizon, setHorizon] = useState(6);
  const state = useDecision(filters, mode, horizon);
  const data = state.data;
  function row(c: DecisionCandidate, label: string, key: string) {
    return <TableRow key={key}><TableCell>{label}{c.at && <div className="text-xs text-muted-foreground">{formatDateTime(c.at)} · {c.day_type}</div>}</TableCell><TableCell>{formatSeconds(c.wait_seconds)}</TableCell><TableCell>{formatSeconds(c.median_seconds)} / {formatSeconds(c.p90_seconds)}</TableCell><TableCell>{formatSeconds(c.total_median_seconds)} / {formatSeconds(c.total_p90_seconds)}</TableCell><TableCell>{c.eligible ? "Đủ điều kiện" : c.reason ?? "Thiếu bằng chứng"}</TableCell></TableRow>;
  }
  return <section className="space-y-3">
    <SectionTitle>Gửi ngay hay lên lịch?</SectionTitle>
    <div className="flex flex-wrap gap-3">
      <Choice label="Mục tiêu quyết định" value={mode} onChange={(value) => setMode(value as typeof mode)} options={[["earliest", "Bắt đầu sớm nhất"], ["schedule", "Chọn lịch gửi"]]} />
      <Choice label="Chân trời (giờ)" value={String(horizon)} onChange={(value) => setHorizon(Number(value))} options={[1, 3, 6, 12, 24].map((h) => [String(h), `${h} giờ`])} />
    </div>
    <p className="text-xs text-muted-foreground">Chỉ hỗ trợ quyết định, không tự gửi. So sánh thời gian chờ + gửi → bắt đầu theo cùng nguồn và tài khoản. Bảng này yêu cầu tất cả ngày; backend dùng loại ngày thực tế của từng thời điểm tương lai (weekday/weekend), độc lập với bộ lọc khung giờ. Tổng thời gian chỉ có điều kiện trên thành công đã xác nhận, không bảo đảm cấp phát hay hoàn thành.</p>
    <p className="text-xs text-muted-foreground">P50/P90 mô tả phân bố lịch sử của các lượt thành công đã xác nhận, loại thất bại và kết quả chưa rõ; không phải dự đoán thống kê. Cận tỷ lệ kết quả và Wilson không đo độ tin cậy của thời gian bắt đầu.</p>
    <OfflineNotice error={state.error} hasData={Boolean(data)} />
    <DataFreshness state={state} timestamp={data?.generated_at} />
    {!data ? <NoData message={state.loading ? "Đang tải quyết định…" : "Chưa có bằng chứng quyết định."} /> : <Card><CardContent className="space-y-3 pt-5">
      <p className="font-medium">{data.abstain ? "Không đưa khuyến nghị: " + decisionReasonLabel(data.reason) : `Gợi ý sơ bộ backend: ${formatDateTime(data.recommendation?.at)}`}</p>
      {data.tentative && <p className="text-sm text-muted-foreground">Tentative · so sánh lịch sử mang tính mô tả, không phải dự báo chắc chắn hoặc lời hứa thành công.</p>}
      {data.selection_basis && <p className="text-xs text-muted-foreground">Cơ sở lựa chọn: {data.selection_basis === "descriptive_p50_p90_pareto" ? `Pareto mô tả: chỉ gợi ý khi tổng chờ + P50 và chờ + P90 thấp hơn mọi phương án khác ít nhất ${data.material_margin_seconds ?? 30} giây. Có đánh đổi hoặc chênh lệch không đáng kể thì không chọn. Nếu chỉ có một ứng viên, không có so sánh trội hơn; không suy ra độ tin cậy thống kê.` : `Backend: ${data.selection_basis}`}</p>}
      {data.certainty && <p className="text-xs text-muted-foreground">Mức chắc chắn: {data.certainty === "descriptive_only" ? "Chỉ mô tả (descriptive_only)" : data.certainty === "none" ? "Không xác nhận (none)" : `Backend: ${data.certainty}`}</p>}
      {data.limitations.map((limitation, index) => <p key={`${limitation}-${index}`} className="text-xs text-muted-foreground">Giới hạn: {Object.prototype.hasOwnProperty.call(LIMITATION_LABELS, limitation) ? LIMITATION_LABELS[limitation] : `Backend: ${limitation}`}</p>)}
      {data.status && <p className="text-xs text-muted-foreground">Trạng thái: {data.status}</p>}
      {!data.abstain && data.reason && <p className="text-sm">{decisionReasonLabel(data.reason)}</p>}
      <Table><TableHeader><TableRow><TableHead>Phương án</TableHead><TableHead>Chờ</TableHead><TableHead>Start trung vị / P90</TableHead><TableHead>Tổng trung vị / P90</TableHead><TableHead>Bằng chứng</TableHead></TableRow></TableHeader><TableBody>
        {data.baseline && row(data.baseline, "Gửi ngay (baseline)", "baseline")}
        {data.candidates.map((c, i) => row(c, c.hour_start === null ? "Chưa rõ giờ" : `${String(c.hour_start).padStart(2, "0")}:00`, String(i)))}
        {data.recommendation && row(data.recommendation, "Khuyến nghị", "recommendation")}
      </TableBody></Table>
      {!data.baseline && <p className="text-xs text-muted-foreground">Thiếu baseline gửi ngay; chưa thể so sánh đầy đủ.</p>}
    </CardContent></Card>}
  </section>;
}
