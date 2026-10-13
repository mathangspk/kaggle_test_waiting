import { useMemo, useState } from "react";
import { Filter, ListChecks, Search } from "lucide-react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useTimezone } from "@/hooks/use-timezone";
import { PageHeader, KeyValue, SectionTitle } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice, StatusBadge, ToneBadge } from "@/components/shared/status-badge";
import { LoadingTable } from "@/components/shared/data-state";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  boundsText,
  formatDateTime,
  formatSeconds,
  parseDate,
  secondsTone,
  statusMeta,
  type Tone,
} from "@/lib/format";
import type { Run } from "@/types";
import { cn } from "@/lib/utils";
import { DataFreshness } from "@/components/shared/data-freshness";

const ACTIVE_FALLBACK = ["pushing", "ambiguous", "submitted", "queued", "running", "timed_out", "collecting"];
const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-muted-foreground",
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
};

function runNotes(run: Run): string[] {
  const notes: string[] = [];
  if (run.t4x2 === true) notes.push("T4×2 xác nhận");
  else if (run.t4x2 === false) notes.push("Không xác nhận T4×2");
  else notes.push("GPU chưa rõ");
  if (run.observation_timed_out) notes.push("Hết thời gian quan sát · không phải hủy");
  if (run.over_600_observation) notes.push(">600 giây chưa quan sát bắt đầu · queue chưa chắc");
  if (run.unacceptable) notes.push("Cận dưới queue >600 giây");
  if (run.submit_to_start_unacceptable) notes.push("Gửi → bắt đầu >600 giây");
  if (run.skip_reason) notes.push(`Bỏ qua: ${run.skip_reason}`);
  if (run.error) notes.push(`Lỗi: ${run.error}`);
  if (run.gpu_names && run.gpu_names.length > 0) notes.push(run.gpu_names.join(", "));
  return notes;
}

