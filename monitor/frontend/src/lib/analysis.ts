export type Source = "probe" | "production";
export type DayType = "all" | "weekday" | "weekend";
export interface AnalysisFilters { source: Source; account: string; day_type: DayType }
type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue => value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
const number = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const count = (value: unknown) => { const n = number(value); return n !== null && Number.isInteger(n) ? n : null; };
const rate = (value: unknown) => { const n = number(value); return n !== null && n <= 1 ? n : null; };
const text = (value: unknown): string | null => typeof value === "string" ? value : null;
export interface AnalysisBucket {
  hour_start: number; label: string;
  attempted: number | null; resolved: number | null; fast: number | null; slow: number | null;
  over10: number | null; failures: number | null; unknown: number | null; pending: number | null; overdue_unconfirmed: number | null; skipped: number | null;
  success_rate: number | null; rate_lower: number | null; rate_upper: number | null;
  wilson_low: number | null; wilson_high: number | null;
  median_seconds: number | null; p90_seconds: number | null;
  day_count: number | null; resolved_day_count: number | null; account_count: number | null; eligible: boolean; reason: string | null;
}

// pending and overdue_unconfirmed are subsets of unknown, not additional outcomes.
function validEvidence(b: RecordValue): boolean {
  const values = [b.attempted, b.resolved, b.fast, b.slow, b.over10, b.failures, b.unknown, b.pending, b.overdue_unconfirmed, b.skipped, b.day_count, b.resolved_day_count, b.account_count].map(count);
  if (values.some((v) => v === null)) return false;
  const [attempted, resolved, fast, slow, over10, failures, unknown, pending, overdue, , days, resolvedDays, accounts] = values as number[];
  const median = number(b.median_seconds), p90 = number(b.p90_seconds);
  const successful = fast + slow + over10;
  const validTiming = successful > 0
    ? median !== null && p90 !== null && median <= p90
    : b.median_seconds === null && b.p90_seconds === null;
  return attempted === resolved + unknown && resolved === fast + slow + over10 + failures
    && pending + overdue <= unknown && resolvedDays <= days && days <= attempted
    && resolvedDays <= resolved && accounts <= attempted && validTiming
    && (attempted === 0 ? days === 0 && accounts === 0 : days > 0 && accounts > 0)
    && (resolved === 0 ? resolvedDays === 0 : resolvedDays > 0);
}

function evidenceEligible(b: RecordValue, minResolved: number, minDays: number): boolean {
  return validEvidence(b) && b.eligible === true && count(b.resolved)! >= minResolved && count(b.resolved_day_count)! >= minDays
    && rate(b.success_rate) !== null && rate(b.rate_lower) !== null && rate(b.rate_upper) !== null && rate(b.rate_lower)! <= rate(b.rate_upper)!;
}

function bucketEligible(b: RecordValue, minResolved: number, minDays: number): boolean {
  if (!evidenceEligible(b, minResolved, minDays) || !Array.isArray(b.accounts)) return false;
  const coverage = record(b.coverage), configured = count(coverage.configured), observed = count(coverage.observed);
  if (configured === null || configured === 0 || observed !== configured || !Array.isArray(coverage.missing_accounts) || coverage.missing_accounts.length !== 0) return false;
  const rows = b.accounts.map(record);
  const names = rows.map((a) => text(a.account));
  if (names.some((name) => name === null) || new Set(names).size !== names.length || rows.length < configured) return false;
  if (count(b.account_count)! < observed || rows.some((a) => !validEvidence(a))) return false;
  for (const key of ["attempted", "resolved", "fast", "slow", "over10", "failures", "unknown", "pending", "overdue_unconfirmed", "skipped"]) {
    if (rows.reduce((sum, a) => sum + count(a[key])!, 0) !== count(b[key])) return false;
  }
  const low = rate(b.rate_lower), high = rate(b.rate_upper);
  if (low === null || high === null || low > high) return false;
  // For a single account, backend may also include non-selected rows. For aggregate
  // coverage, require every supplied account's evidence; never trust pooled minima.
  const selected = text(b.account) ? rows.filter((a) => a.account === b.account) : rows;
  return selected.length >= configured && selected.every((a) => evidenceEligible(a, minResolved, minDays))
    && rate(b.success_rate) !== null;
}
export interface AnalysisAccount { id: string; name: string; attempted: number | null; resolved: number | null; day_count: number | null }
export interface Analysis {
  source: Source | null;
  generated_at: string | null; definitions: Record<string, string>;
  policy: { min_resolved: number; min_days: number }; buckets: AnalysisBucket[];
  accounts: AnalysisAccount[]; sampling: RecordValue; reason: string | null;
}

