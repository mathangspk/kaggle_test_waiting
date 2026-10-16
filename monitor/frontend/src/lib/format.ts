export type TimeZoneMode = "local" | "vn";

export const VN_TIMEZONE = "Asia/Ho_Chi_Minh";

export function hasOwn(value: unknown, key: string): boolean {
  return !!value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, key);
}

export function pick<T = unknown>(source: unknown, ...keys: string[]): T | null {
  if (!source || typeof source !== "object") return null;
  const record = source as Record<string, unknown>;
  for (const key of keys) {
    if (hasOwn(record, key)) {
      const value = record[key];
      if (value !== null && value !== undefined && value !== "") return value as T;
    }
  }
  return null;
}

/** Numeric value only when finite and non-negative; booleans/objects become null. */
export function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  if (typeof value === "object") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function asInteger(value: unknown): number | null {
  const parsed = asNumber(value);
  return parsed === null ? null : Math.trunc(parsed);
}

export function asText(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

export function parseDate(value: unknown): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : (value as string));
  return Number.isFinite(date.getTime()) ? date : null;
}

export function timeZoneName(mode: TimeZoneMode): string | undefined {
  return mode === "vn" ? VN_TIMEZONE : undefined;
}

/** Age in seconds of a timestamp, or null when unknown/absent. */
export function freshnessAge(value: unknown): number | null {
  const date = parseDate(value);
  if (!date) return null;
  return (Date.now() - date.getTime()) / 1000;
}

/** Clamp a possibly-negative UTC clock skew into a human sentence. */
export function freshnessText(value: unknown, staleAfterSeconds: number | null | undefined): string {
  const age = freshnessAge(value);
  if (age === null) return "Chưa xác định độ mới";
  if (age < -30) return "Mốc dữ liệu ở tương lai · kiểm tra đồng hồ";
  const stale = staleAfterSeconds != null && age > staleAfterSeconds;
  return `${staleAfterSeconds == null ? "Chưa rõ ngưỡng độ mới" : stale ? "Dữ liệu cũ" : "Dữ liệu mới"} · ${Math.max(0, Math.floor(age))} giây trước`;
}

export function formatDateTime(value: unknown, mode: TimeZoneMode = "vn"): string {
  const date = parseDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: timeZoneName(mode),
    timeZoneName: "short",
  }).format(date);
}

export function formatShort(value: unknown, mode: TimeZoneMode = "vn"): string {
  const date = parseDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timeZoneName(mode),
  }).format(date);
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Chưa có dữ liệu";
  return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value)} giây`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("vi-VN").format(value);
}

export function formatBoolean(value: boolean | null | undefined): string {
  return value === true ? "Có" : value === false ? "Không" : "Chưa rõ";
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("vi-VN", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

export type Tone = "neutral" | "success" | "warning" | "danger";

export function secondsTone(value: number | null | undefined): Tone {
  if (value === null || value === undefined) return "neutral";
  if (value <= 300) return "success";
  if (value <= 600) return "warning";
  return "danger";
}

const STATUS_LABELS: Record<string, { label: string; tone: Tone }> = {
  scheduled: { label: "Đã lên lịch", tone: "neutral" },
  pushing: { label: "Đang gửi", tone: "warning" },
  ambiguous: { label: "Chưa xác định (chờ đối soát)", tone: "warning" },
  submitted: { label: "Đã gửi", tone: "warning" },
  queued: { label: "Đang xếp hàng", tone: "warning" },
  running: { label: "Đang chạy", tone: "success" },
  timed_out: { label: "Hết thời gian quan sát", tone: "warning" },
  collecting: { label: "Đang thu kết quả", tone: "warning" },
  success: { label: "Thành công", tone: "success" },
  completed: { label: "Hoàn tất", tone: "success" },
  failed: { label: "Thất bại", tone: "danger" },
  wrong_gpu: { label: "Sai GPU", tone: "danger" },
  invalid_output: { label: "Kết quả không hợp lệ", tone: "danger" },
  cancelled: { label: "Đã hủy", tone: "danger" },
  skipped: { label: "Bỏ qua", tone: "neutral" },
  idle: { label: "Rảnh", tone: "neutral" },
  observed_idle: { label: "Quan sát không có hoạt động", tone: "neutral" },
  partial: { label: "Độ phủ một phần", tone: "warning" },
  stale: { label: "Bằng chứng cũ", tone: "warning" },
  unresolved: { label: "Chưa xác định", tone: "warning" },
  ok: { label: "Khỏe", tone: "success" },
  error: { label: "Lỗi", tone: "danger" },
  pending: { label: "Đang chờ", tone: "warning" },
  cancel_requested: { label: "Đã yêu cầu hủy", tone: "warning" },
  complete: { label: "Hoàn tất", tone: "success" },
};

export function statusMeta(value: unknown): { label: string; tone: Tone; raw: string | null } {
  const raw = asText(value);
  if (!raw) return { label: "Chưa rõ", tone: "neutral", raw: null };
  const normalized = raw.trim().toLowerCase().replace(/[\s-]+/g, "_");
  const known = STATUS_LABELS[normalized];
  if (known) return { ...known, raw };
  return { label: "Chưa rõ", tone: "neutral", raw };
}

/** Boundary text that never conflates an unknown upper bound with zero. */
export function boundsText(
  lower: number | null | undefined,
  upper: number | null | undefined,
): string {
  if ((lower === null || lower === undefined) && (upper === null || upper === undefined)) {
    return "Chưa biết cận";
  }
  if (lower !== null && lower !== undefined && upper !== null && upper !== undefined) {
    return `${formatSeconds(lower)} – ${formatSeconds(upper)}`;
  }
  if (lower !== null && lower !== undefined) return `≥ ${formatSeconds(lower)} · chưa biết cận trên`;
  return `≤ ${formatSeconds(upper ?? null)} · chưa biết cận dưới`;
}
