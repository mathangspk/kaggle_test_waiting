export function evidence(overrides: Record<string, unknown> = {}) {
  return { attempted: 20, resolved: 20, fast: 20, slow: 0, over10: 0, failures: 0,
    unknown: 0, pending: 0, overdue_unconfirmed: 0, skipped: 0, day_count: 7,
    resolved_day_count: 7, account_count: 1, success_rate: 1, rate_lower: 1,
    rate_upper: 1, median_seconds: 100, p90_seconds: 200, eligible: true, reason: null, ...overrides };
}
export function contractBucket(overrides: Record<string, unknown> = {}) {
  return { ...evidence(), hour_start: 6, label: "06:00 – 08:00", weighting: "equal_account",
    accounts: [{ account: "a", ...evidence() }], coverage: { configured: 1, observed: 1, missing_accounts: [] }, ...overrides };
}
export function contractCandidate(overrides: Record<string, unknown> = {}) {
  return { at: "2026-10-16T23:30:00Z", local_at: "2026-10-17T06:30:00+07:00", day_type: "weekend", hour_start: 6,
    waiting_seconds: 0, total_p50_seconds: 100, total_p90_seconds: 200, bucket: contractBucket(), ...overrides };
}