export function adaptAnalysis(value: unknown): Analysis {
  const raw = record(value);
  if (raw.schema_version !== 2 || raw.metric !== "submit_to_start" || !Array.isArray(raw.buckets)) {
    throw new Error("/api/analysis: cần schema_version 2 và metric submit_to_start");
  }
  const policy = record(raw.policy);
  const min_resolved = Math.max(20, count(policy.min_resolved) ?? 20);
  const min_days = Math.max(7, count(policy.min_days) ?? 7);
  const seen = new Set<number>();
  const buckets = raw.buckets.map((value): AnalysisBucket => {
    const b = record(value);
    const hour = count(b.hour_start);
    if (hour === null || hour > 22 || hour % 2 !== 0 || seen.has(hour)) throw new Error("/api/analysis: khung 2 giờ không hợp lệ");
    seen.add(hour);
    const resolved = count(b.resolved), attempted = count(b.attempted), fast = count(b.fast);
    // Backend rates/bounds are equal-account weighted. Null means unavailable;
    // pooled counts cannot reconstruct the selected-account estimand.
    const point = rate(b.success_rate);
    const lower = rate(b.rate_lower), upper = rate(b.rate_upper);
    const days = count(b.day_count);
    const accounts = count(b.account_count);
    const wilsonLow = point !== null ? rate(b.wilson_low) : null;
    const wilsonHigh = point !== null ? rate(b.wilson_high) : null;
    const validWilson = wilsonLow !== null && wilsonHigh !== null && wilsonLow <= wilsonHigh;
    const eligible = bucketEligible(b, min_resolved, min_days);
    const median = number(b.median_seconds), p90 = number(b.p90_seconds);
    const validTiming = resolved !== null && resolved > 0 && median !== null && p90 !== null && median <= p90;
    return {
      hour_start: hour, label: text(b.label) ?? `${String(hour).padStart(2, "0")}:00 – ${String((hour + 2) % 24).padStart(2, "0")}:00`,
      attempted, resolved, fast, slow: count(b.slow), over10: count(b.over10), failures: count(b.failures),
      unknown: count(b.unknown), pending: count(b.pending), overdue_unconfirmed: count(b.overdue_unconfirmed), skipped: count(b.skipped),
      success_rate: point, rate_lower: lower !== null && upper !== null && lower <= upper ? lower : null,
      rate_upper: lower !== null && upper !== null && lower <= upper ? upper : null,
      wilson_low: validWilson ? wilsonLow : null, wilson_high: validWilson ? wilsonHigh : null,
      median_seconds: validTiming ? median : null,
      p90_seconds: validTiming ? p90 : null,
      day_count: days, resolved_day_count: count(b.resolved_day_count), account_count: accounts, eligible,
      reason: eligible ? null : b.eligible === true ? "Bằng chứng, phân hoạch kết quả hoặc độ phủ tài khoản không hợp lệ" : text(b.reason) ?? "Chưa đủ bằng chứng hoặc độ phủ tài khoản chưa xác nhận",
    };
  }).sort((a, b) => a.hour_start - b.hour_start);
  const accounts = Array.isArray(raw.accounts) ? raw.accounts.flatMap((value): AnalysisAccount[] => {
    const a = record(value), id = text(a.id) ?? text(a.account) ?? text(a.account_id);
    return id ? [{ id, name: text(a.name) ?? text(a.account_name) ?? id, attempted: count(a.attempted), resolved: count(a.resolved), day_count: count(a.day_count) }] : [];
  }) : [];
  return { source: raw.source === "probe" || raw.source === "production" ? raw.source : null,
    generated_at: text(raw.generated_at), policy: { min_resolved, min_days }, buckets, accounts,
    definitions: Object.fromEntries(Object.entries(record(raw.definitions)).filter((entry): entry is [string, string] => typeof entry[1] === "string")),
    sampling: record(raw.sampling), reason: text(raw.reason) };
}

export interface DecisionCandidate {
  at: string | null; local_at: string | null; day_type: string | null;
  hour_start: number | null; wait_seconds: number | null; median_seconds: number | null; p90_seconds: number | null;
  total_median_seconds: number | null; total_p90_seconds: number | null; eligible: boolean; reason: string | null;
}
export interface Decision {
  tentative: boolean; certainty: string | null;
  selection_basis: string | null; limitations: string[]; material_margin_seconds: number | null;
  generated_at: string | null; abstain: boolean; status: string | null; reason: string | null;
  baseline: DecisionCandidate | null; candidates: DecisionCandidate[]; recommendation: DecisionCandidate | null;
}

