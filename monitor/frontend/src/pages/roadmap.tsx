import { useMemo } from "react";
import { ArrowRight, ExternalLink, GitBranch, Route as RouteIcon, ShieldAlert } from "lucide-react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useTimezone } from "@/hooks/use-timezone";
import { PageHeader, SectionTitle } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice, ToneBadge } from "@/components/shared/status-badge";
import { LoadingBlock } from "@/components/shared/data-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { formatDateTime, type Tone } from "@/lib/format";
import type { RoadmapIssue, RoadmapSnapshot } from "@/types";
import { DataFreshness } from "@/components/shared/data-freshness";

const REASON_TEXT: Record<string, string> = {
  roadmap_json_not_packaged: "Chưa có roadmap.json được đóng gói cùng backend.",
  roadmap_json_unreadable: "roadmap.json không đọc được hoặc sai định dạng JSON.",
};

function issueStateTone(state: string | undefined): Tone {
  const normalized = (state ?? "").toLowerCase();
  if (normalized === "open") return "success";
  if (normalized === "closed") return "neutral";
  return "warning";
}

function issueKey(issue: RoadmapIssue): string {
  return String(issue.number);
}

export default function RoadmapPage() {
  const { roadmap } = useMonitorData();
  const { mode } = useTimezone();
  const data = roadmap.data;
  const snapshot: RoadmapSnapshot | null | undefined = data?.snapshot ?? (data?.issues ? data : null);
  const issues = snapshot?.issues ?? [];
  const dependencies = snapshot?.dependencies ?? [];
  const blocked = snapshot?.blocked ?? [];

  const byNumber = useMemo(() => {
    const map = new Map<string, RoadmapIssue>();
    issues.forEach((issue) => map.set(issueKey(issue), issue));
    return map;
  }, [issues]);

  const summary = useMemo(() => {
    const open = issues.filter((issue) => (issue.state ?? "").toLowerCase() === "open").length;
    return {
      total: issues.length,
      open,
       closed: issues.filter((issue) => (issue.state ?? "").toLowerCase() === "closed").length,
      blocked: blocked.length,
      dependencies: dependencies.length,
    };
  }, [issues, blocked, dependencies]);

  return (
    <div>
      <PageHeader
        title="Lộ trình"
        description="Issue, phụ thuộc và các mục đang bị chặn từ ảnh chụp roadmap.json đóng gói khi triển khai. Tải lại trang không đồng bộ ảnh chụp với GitHub."
        actions={
          snapshot?.repo ? (
            <Badge variant="outline" className="gap-1">
              <GitBranch className="size-3.5" />
              {snapshot.repo}
            </Badge>
          ) : undefined
        }
      />

      <OfflineNotice error={roadmap.error} hasData={Boolean(data)} />
      <DataFreshness state={roadmap} kind="roadmap" staleAfter={24 * 60 * 60} timestamp={snapshot?.synced_at ?? snapshot?.syncedAt} />

      {!data && roadmap.loading ? (
        <LoadingBlock className="h-64 w-full" />
      ) : !data ? (
        <NoData message="Chưa tải được dữ liệu lộ trình." />
      ) : data.available === false || !snapshot ? (
        <Alert variant="warning">
          <ShieldAlert className="text-warning" />
          <AlertTitle>Chưa có ảnh chụp lộ trình</AlertTitle>
          <AlertDescription>
            {REASON_TEXT[data.reason ?? ""] ?? data.reason ?? "Backend không cung cấp roadmap.json."}
            {data.source ? ` Nguồn: ${data.source}.` : ""}
          </AlertDescription>
        </Alert>
      ) : (
        <div className="space-y-8">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Tổng issue</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold tabular-nums">{summary.total}</CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Đang mở</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold tabular-nums text-success">{summary.open}</CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Đã đóng</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold tabular-nums">{summary.closed}</CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Bị chặn</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold tabular-nums text-warning">
                {summary.blocked}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Phụ thuộc</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold tabular-nums">{summary.dependencies}</CardContent>
            </Card>
          </div>

          <p className="text-xs text-muted-foreground">
            Ảnh chụp đồng bộ lúc: {formatDateTime(snapshot.synced_at ?? snapshot.syncedAt, mode)} · theo bản triển khai
          </p>

          {blocked.length > 0 ? (
            <section>
              <SectionTitle
                aside={<span className="text-xs text-muted-foreground">{blocked.length} mục</span>}
              >
                Đang bị chặn
              </SectionTitle>
              <div className="flex flex-wrap gap-2">
                {blocked.map((number) =>
                  byNumber.has(String(number)) ? (
                    <ToneBadge key={String(number)} tone="warning" value={String(number)}>
                      #{number} · {byNumber.get(String(number))?.title ?? ""}
                    </ToneBadge>
                  ) : (
                    <ToneBadge key={String(number)} tone="warning" value={String(number)}>
                      #{number}
                    </ToneBadge>
                  ),
                )}
              </div>
            </section>
          ) : null}

          <section>
            <SectionTitle aside={<span className="text-xs text-muted-foreground">{issues.length} issue</span>}>
              Danh sách issue
            </SectionTitle>
            {issues.length === 0 ? (
              <NoData message="Không có issue trong ảnh chụp lộ trình." />
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {issues.map((issue) => {
                  const blockedIssue = blocked.some((number) => String(number) === issueKey(issue));
                  return (
                    <Card key={issueKey(issue)}>
                      <CardHeader className="space-y-2">
                        <div className="flex items-start justify-between gap-2">
                          <CardTitle className="flex items-start gap-2 text-base leading-snug">
                            <span className="text-muted-foreground">#{issue.number}</span>
                            <span>{issue.title ?? "(không có tiêu đề)"}</span>
                          </CardTitle>
                          <div className="flex shrink-0 flex-col items-end gap-1">
                            <ToneBadge tone={issueStateTone(issue.state)} value={issue.state}>
                              {issue.state ?? "không rõ"}
                            </ToneBadge>
                            {blockedIssue ? <Badge variant="warning">Bị chặn</Badge> : null}
                          </div>
                        </div>
                        {issue.labels && Array.isArray(issue.labels) && issue.labels.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {issue.labels.map((label, index) => (
                              <Badge key={index} variant="secondary" className="text-[10px]">
                                {typeof label === "string"
                                  ? label
                                  : typeof label === "object" && label && "name" in label
                                    ? String((label as { name?: unknown }).name ?? "")
                                    : ""}
                              </Badge>
                            ))}
                          </div>
                        ) : null}
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {issue.body ? (
                          <p className="line-clamp-3 text-sm text-muted-foreground">{issue.body}</p>
                        ) : (
                          <p className="text-sm text-muted-foreground">Không có mô tả.</p>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <Dialog>
                            <DialogTrigger asChild>
                              <Button variant="outline" size="sm">
                                Xem mô tả
                              </Button>
                            </DialogTrigger>
                            <DialogContent className="max-h-[85vh] overflow-y-auto nice-scroll">
                              <DialogHeader>
                                <DialogTitle>
                                  #{issue.number} · {issue.title ?? "(không có tiêu đề)"}
                                </DialogTitle>
                                <DialogDescription>
                                  Cập nhật: {formatDateTime(issue.updatedAt ?? issue.updated_at, mode)}
                                </DialogDescription>
                              </DialogHeader>
                              <pre className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
                                {issue.body ?? "Không có mô tả."}
                              </pre>
                            </DialogContent>
                          </Dialog>
                          {issue.url ? (
                            <Button asChild variant="link" size="sm">
                              <a href={issue.url} target="_blank" rel="noreferrer noopener">
                                Mở trên GitHub
                                <ExternalLink />
                              </a>
                            </Button>
                          ) : null}
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </section>

          <section>
            <SectionTitle
              aside={<span className="text-xs text-muted-foreground">{dependencies.length} liên kết</span>}
            >
              Phụ thuộc
            </SectionTitle>
            {dependencies.length === 0 ? (
              <NoData message="Không có phụ thuộc trong ảnh chụp lộ trình." />
            ) : (
              <Card>
                <CardContent className="p-0">
                  <ul className="divide-y divide-border">
                    {dependencies.map((dependency, index) => {
                      const from = byNumber.get(String(dependency.from));
                      const to = byNumber.get(String(dependency.to));
                      return (
                        <li key={index} className="flex flex-wrap items-center gap-2 px-5 py-3 text-sm">
                          <span className="inline-flex items-center gap-1">
                            <RouteIcon className="size-3.5 text-muted-foreground" />#{dependency.from}
                            {from?.title ? <span className="text-muted-foreground"> {from.title}</span> : null}
                          </span>
                          <ArrowRight className="size-4 text-muted-foreground" />
                          <span className="inline-flex items-center gap-1">
                            #{dependency.to}
                            {to?.title ? <span className="text-muted-foreground"> {to.title}</span> : null}
                          </span>
                          {dependency.label ? (
                            <Badge variant="secondary" className="ml-auto">
                              {dependency.label}
                            </Badge>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </CardContent>
              </Card>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
