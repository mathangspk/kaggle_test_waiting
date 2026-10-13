import { Link } from "react-router-dom";
import { useState } from "react";
import { useAnalysis } from "@/hooks/use-analysis";
import type { AnalysisFilters } from "@/lib/analysis";
import { AnalysisControls, AnalysisWindows } from "@/components/shared/analysis-panels";
import { Activity, CalendarClock, Gauge, ListChecks, ShieldAlert, TimerReset } from "lucide-react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useTimezone } from "@/hooks/use-timezone";
import { KeyValue, PageHeader, SectionTitle, StatCard } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice, StatusBadge } from "@/components/shared/status-badge";
import { LoadingCards } from "@/components/shared/data-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  boundsText,
  formatCount,
  formatDateTime,
  formatSeconds,
  parseDate,
  secondsTone,
  type Tone,
} from "@/lib/format";
import type { Run } from "@/types";
import { cn } from "@/lib/utils";
import { DataFreshness } from "@/components/shared/data-freshness";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-foreground",
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
};

function sortRuns(runs: Run[]): Run[] {
  return [...runs].sort((a, b) => {
    const ta = (parseDate(a.submitted_at ?? a.scheduled_at)?.getTime() ?? -Infinity);
    const tb = (parseDate(b.submitted_at ?? b.scheduled_at)?.getTime() ?? -Infinity);
    return tb - ta;
  });
}

