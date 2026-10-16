import type { AccountActivity } from "@/types";
import { KeyValue } from "@/components/shared/layout-bits";
import { StatusBadge } from "@/components/shared/status-badge";
import { formatDateTime, parseDate } from "@/lib/format";
import { useTimezone } from "@/hooks/use-timezone";

const labels: Record<string, string> = {
  running: "Đang chạy", pending: "Đang chờ", unresolved: "Chưa xác định",
  partial: "Độ phủ một phần", stale: "Bằng chứng cũ", unknown: "Chưa rõ",
  observed_idle: "Quan sát không có hoạt động",
};

function evidenceFreshness(at: string | null, ttl: number): string {
  const time = parseDate(at)?.getTime();
  if (time == null) return "unknown";
  return Date.now() >= time && Date.now() - time <= ttl * 1000 ? "fresh" : "stale";
}

export function activityStatus(activity?: AccountActivity, collectionError?: string | null): string {
  if (!activity) return "unknown";
  if (collectionError) return "unknown";
  const fresh = (at: string | null, ttl: number) => evidenceFreshness(at, ttl) === "fresh";
  if (activity.status === "observed_idle" && !activity.sources.some((s) =>
    s.coverage === "full" && !s.error && !s.auth_error && fresh(s.observed_at, s.stale_after_seconds))) return "stale";
  if (["running", "pending"].includes(activity.status) && !activity.observations.some((o) =>
    o.state === activity.status && fresh(o.observed_at, o.stale_after_seconds ?? activity.sources.find((s) => s.source === o.source)?.stale_after_seconds ?? 300))) return "stale";
  return activity.status;
}

export function AccountActivityEvidence({ activity, collectionError }: { activity?: AccountActivity; collectionError?: string | null }) {
  const { mode } = useTimezone();
  const status = activityStatus(activity, collectionError);
  const observations = activity?.observations ?? [];
  const visible = [...observations.filter((o) => o.state !== "terminal"),
    ...observations.filter((o) => o.state === "terminal")].slice(0, 10);
  return <div className="space-y-2 text-xs" aria-label="Account activity evidence">
    <p className="font-medium">{labels[status] ?? status}</p>
    <p>Độ phủ: {activity?.coverage ?? "none"} · không suy ra rảnh từ probe hoàn tất</p>
    <dl><KeyValue term="Quan sát hoạt động" value={formatDateTime(activity?.observed_at, mode)} /></dl>
    {activity?.sources.map((source) => <div key={source.source}>
      <p>{source.source} · {source.coverage} · {evidenceFreshness(source.observed_at, source.stale_after_seconds)}</p>
      <p>{formatDateTime(source.observed_at, mode)} · lần thử {formatDateTime(source.last_attempt_at, mode)}</p>
      {(source.error || source.auth_error) && <p className="text-warning">{source.auth_error ?? source.error} · giữ bằng chứng gần nhất</p>}
    </div>)}
    {observations.length > 10 && <p>Hiển thị {visible.length}/{observations.length} bằng chứng · ưu tiên chưa kết thúc; API giữ toàn bộ cửa sổ</p>}
    {visible.map((observation) => <div key={`${observation.source}:${observation.identity}`} className="border-t pt-2">
      <p className="break-all">{observation.identity} · {observation.run_type} · {observation.source}</p>
      <StatusBadge status={observation.state} /> {observation.provider_state ?? "provider chưa rõ"} · {observation.coverage} · {evidenceFreshness(observation.observed_at, observation.stale_after_seconds ?? activity?.sources.find((s) => s.source === observation.source)?.stale_after_seconds ?? 300)}
      <p>{formatDateTime(observation.observed_at, mode)}</p>
      <p>GPU yêu cầu: {observation.gpu_requested ?? "chưa rõ"} · thiết bị xác minh: {observation.gpu_verified_devices?.join(", ") || "chưa rõ"}</p>
    </div>)}
  </div>;
}