function RunDetails({ run }: { run: Run }) {
  const { mode } = useTimezone();
  const state = statusMeta(run.status ?? run.state);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={run.status ?? run.state} />
        <Badge variant="outline" className="font-mono text-xs">
          {run.id}
        </Badge>
        <span className="text-xs text-muted-foreground">{state.raw ?? ""}</span>
      </div>
      <dl>
        <KeyValue term="Tài khoản" value={run.account_name ?? run.account} />
        <KeyValue term="Phiên bản" value={run.version ?? "—"} />
        <KeyValue term="Lên lịch" value={formatDateTime(run.scheduled_at, mode)} />
        <KeyValue term="Gửi lúc" value={formatDateTime(run.submitted_at, mode)} />
        <KeyValue term="Ghi nhận gửi" value={formatDateTime(run.submission_ack_at, mode)} />
        <KeyValue term="Kết thúc gửi" value={formatDateTime(run.push_end_at, mode)} />
        <KeyValue term="Bắt đầu lúc" value={formatDateTime(run.started_at, mode)} />
        <KeyValue term="Hoàn tất lúc" value={formatDateTime(run.completed_at, mode)} />
        <KeyValue term="Quan sát kết thúc" value={formatDateTime(run.terminal_observed_at, mode)} />
        <KeyValue term="Đối soát lúc" value={formatDateTime(run.reconciled_at_utc, mode)} />
      </dl>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border p-3">
          <div className="text-xs text-muted-foreground">Queue · cận trên ước lượng</div>
          <div className={cn("text-lg font-semibold tabular-nums", TONE_TEXT[secondsTone(run.queue_estimate_seconds)])}>
            {formatSeconds(run.queue_estimate_seconds)}
          </div>
          <div className="text-xs text-muted-foreground">
            {boundsText(run.queue_lower_seconds, run.queue_upper_seconds)}
          </div>
        </div>
        <div className="rounded-lg border border-border p-3">
          <div className="text-xs text-muted-foreground">Gửi → bắt đầu</div>
          <div
            className={cn("text-lg font-semibold tabular-nums", TONE_TEXT[secondsTone(run.submit_to_start_seconds)])}
          >
            {formatSeconds(run.submit_to_start_seconds)}
          </div>
          <div className="text-xs text-muted-foreground">
            {boundsText(run.submit_to_start_lower_seconds, run.submit_to_start_upper_seconds)}
          </div>
        </div>
      </div>
      <div className="space-y-1 text-sm">
        {runNotes(run).map((note) => (
          <div key={note} className="text-muted-foreground">
            {note}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function RunsPage() {
  const { summary, logic } = useMonitorData();
  const { mode } = useTimezone();
  const data = summary.data;
  const runs = data?.runs ?? [];
  const accounts = data?.accounts ?? [];
  const activeStates = useMemo(
    () => new Set(logic.data?.active_states ?? ACTIVE_FALLBACK),
    [logic.data?.active_states],
  );

  const [account, setAccount] = useState("all");
  const [state, setState] = useState("all");
  const [query, setQuery] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);

  const stateOptions = useMemo(
    () => Array.from(new Set(runs.map((run) => run.status ?? run.state).filter((value): value is string => Boolean(value)))).sort(),
    [runs],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return [...runs]
      .filter((run) => (account === "all" ? true : (run.account_id ?? run.account) === account))
      .filter((run) => (state === "all" ? true : (run.status ?? run.state) === state))
      .filter((run) => (activeOnly ? activeStates.has(run.status ?? run.state ?? "") : true))
      .filter((run) =>
        needle
          ? [run.id, run.account, run.account_name, run.name, run.ref]
              .filter(Boolean)
              .some((value) => String(value).toLowerCase().includes(needle))
          : true,
      )
      .sort((a, b) => {
        const ta = parseDate(a.submitted_at ?? a.scheduled_at)?.getTime() ?? -Infinity;
        const tb = parseDate(b.submitted_at ?? b.scheduled_at)?.getTime() ?? -Infinity;
        return tb - ta;
      });
  }, [runs, account, state, activeOnly, activeStates, query]);

  const filtersActive = account !== "all" || state !== "all" || query.trim() !== "" || activeOnly;

  return (
    <div>
      <PageHeader
        title="Lượt chạy"
         description="Danh sách lượt chạy đã ghi nhận. Chọn Chi tiết để xem bằng chứng và khoảng bất định."
        actions={
          <Badge variant="outline" className="gap-1">
            <ListChecks className="size-3.5" />
            {filtered.length} / {runs.length}
          </Badge>
        }
      />

      <OfflineNotice error={summary.error} hasData={Boolean(data)} />
      <DataFreshness state={summary} timestamp={data?.meta.data_updated_at ?? data?.generated_at} staleAfter={data?.meta.stale_after_seconds} />

      {!data && summary.loading ? (
        <LoadingTable />
      ) : !data ? (
        <NoData message="Chưa có dữ liệu lượt chạy." />
      ) : (
        <div className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3 pt-5">
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Filter className="size-3.5" />
                Bộ lọc
              </span>
              <Select value={account} onValueChange={setAccount}>
                <SelectTrigger className="w-[10rem]" aria-label="Lọc tài khoản">
                  <SelectValue placeholder="Tài khoản" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tất cả tài khoản</SelectItem>
                  {accounts.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={state} onValueChange={setState}>
                <SelectTrigger className="w-[10rem]" aria-label="Lọc trạng thái">
                  <SelectValue placeholder="Trạng thái" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Mọi trạng thái</SelectItem>
                  {stateOptions.map((value) => (
                    <SelectItem key={value} value={value}>
                      {statusMeta(value).label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="relative min-w-[12rem] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Tìm theo mã, tài khoản, ref…"
                  className="pl-8"
                  aria-label="Tìm lượt chạy"
                />
              </div>

              <label className="inline-flex items-center gap-2 text-sm">
                <Switch checked={activeOnly} onCheckedChange={setActiveOnly} aria-label="Chỉ lượt đang hoạt động" />
                Đang hoạt động
              </label>

              {filtersActive ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAccount("all");
                    setState("all");
                    setQuery("");
                    setActiveOnly(false);
                  }}
                >
                  Xóa lọc
                </Button>
              ) : null}
            </CardContent>
          </Card>

          <section>
            <SectionTitle
              aside={<span className="text-xs text-muted-foreground">Tối đa 200 lượt gần nhất</span>}
            >
              Kết quả
            </SectionTitle>
            <Card>
              <CardContent className="p-0">
                {filtered.length === 0 ? (
                  <div className="p-5">
                    <NoData message="Không có lượt chạy khớp bộ lọc." />
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Lượt chạy</TableHead>
                        <TableHead>Tài khoản</TableHead>
                        <TableHead>Trạng thái</TableHead>
                        <TableHead>Gửi lúc</TableHead>
                        <TableHead>Bắt đầu lúc</TableHead>
                        <TableHead className="text-right">Queue · cận trên</TableHead>
                        <TableHead className="text-right">Gửi → bắt đầu</TableHead>
                        <TableHead>Ghi chú</TableHead>
                        <TableHead className="w-16" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filtered.map((run) => (
                        <TableRow key={run.id}>
                          <TableCell className="max-w-[9rem] truncate font-mono text-xs" title={run.id}>
                            {run.id}
                          </TableCell>
                          <TableCell className="whitespace-nowrap">{run.account_name ?? run.account}</TableCell>
                          <TableCell>
                            <StatusBadge status={run.status ?? run.state} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {formatDateTime(run.submitted_at, mode)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {formatDateTime(run.started_at, mode)}
                          </TableCell>
                          <TableCell className="text-right">
                            <ToneBadge tone={secondsTone(run.queue_estimate_seconds)}>
                              {formatSeconds(run.queue_estimate_seconds)}
                            </ToneBadge>
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right text-xs tabular-nums",
                              TONE_TEXT[secondsTone(run.submit_to_start_seconds)],
                            )}
                          >
                            {formatSeconds(run.submit_to_start_seconds)}
                          </TableCell>
                          <TableCell className="max-w-[16rem] text-xs text-muted-foreground">
                            {runNotes(run).slice(0, 2).join(" · ")}
                          </TableCell>
                          <TableCell className="text-right">
                            <Dialog>
                              <DialogTrigger asChild>
                                <Button variant="ghost" size="sm">
                                  Chi tiết
                                </Button>
                              </DialogTrigger>
                              <DialogContent className="max-h-[85vh] overflow-y-auto nice-scroll">
                                <DialogHeader>
                                  <DialogTitle>Chi tiết lượt chạy</DialogTitle>
                                  <DialogDescription>
                                    Bằng chứng và khoảng bất định đã ghi nhận. Không suy diễn giá trị thiếu.
                                  </DialogDescription>
                                </DialogHeader>
                                <RunDetails run={run} />
                              </DialogContent>
                            </Dialog>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </section>

          <p className="text-xs text-muted-foreground">
            Hết thời gian quan sát không phải thất bại hay hủy: tài khoản vẫn có thể đang hoạt động. Không quan sát
            được queued hoặc vẫn đang queued thì chưa có cận trên queue.
          </p>
        </div>
      )}
    </div>
  );
}