const DECISION_REASONS: Record<string, string> = {
  latency_tradeoff: "Đánh đổi độ trễ: trung vị thấp hơn nhưng P90 cao hơn; không có phương án trội cả hai",
  tradeoff: "Đánh đổi độ trễ: trung vị thấp hơn nhưng P90 cao hơn; không có phương án trội cả hai",
  equivalent_total_latency: "Tổng độ trễ tương đương: chênh lệch không đủ đáng kể để chọn một phương án",
  equivalent: "Tổng độ trễ tương đương: chênh lệch không đủ đáng kể để chọn một phương án",
  insufficient_evidence: "Chưa đủ bằng chứng để so sánh độ trễ",
  missing_baseline: "Thiếu bằng chứng cho phương án gửi ngay (baseline)",
  historical_comparison: "So sánh mô tả phân bố độ trễ lịch sử",
  sole_eligible_candidate: "Chỉ có một ứng viên đủ điều kiện; không có phương án khác để so sánh",
  invalid_candidate_evidence: "Bằng chứng ứng viên thiếu hoặc mâu thuẫn; chưa thể so sánh",
};
export function decisionReasonLabel(reason: string | null): string {
  return reason === null ? "Backend chưa cung cấp lý do" : Object.prototype.hasOwnProperty.call(DECISION_REASONS, reason) ? DECISION_REASONS[reason] : `Lý do backend chưa có nhãn: ${reason}`;
}
function candidate(value: unknown, minResolved: number, minDays: number): DecisionCandidate | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const c = record(value), b = record(c.bucket);
  const wait = number(c.waiting_seconds), median = number(b.median_seconds), p90 = number(b.p90_seconds);
  const totalMedian = number(c.total_p50_seconds), totalP90 = number(c.total_p90_seconds);
  const same = (a: number | null, v: number | null, w: number | null) => a !== null && v !== null && w !== null && Math.abs(a - (v + w)) <= 1e-6;
  const timingValid = median !== null && p90 !== null && median <= p90 && same(totalMedian, median, wait) && same(totalP90, p90, wait);
  const hour = count(c.hour_start), at = text(c.at);
  const eligible = bucketEligible(b, minResolved, minDays) && timingValid && hour !== null && hour <= 22 && hour % 2 === 0 && hour === count(b.hour_start) && at !== null && Number.isFinite(Date.parse(at));
  return { at: text(c.at), local_at: text(c.local_at), day_type: text(c.day_type),
    hour_start: count(c.hour_start), wait_seconds: wait, median_seconds: median, p90_seconds: p90,
    total_median_seconds: totalMedian, total_p90_seconds: totalP90,
    eligible, reason: eligible ? null : text(b.reason) ?? "Thiếu bằng chứng hoặc thời lượng mâu thuẫn" };
}
export function adaptDecision(value: unknown): Decision {
  const raw = record(value);
  if (!Object.keys(raw).length) throw new Error("/api/decision: phản hồi không hợp lệ");
  const policy = record(raw.policy), minResolved = Math.max(20, count(policy.min_resolved) ?? 20), minDays = Math.max(7, count(policy.min_days) ?? 7);
  const candidates = Array.isArray(raw.candidates) ? raw.candidates.flatMap((c) => candidate(c, minResolved, minDays) ?? []) : [];
  const baseline = candidates.find((c) => c.wait_seconds === 0) ?? null;
  const recommendedAt = text(raw.recommended_at);
  const matches = recommendedAt === null ? [] : candidates.filter((c) => c.at === recommendedAt);
  const recommendation = matches.length === 1 ? matches[0] : null;
  const tentative = raw.status === "tentative";
  const abstain = !tentative || !recommendation?.eligible || candidates.some((c) => !c.eligible) || (raw.mode !== "schedule" && !baseline?.eligible);
  return { generated_at: text(raw.generated_at), abstain, tentative: tentative && !abstain, certainty: text(raw.certainty),
    selection_basis: text(raw.selection_basis), limitations: Array.isArray(raw.limitations) ? raw.limitations.filter((v): v is string => typeof v === "string") : [],
    material_margin_seconds: number(raw.material_margin_seconds),
    status: text(raw.status), reason: tentative && abstain ? "invalid_candidate_evidence" : text(raw.reason),
    baseline, candidates,
    recommendation: abstain ? null : recommendation };
}
