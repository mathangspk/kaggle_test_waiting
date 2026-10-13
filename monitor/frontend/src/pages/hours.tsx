import { useState } from "react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useAnalysis } from "@/hooks/use-analysis";
import type { AnalysisFilters } from "@/lib/analysis";
import { AnalysisControls, AnalysisWindows, DecisionPanel } from "@/components/shared/analysis-panels";
import { PageHeader } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice } from "@/components/shared/status-badge";
import { DataFreshness } from "@/components/shared/data-freshness";
import { formatCount, formatSeconds } from "@/lib/format";

export default function HoursPage() {
  const { summary } = useMonitorData();
  const [filters, setFilters] = useState<AnalysisFilters>({ source: "probe", account: "", day_type: "all" });
  const analysis = useAnalysis(filters);
  const accounts = [...new Map([...(summary.data?.accounts ?? []), ...(analysis.data?.accounts ?? [])].map((a) => [a.id, a])).values()];
  if (filters.account && !accounts.some((a) => a.id === filters.account)) accounts.push({ id: filters.account, name: filters.account });
  return <div className="space-y-6">
    <PageHeader title="Khung giờ" description="Xác suất cấp phát theo mẫu tài khoản · gửi → bắt đầu trong cửa sổ 2 giờ (Asia/Ho_Chi_Minh), không phải GPU rảnh toàn hệ thống." />
    <AnalysisControls filters={filters} onChange={setFilters} accounts={accounts} />
    <OfflineNotice error={analysis.error} hasData={Boolean(analysis.data)} />
    <DataFreshness state={analysis} timestamp={analysis.data?.generated_at} />
    {analysis.data ? <AnalysisWindows data={analysis.data} source={filters.source} /> : <NoData message={analysis.loading ? "Đang tải phân tích…" : `Chưa có dữ liệu ${filters.source} cho nhóm đã chọn.`} />}
    <DecisionPanel filters={filters} />
    <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm font-medium">Queue · chẩn đoán phụ (probe, tất cả tài khoản/ngày)</summary>
      <OfflineNotice error={summary.error} hasData={Boolean(summary.data)} />
      <DataFreshness state={summary} timestamp={summary.data?.meta.data_updated_at ?? summary.data?.generated_at} />
      <p className="my-3 text-xs text-muted-foreground">Dữ liệu /api/summary độc lập với bộ lọc trên. Queue là cận trên ước lượng, không dùng để xếp hạng hoặc quyết định gửi.</p>
      <div className="grid gap-2 sm:grid-cols-4">{(summary.data?.hours ?? []).map((h) => <div key={h.hour} className="rounded-lg border p-3 text-xs">
        <p>{String(h.hour).padStart(2, "0")}:00</p><p>{formatCount(h.sample_count)} mẫu gửi · {formatCount(h.queue_sample_count)} mẫu queue</p>
        <p>{h.queue_sample_count > 0 && h.median_queue_seconds !== null ? formatSeconds(h.median_queue_seconds) : "Queue chưa rõ"}</p>
      </div>)}</div>
      {!summary.data && <NoData message="Chưa có dữ liệu queue." />}
    </details>
  </div>;
}
