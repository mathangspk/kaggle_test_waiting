import { useEffect, useState } from "react";
import { useTimezone } from "@/hooks/use-timezone";
import { formatDateTime, freshnessAge } from "@/lib/format";
import type { AsyncState } from "@/types";

export function DataFreshness({ state, timestamp, staleAfter = 45, kind = "collector" }: {
  state: AsyncState<unknown>;
  timestamp?: string | null;
  staleAfter?: number;
  kind?: "collector" | "roadmap";
}) {
  const { mode } = useTimezone();
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  const snapshot = kind === "roadmap";
  const age = freshnessAge(snapshot ? timestamp : timestamp ?? state.lastLoaded);
  const stale = age !== null && age > staleAfter;
  const warning = Boolean(state.error) || stale;
  const label = state.data === null ? "Chưa có ảnh chụp"
    : snapshot ? age === null ? "Chưa rõ tuổi ảnh chụp lộ trình"
      : stale ? "Ảnh chụp lộ trình cũ · đồng bộ đã quá 24 giờ" : "Ảnh chụp lộ trình · đồng bộ trong 24 giờ"
    : warning ? "Dữ liệu cũ · đang giữ ảnh chụp gần nhất" : "Ảnh chụp đã tải";
  return (
    <p role="status" className="mb-4 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
      <span className={warning ? "font-medium text-warning" : undefined}>
        {label}
      </span>
      <span>Tải thành công: {formatDateTime(state.lastLoaded, mode)}</span>
      <span>{snapshot ? "Đồng bộ ảnh chụp (bản triển khai)" : "Mốc backend"}: {formatDateTime(timestamp, mode)}</span>
      {snapshot ? <span>Ngưỡng tuổi ảnh chụp: 24 giờ · dữ liệu tĩnh đóng gói khi triển khai</span> : null}
      {snapshot && state.error && state.data !== null ? <span className="text-warning">Lỗi tải · giữ ảnh chụp lộ trình gần nhất</span> : null}
      {state.loading ? <span>Đang làm mới…</span> : null}
    </p>
  );
}
