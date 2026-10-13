import { usePolling } from "@/hooks/use-polling";
import { getAnalysis, getDecision } from "@/lib/api";
import type { AnalysisFilters } from "@/lib/analysis";

export function useAnalysis(filters: AnalysisFilters) {
  return usePolling((signal) => getAnalysis(filters, signal), 15000, JSON.stringify(filters));
}
export function useDecision(filters: Pick<AnalysisFilters, "source" | "account">, mode: "earliest" | "schedule", horizon: number) {
  return usePolling((signal) => getDecision(filters, mode, horizon, signal), 15000, JSON.stringify([filters.source, filters.account, mode, horizon]));
}
