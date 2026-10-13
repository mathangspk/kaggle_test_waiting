// @vitest-environment node
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { adaptAnalysis, adaptDecision } from "../src/lib/analysis";

// Run pure actual builders, not copied frontend-shaped approximations.
const fixtures = JSON.parse(execFileSync("python", [fileURLToPath(new URL("./backend-fixtures.py", import.meta.url)), fileURLToPath(new URL("../../app.py", import.meta.url))], { encoding: "utf8" }));
const bucket = (b: unknown) => adaptAnalysis({ schema_version: 2, metric: "submit_to_start", buckets: [b] }).buckets[0];
it("preserves equal-account 50%, not pooled 100/120", () => {
  expect(fixtures.weighted.resolved).toBe(120);
  expect(bucket(fixtures.weighted)).toMatchObject({ success_rate: 0.5, rate_lower: 0.5, rate_upper: 0.5, eligible: true });
});
it("preserves backend null point and weighted identification bounds for missing accounts", () => {
  expect(bucket(fixtures.missing)).toMatchObject({ success_rate: null, rate_lower: 1 / 3, rate_upper: 2 / 3, eligible: false });
  expect(bucket(fixtures.null_point)).toMatchObject({ fast: 100, resolved: 100, success_rate: null, rate_lower: 0.5, rate_upper: 1, eligible: false });
  expect(bucket({ ...fixtures.weighted, success_rate: undefined, rate_lower: undefined, rate_upper: undefined })).toMatchObject({ success_rate: null, rate_lower: null, rate_upper: null, eligible: false });
});
it("rejects contradictory partitions, subset counters, coverage, resolved dates and timings", () => {
  for (const change of [
    { attempted: 119 }, { unknown: 1 }, { fast: 101 }, { pending: 1 }, { overdue_unconfirmed: 1 },
    { resolved_day_count: 8 }, { resolved_day_count: 6 }, { resolved_day_count: undefined }, { day_count: undefined }, { account_count: undefined }, { median_seconds: 101 },
    { coverage: { configured: 3, observed: 2, missing_accounts: ["c"] } }, { accounts: [] },
    { accounts: fixtures.weighted.accounts.map((a: object) => ({ ...a, resolved_day_count: 6 })) },
    { accounts: fixtures.weighted.accounts.map((a: object) => ({ ...a, resolved: 19 })) },
    { accounts: fixtures.weighted.accounts.map((a: object) => ({ ...a, account_count: 0 })) },
  ]) expect(bucket({ ...fixtures.weighted, ...change }).eligible).toBe(false);
  expect(bucket({ ...fixtures.sparse, eligible: true }).eligible).toBe(false);
  expect(bucket({ ...fixtures.weighted, median_seconds: 101 })).toMatchObject({ median_seconds: null, p90_seconds: null });
});
it("consumes real tentative timestamp recommendation, nested bucket and zero-wait baseline", () => {
  expect(fixtures.tentative.status).toBe("tentative");
  const d = adaptDecision(fixtures.tentative);
  expect(d).toMatchObject({ abstain: false, tentative: true, certainty: "descriptive_only" });
  expect(d.recommendation).toMatchObject({ at: fixtures.tentative.recommended_at, wait_seconds: 0, median_seconds: 100, total_median_seconds: 100, eligible: true });
  expect(d.baseline).toEqual(d.recommendation);
});
it("accepts real Pareto dominance despite overlapping rate bounds, with day-specific waits", () => {
  const d = adaptDecision(fixtures.overlap);
  expect(d).toMatchObject({ abstain: false, tentative: true, certainty: "descriptive_only", selection_basis: "descriptive_p50_p90_pareto", material_margin_seconds: 30 });
  expect(d.limitations).toContain("no_latency_confidence");
  expect(d.limitations).toEqual(fixtures.overlap.limitations);
  expect(d.recommendation?.wait_seconds).toBe(0);
  const selected = fixtures.overlap.candidates.find((c: { at: string }) => c.at === fixtures.overlap.recommended_at);
  for (const c of fixtures.overlap.candidates) {
    if (c === selected) continue;
    expect(c.total_p50_seconds - selected.total_p50_seconds).toBeGreaterThanOrEqual(30);
    expect(c.total_p90_seconds - selected.total_p90_seconds).toBeGreaterThanOrEqual(30);
    expect(selected.bucket.rate_lower).toBeLessThanOrEqual(c.bucket.rate_upper);
  }
  expect(d.candidates.map((c) => c.total_median_seconds)).toEqual([100, 1850, 9060]);
  expect(d.candidates.map((c) => c.day_type)).toEqual(["weekday", "weekend", "weekend"]);
  expect(d.candidates[1].wait_seconds).toBe(1800);
});
it("preserves real latency tradeoff and negligible-difference abstention", () => {
  expect(fixtures.tradeoff.candidates[0].total_p50_seconds).toBeLessThan(fixtures.tradeoff.candidates[1].total_p50_seconds);
  expect(fixtures.tradeoff.candidates[0].total_p90_seconds).toBeGreaterThan(fixtures.tradeoff.candidates[1].total_p90_seconds);
  expect(["latency_tradeoff", "tradeoff"]).toContain(fixtures.tradeoff.reason);
  expect(adaptDecision(fixtures.tradeoff)).toMatchObject({ abstain: true, recommendation: null, reason: fixtures.tradeoff.reason });
  expect(["equivalent_total_latency", "equivalent"]).toContain(fixtures.equivalent.reason);
  expect(adaptDecision(fixtures.equivalent)).toMatchObject({ abstain: true, recommendation: null, reason: fixtures.equivalent.reason });
  expect(adaptDecision(fixtures.missing_baseline)).toMatchObject({ abstain: true, reason: "missing_baseline" });
  expect(adaptDecision(fixtures.insufficient)).toMatchObject({ abstain: true, reason: "insufficient_evidence" });
});
it("allows schedule tentative without current baseline and rejects unmatchable or inconsistent recommendation", () => {
  expect(adaptDecision(fixtures.schedule)).toMatchObject({ abstain: false, baseline: null, tentative: true });
  for (const change of [{ recommended_at: "missing" }, { status: "unknown" },
    { candidates: fixtures.tentative.candidates.map((c: object) => ({ ...c, total_p50_seconds: 99 })) }]) {
    expect(adaptDecision({ ...fixtures.tentative, ...change })).toMatchObject({ abstain: true, recommendation: null });
  }
});
export { fixtures };

it("preserves actual preliminary heldout diagnostic with separate dates and noncausal comparison", () => {
  const sampling = { interval_minutes: 60, phase: "preliminary", heldout: fixtures.heldout };
  const data = adaptAnalysis({ schema_version: 2, metric: "submit_to_start", source: "probe", buckets: [], sampling });
  expect(data.source).toBe("probe");
  expect(data.sampling).toEqual(sampling);
  expect(fixtures.heldout).toMatchObject({ status: "available", phase: "preliminary", causal: false, counterfactual: false, savings_seconds: null, selected_hours: [8], observed_rate_difference: -0.5 });
  expect(fixtures.heldout.training_dates).toHaveLength(7);
  expect(fixtures.heldout.heldout_dates).toHaveLength(7);
});
