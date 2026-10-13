import { afterEach, expect, it, vi } from "vitest";
import { adaptAnalysis, adaptDecision, decisionReasonLabel } from "../src/lib/analysis";
import { getAnalysis, getDecision } from "../src/lib/api";
import { contractBucket, contractCandidate } from "./contract-helpers";

const analysis = (b: Record<string, unknown>) => adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [{ hour_start: 0, ...b }] }).buckets[0];
afterEach(() => vi.unstubAllGlobals());

it("preserves selection metadata and unknown reasons without inventing a category", () => {
  const c = contractCandidate();
  expect(adaptDecision({ status: "tentative", recommended_at: c.at, candidates: [c], selection_basis: "descriptive_p50_p90_pareto", limitations: ["no_latency_confidence", "future_limit", 1], certainty: "descriptive_only" })).toMatchObject({ abstain: false, selection_basis: "descriptive_p50_p90_pareto", limitations: ["no_latency_confidence", "future_limit"], certainty: "descriptive_only" });
  expect(adaptDecision({ status: "abstain", reason: "future_reason" }).reason).toBe("future_reason");
  expect(decisionReasonLabel("future_reason")).toBe("Lý do backend chưa có nhãn: future_reason");
  expect(decisionReasonLabel("toString")).toBe("Lý do backend chưa có nhãn: toString");
  expect(decisionReasonLabel(null)).toBe("Backend chưa cung cấp lý do");
  expect(adaptDecision({ status: "tentative", recommended_at: c.at, candidates: [c] })).toMatchObject({ abstain: false, selection_basis: null, limitations: [] });
});

it("never derives point or bounds from pooled counts even when fast is 100%", () => {
  const b = analysis({ attempted: 100, resolved: 100, fast: 100, success_rate: null, rate_lower: null, rate_upper: null });
  expect(b).toMatchObject({ success_rate: null, rate_lower: null, rate_upper: null, eligible: false });
});
it("does not turn missing or pending-only evidence into a zero point or median", () => {
  expect(analysis({ attempted: 4, resolved: 0, fast: 0, pending: 4, median_seconds: 0, success_rate: null, rate_lower: 0, rate_upper: 1 })).toMatchObject({ success_rate: null, median_seconds: null, p90_seconds: null, rate_lower: 0, rate_upper: 1, eligible: false });
  expect(analysis({ attempted: 4 })).toMatchObject({ resolved: null, fast: null, success_rate: null, pending: null, unknown: null, rate_lower: null, rate_upper: null });
  const a = adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [], accounts: [{ account_id: "a", name: "A" }] });
  expect(a.accounts[0]).toEqual({ id: "a", name: "A", attempted: null, resolved: null, day_count: null });
});
it("requires backend eligibility, both per-window minima and known account coverage", () => {
  const base = contractBucket();
  expect(analysis(base).eligible).toBe(true);
  for (const change of [{ resolved: 19 }, { day_count: 6 }, { account_count: null }, { eligible: false }, { resolved: 31 }]) expect(analysis({ ...base, ...change }).eligible).toBe(false);
  expect(() => adaptAnalysis({ schema_version: 1, metric: "queue", buckets: [] })).toThrow();
});
it("preserves supplied bounds/Wilson without inventing confidence", () => {
  expect(analysis({ attempted: 30, resolved: 20, fast: 10, success_rate: 0.5, rate_lower: 0.2, rate_upper: 0.8, wilson_low: 0.3, wilson_high: 0.7 })).toMatchObject({ rate_lower: 0.2, rate_upper: 0.8, wilson_low: 0.3, wilson_high: 0.7 });
  expect(analysis({ attempted: 20, resolved: 20, fast: 10 }).wilson_low).toBeNull();
  expect(analysis({ attempted: 20, resolved: 20, fast: 10, wilson_low: 0.9, wilson_high: 0.1 })).toMatchObject({ wilson_low: null, wilson_high: null });
});

it("omits account for all-account requests and never sends day_type to decision", async () => {
  const signal = new AbortController().signal;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ schema_version: 2, metric: "submit_to_start", buckets: [] }))).mockResolvedValueOnce(new Response(JSON.stringify({ abstain: true }))));
  await getAnalysis({ source: "probe", account: "", day_type: "all" }, signal);
  await getDecision({ source: "probe", account: "" }, "earliest", 6, signal);
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/analysis?source=probe&day_type=all", expect.anything());
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/decision?mode=earliest&horizon_hours=6&source=probe", expect.anything());
});
it("abstains on unknown eligibility, missing baseline or incomplete timings", () => {
  const c = contractCandidate();
  expect(adaptDecision({ status: "tentative", candidates: [c], recommended_at: c.at }).recommendation).toMatchObject({ total_median_seconds: 100, total_p90_seconds: 200 });
  for (const payload of [
    { status: "abstain", candidates: [c], recommended_at: c.at },
    { status: "tentative", candidates: [{ ...c, waiting_seconds: 1 }], recommended_at: c.at },
    { status: "tentative", candidates: [{ ...c, bucket: { eligible: true } }], recommended_at: c.at },
    { status: "new_status", candidates: [c], recommended_at: c.at },
  ]) expect(adaptDecision(payload)).toMatchObject({ abstain: true, recommendation: null });
});
it("uses exact read-only queries and leaves empty production data empty", async () => {
  const signal = new AbortController().signal;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ schema_version: 2, metric: "submit_to_start", buckets: [] }))).mockResolvedValueOnce(new Response(JSON.stringify({ abstain: true, reason: "no production evidence", candidates: [], recommendation: null }))));
  expect((await getAnalysis({ source: "production", account: "a/b", day_type: "weekend" }, signal)).buckets).toEqual([]);
  await getDecision({ source: "production", account: "a/b" }, "schedule", 6, signal);
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/analysis?source=production&day_type=weekend&account=a%2Fb", expect.objectContaining({ signal, cache: "no-store" }));
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/decision?mode=schedule&horizon_hours=6&source=production&account=a%2Fb", expect.objectContaining({ signal }));
});