export default function OverviewPage() {
  const { summary } = useMonitorData();
  const [filters, setFilters] = useState<AnalysisFilters>({ source: "probe", account: "", day_type: "all" });
  const analysis = useAnalysis(filters);
  const { mode } = useTimezone();
  const data = summary.data;
  const meta = data?.meta;
  const hours = data?.hours ?? [];
  const accounts = data?.accounts ?? [];
  const runs = data?.runs ?? [];
  const statistics = data?.statistics;

  const sum = (key: "successful_t4x2") =>
    hours.length > 0 && hours.every((hour) => typeof hour[key] === "number")
      ? hours.reduce((total, hour) => total + hour[key], 0) : null;
  const successful = sum("successful_t4x2");
  const activeAccounts = accounts.length > 0 && accounts.every((account) => account.active_runs != null)
    ? accounts.filter((account) => account.active_runs).length : null;
  const recent = sortRuns(runs).slice(0, 6);

  return (
    <div>
      <PageHeader
        title="Tổng quan"
         description="Tình trạng bộ thu thập, tài khoản, khung giờ đáng cân nhắc và các lượt chạy gần nhất."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to="/runs">
              <ListChecks />
              Xem tất cả lượt chạy
            </Link>
          </Button>
        }
      />

      <OfflineNotice error={summary.error} hasData={Boolean(data)} />
      <DataFreshness state={summary} timestamp={meta?.data_updated_at ?? data?.generated_at} staleAfter={meta?.stale_after_seconds} />

      {!data && summary.loading ? (
        <LoadingCards />
      ) : !data ? (
        <NoData message="Chưa có dữ liệu tổng quan. Sẽ tự thử lại sau 15 giây." />
      ) : (
        <div className="space-y-8">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <StatCard
              label="Tổng lượt chạy (không gồm đã lên lịch)"
              value={formatCount(statistics?.total_runs)}
              hint={`${formatCount(statistics?.sample_count)} lượt thử gửi`}
              icon={<ListChecks className="size-4" />}
            />
            <StatCard
              label="Tỷ lệ cấp phát nhanh"
              value="Theo cửa sổ 2 giờ"
              hint="Tỷ lệ backend theo trọng số bằng nhau giữa tài khoản; thiếu độ phủ giữ điểm chưa rõ. Không suy ra từ số đếm gộp."
              icon={<Gauge className="size-4" />}
            />
            <StatCard
              label="Ngày phủ dữ liệu"
              value={formatCount(statistics?.day_count)}
              hint="Mỗi cửa sổ cần ≥7 ngày và ≥20 resolved; độ phủ do backend xác nhận"
              icon={<CalendarClock className="size-4" />}
            />
            <StatCard
              label="Lượt T4×2 thành công"
              value={formatCount(successful)}
              hint="Bằng chứng hai thiết bị T4"
              tone="success"
              icon={<ShieldAlert className="size-4" />}
            />
            <StatCard
              label="Đã bỏ qua"
              value={formatCount(statistics?.skipped)}
              hint="Lỡ khung, chưa sẵn sàng hoặc đang bận"
              icon={<TimerReset className="size-4" />}
            />
            <StatCard
              label="Tài khoản đang hoạt động"
              value={`${formatCount(activeAccounts)} / ${formatCount(accounts.length)}`}
              hint={meta?.api_in_flight === true ? "Có lệnh gọi API đang chạy" : meta?.api_in_flight === false ? "Không có lệnh gọi API đang chạy" : "Chưa rõ hoạt động API"}
              tone={activeAccounts !== null && activeAccounts > 0 ? "warning" : "neutral"}
              icon={<Activity className="size-4" />}
            />
          </div>

          <section>
            <SectionTitle
              aside={
                <span className="text-xs text-muted-foreground">
                  {formatCount(accounts.length)} tài khoản theo dõi
                </span>
              }
            >
              Tổng quan tài khoản
            </SectionTitle>
            {accounts.length === 0 ? (
              <NoData message="Chưa có dữ liệu tài khoản trong phản hồi." />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                {accounts.map((account) => (
                  <Card key={account.id}>
                    <CardHeader className="flex flex-row items-start justify-between space-y-0">
                      <CardTitle className="truncate text-base" title={account.name}>
                        {account.name}
                      </CardTitle>
                      <StatusBadge status={account.status ?? account.state} />
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div>
                        <div className="text-2xl font-semibold tabular-nums">
                          {formatSeconds(account.queue_estimate_seconds)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                           Queue · chẩn đoán phụ, cận trên của mẫu gửi gần nhất
                        </div>
                      </div>
                      <dl>
                        <KeyValue
                          term="Khoảng bất định"
                          value={boundsText(account.queue_lower_seconds, account.queue_upper_seconds)}
                        />
                        <KeyValue term="Số mẫu" value={formatCount(account.sample_count)} />
                        <KeyValue term="Ngày phủ" value={formatCount(account.day_count)} />
                        <KeyValue
                          term="Đang hoạt động"
                           value={account.active_runs == null ? "Chưa rõ" : account.active_runs > 0 ? "Có" : "Không"}
                        />
                        <KeyValue
                          term="Lượt kế tiếp"
                          value={formatDateTime(account.next_probe_utc, mode)}
                        />
                        <KeyValue
                          term="Cập nhật"
                          value={formatDateTime(account.updated_at, mode)}
                        />
                      </dl>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </section>

          <section>
            <AnalysisControls filters={filters} onChange={setFilters} accounts={accounts} />
            <OfflineNotice error={analysis.error} hasData={Boolean(analysis.data)} />
            <DataFreshness state={analysis} timestamp={analysis.data?.generated_at} />
            {analysis.data ? <AnalysisWindows data={analysis.data} source={filters.source} /> : <NoData message={`Chưa có phân tích ${filters.source} cho nhóm đã chọn.`} />}
            <Button asChild variant="outline" className="mt-4"><Link to="/hours">So sánh gửi ngay / lên lịch</Link></Button>
          </section>

          <section>
            <SectionTitle
              aside={
                <Button asChild variant="ghost" size="sm">
                  <Link to="/runs">Chi tiết</Link>
                </Button>
              }
            >
              Lượt chạy gần nhất
            </SectionTitle>
            <Card>
              <CardContent className="p-0">
                {recent.length === 0 ? (
                  <div className="p-5">
                    <NoData message="Chưa có lượt chạy nào." />
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Lượt chạy</TableHead>
                        <TableHead>Tài khoản</TableHead>
                        <TableHead>Trạng thái</TableHead>
                        <TableHead>Gửi lúc</TableHead>
                        <TableHead className="text-right">Queue · cận trên</TableHead>
                        <TableHead className="text-right">Gửi → bắt đầu</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recent.map((run) => (
                        <TableRow key={run.id}>
                          <TableCell className="max-w-[10rem] truncate font-mono text-xs" title={run.id}>
                            {run.id}
                          </TableCell>
                          <TableCell>{run.account_name ?? run.account}</TableCell>
                          <TableCell>
                            <StatusBadge status={run.status ?? run.state} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {formatDateTime(run.submitted_at, mode)}
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right tabular-nums",
                              TONE_TEXT[secondsTone(run.queue_estimate_seconds)],
                            )}
                          >
                            {formatSeconds(run.queue_estimate_seconds)}
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right tabular-nums",
                              TONE_TEXT[secondsTone(run.submit_to_start_seconds)],
                            )}
                          >
                            {formatSeconds(run.submit_to_start_seconds)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </section>
        </div>
      )}
    </div>
  );
}
