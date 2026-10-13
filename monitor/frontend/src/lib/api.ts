import type { Health, LogicResponse, RoadmapResponse, Summary } from "@/types";
import { adaptAnalysis, adaptDecision, type AnalysisFilters } from "@/lib/analysis";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface FetchOptions {
  signal: AbortSignal;
  acceptStatuses?: number[];
}

async function fetchJson<T>(path: string, { signal, acceptStatuses = [] }: FetchOptions): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Không kết nối được tới backend", 0);
  }
  const ok = response.ok || acceptStatuses.includes(response.status);
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    if (!ok) throw new ApiError(`HTTP ${response.status}`, response.status);
    throw new ApiError("Phản hồi không phải JSON hợp lệ", response.status);
  }
  if (!ok) throw new ApiError(`HTTP ${response.status}`, response.status);
  return body as T;
}

export function getSummary(signal: AbortSignal): Promise<Summary> {
  return fetchJson<Summary>("/api/summary", { signal }).then((data) => {
    if (!data || !Array.isArray(data.accounts) || !Array.isArray(data.runs) || !Array.isArray(data.hours ?? data.hourly)) {
      throw new ApiError("/api/summary: cấu trúc dữ liệu không hợp lệ", 200);
    }
    return { ...data, meta: data.meta ?? {}, hours: data.hours ?? data.hourly ?? [] };
  });
}

export function getHealth(signal: AbortSignal): Promise<Health> {
  // /healthz returns 503 with a valid JSON body when the worker is unhealthy.
  return fetchJson<Health>("/healthz", { signal, acceptStatuses: [503] }).then((data) => {
    if (!data || typeof data.ok !== "boolean") {
      throw new ApiError("/healthz: cấu trúc dữ liệu không hợp lệ", 503);
    }
    return data;
  });
}

export function getLogic(signal: AbortSignal): Promise<LogicResponse> {
  return fetchJson<LogicResponse>("/api/logic", { signal }).then((data) => {
    if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
      throw new ApiError("/api/logic: cấu trúc dữ liệu không hợp lệ", 200);
    }
    return data;
  });
}

export function getRoadmap(signal: AbortSignal): Promise<RoadmapResponse> {
  return fetchJson<RoadmapResponse>("/api/roadmap", { signal }).then((data) => {
    const snapshot = data?.snapshot ?? data;
    if (!data || (data.available !== false && !Array.isArray(snapshot?.issues))) {
      throw new ApiError("/api/roadmap: cấu trúc dữ liệu không hợp lệ", 200);
    }
    return data;
  });
}

export function getAnalysis(filters: AnalysisFilters, signal: AbortSignal) {
  const query = new URLSearchParams({ source: filters.source, day_type: filters.day_type });
  if (filters.account) query.set("account", filters.account);
  return fetchJson<unknown>(`/api/analysis?${query}`, { signal }).then(adaptAnalysis);
}

export function getDecision(filters: Pick<AnalysisFilters, "source" | "account">, mode: "earliest" | "schedule", horizon: number, signal: AbortSignal) {
  const query = new URLSearchParams({ mode, horizon_hours: String(horizon), source: filters.source });
  if (filters.account) query.set("account", filters.account);
  return fetchJson<unknown>(`/api/decision?${query}`, { signal }).then(adaptDecision);
}
