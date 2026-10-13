import { Activity, Cpu, HeartPulse, ServerCog } from "lucide-react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useTimezone } from "@/hooks/use-timezone";
import { KeyValue, PageHeader, SectionTitle, StatCard } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice, ToneBadge } from "@/components/shared/status-badge";
import { LoadingCards, LoadingTable } from "@/components/shared/data-state";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatBoolean, formatCount, formatDateTime } from "@/lib/format";
import { DataFreshness } from "@/components/shared/data-freshness";

function statusValue(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

export default function OperationsPage() {
  const { health, summary } = useMonitorData();
  const { mode } = useTimezone();
  const data = summary.data;
  const healthData = health.data;
  const apiCalls = data?.api_calls ?? [];
  const definitions = data?.definitions ?? {};
  const meta = data?.meta;

  const progress = healthData?.progress;
  const progressTone =
    progress === "ok" ? "success" : progress === "error" || progress === "fatal" ? "danger" : "neutral";
  const apiTable = apiCalls.slice(0, 50);

  return (
    <div>
      <PageHeader
        title="Vận hành"
        description="Sức khỏe worker, cấu hình, mốc hoạt động API và định nghĩa chỉ số. Trang read-only: không có hành động ghi nào."
        actions={
          <Badge variant="outline" className="gap-1">
            <Activity className="size-3.5" />
            /healthz · /api/summary
          </Badge>
        }
      />

      <OfflineNotice error={health.error ? `/healthz: ${health.error}` : null} hasData={Boolean(healthData)} />
      <DataFreshness state={health} timestamp={healthData?.heartbeat_utc} />
      <OfflineNotice error={summary.error ? `/api/summary: ${summary.error}` : null} hasData={Boolean(data)} />
      <DataFreshness state={summary} timestamp={meta?.data_updated_at ?? data?.generated_at} staleAfter={meta?.stale_after_seconds} />

      {!data && !healthData && (summary.loading || health.loading) ? (
        <div className="space-y-6">
          <LoadingCards />
          <LoadingTable />
        </div>
      ) : !data && !healthData ? <NoData message="Chưa tải được dữ liệu vận hành. Sẽ thử lại sau 15 giây." /> : (
        <div className="space-y-8">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Sức khỏe worker"
              value={healthData?.ok === true ? "Khỏe" : healthData?.ok === false ? "Không khỏe" : "Chưa rõ"}
              hint={
                healthData?.worker_alive === false
                  ? "Thread worker đã dừng"
                  : healthData?.worker_alive === true
                    ? "Thread worker còn sống"
                    : "Chưa xác định"
              }
              tone={healthData?.ok === true ? "success" : healthData?.ok === false ? "danger" : "neutral"}
              icon={<HeartPulse className="size-4" />}
            />
            <StatCard
              label="Sẵn sàng chạy probe"
              value={healthData?.ready === true ? "Có" : healthData?.ready === false ? "Chưa" : "Chưa rõ"}
              hint="Cần SDK sẵn sàng và có tài khoản"
              tone={healthData?.ready === true ? "success" : healthData?.ready === false ? "warning" : "neutral"}
              icon={<ServerCog className="size-4" />}
            />
            <StatCard
              label="Probe tự động"
              value={healthData?.auto_probes === true ? "Bật" : healthData?.auto_probes === false ? "Tắt" : "Chưa rõ"}
              hint="AUTO_PROBES từ backend"
              tone={healthData?.auto_probes === true ? "warning" : "neutral"}
              icon={<Cpu className="size-4" />}
            />
            <StatCard
              label="Tiến trình tick"
              value={statusValue(progress) ?? "Chưa rõ"}
              hint={healthData?.fatal_reason ? `Dừng: ${healthData.fatal_reason}` : undefined}
              tone={progressTone}
              icon={<Activity className="size-4" />}
            />
          </div>

          <section>
            <SectionTitle>Health chi tiết</SectionTitle>
            <Card>
              <CardContent className="pt-5">
                {!healthData ? (
                  <NoData message="Chưa tải được /healthz." />
                ) : (
                  <dl>
                    <KeyValue
                      term="Schema / OK"
                      value={`v${healthData.schema_version ?? "—"} · ${healthData.ok ? "true" : "false"}`}
                    />
                    <KeyValue
                      term="Progress / Watchdog / Fatal"
                      value={[progress, healthData.watchdog ?? "không", healthData.fatal_reason ?? "không"]
                        .filter(Boolean)
                        .join(" / ")}
                    />
                    <KeyValue
                      term="Cấu hình"
                      value={
                         healthData.config_ok === true
                           ? "hợp lệ"
                           : healthData.config_ok === false ? `lỗi: ${healthData.configuration_error ?? "chưa rõ"}` : "Chưa rõ"
                      }
                    />
                    <KeyValue term="SDK sẵn sàng" value={formatBoolean(healthData.sdk_ready)} />
                    <KeyValue
                      term="Readiness"
                      value={
                        healthData.readiness?.ok === true
                          ? `ok · SDK ${healthData.readiness.sdk_version ?? "?"}`
                          : healthData.readiness?.error ?? "chưa rõ"
                      }
                    />
                    <KeyValue
                      term="Thử readiness"
                      value={formatCount(healthData.readiness_attempts)}
                    />
                    <KeyValue term="API đang chạy" value={formatBoolean(healthData.api_in_flight)} />
                    <KeyValue term="Heartbeat" value={formatDateTime(healthData.heartbeat_utc, mode)} />
                    <KeyValue term="Tick bắt đầu" value={formatDateTime(healthData.tick_started_utc, mode)} />
                    <KeyValue term="Tick kết thúc" value={formatDateTime(healthData.tick_finished_utc, mode)} />
                    <KeyValue
                      term="Lệnh API gần nhất"
                      value={formatDateTime(healthData.last_api_attempt_utc, mode)}
                    />
                    <KeyValue
                      term="Kết quả API gần nhất"
                      value={formatDateTime(healthData.last_api_result_utc, mode)}
                    />
                    {healthData.worker_error ? (
                      <KeyValue term="Lỗi worker" value={healthData.worker_error} />
                    ) : null}
                  </dl>
                )}
              </CardContent>
            </Card>
          </section>

          <section>
            <SectionTitle>Metadata từ backend</SectionTitle>
            <Card>
              <CardContent className="pt-5">
                <dl>
                  <KeyValue term="Độ mới dữ liệu" value={formatDateTime(meta?.data_updated_at, mode)} />
                  <KeyValue term="Bằng chứng cập nhật" value={formatDateTime(meta?.evidence_updated_at, mode)} />
                  <KeyValue term="Mẫu / Ngày" value={`${formatCount(meta?.sample_count)} / ${formatCount(meta?.day_count)}`} />
                  <KeyValue
                    term="Hủy từ xa"
                    value={formatBoolean(meta?.cancellation_supported)}
                  />
                  <KeyValue
                    term="Thước đo 5 phút"
                    value={meta?.success_5m_metric ?? "—"}
                  />
                </dl>
              </CardContent>
            </Card>
          </section>

          <section>
            <SectionTitle
              aside={<span className="text-xs text-muted-foreground">{apiCalls.length} trong lịch sử (tối đa 200)</span>}
            >
              Lệnh gọi API gần đây
            </SectionTitle>
            <Card>
              <CardContent className="p-0">
                {apiTable.length === 0 ? (
                  <div className="p-5">
                    <NoData message="Chưa có lệnh gọi API nào được ghi nhận." />
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Thời điểm</TableHead>
                        <TableHead>Thao tác</TableHead>
                        <TableHead>Lượt chạy</TableHead>
                        <TableHead className="text-right">Độ trễ</TableHead>
                        <TableHead className="text-right">HTTP</TableHead>
                        <TableHead>Kết quả</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {apiTable.map((call) => (
                        <TableRow key={call.id ?? call.at}>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {formatDateTime(call.at, mode)}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="font-mono text-[10px]">
                              {call.operation ?? "—"}
                            </Badge>
                          </TableCell>
                          <TableCell className="max-w-[9rem] truncate font-mono text-xs" title={call.run_id}>
                            {call.run_id ?? "—"}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {call.latency_ms != null ? `${formatCount(Math.round(call.latency_ms))} ms` : "—"}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {call.http_status != null ? call.http_status : "—"}
                          </TableCell>
                          <TableCell>
                             {call.ok === 1 ? (
                              <ToneBadge tone="success">ok</ToneBadge>
                            ) : (
                              <ToneBadge tone={call.error ? "danger" : "neutral"} value={call.error ?? undefined}>
                                 {call.error ?? (call.ok === 0 ? "lỗi" : "Chưa rõ")}
                              </ToneBadge>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </section>

          <section>
            <SectionTitle>Định nghĩa chỉ số</SectionTitle>
            <Card>
              <CardContent className="space-y-3 pt-5 text-sm text-muted-foreground">
                {(Object.entries(definitions) as [string, string][]).map(([key, value]) => (
                  <div key={key}>
                    <span className="font-medium text-foreground">{key}: </span>
                    {value}
                  </div>
                ))}
              </CardContent>
            </Card>
          </section>

          <p className="text-xs text-muted-foreground">
            Kết quả là dữ liệu vận hành thực từ backend. Mốc thời gian hiển thị theo múi giờ bạn chọn; nhóm giờ trong
            Khung giờ dùng múi giờ backend (Asia/Ho_Chi_Minh).
          </p>
        </div>
      )}
    </div>
  );
}
