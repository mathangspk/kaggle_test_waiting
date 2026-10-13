import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { usePolling } from "@/hooks/use-polling";
import { getHealth, getLogic, getRoadmap, getSummary } from "@/lib/api";
import type { AsyncState, Health, LogicResponse, RoadmapResponse, Summary } from "@/types";

const REFRESH_MS = 15000;

interface MonitorDataValue {
  summary: AsyncState<Summary>;
  health: AsyncState<Health>;
  logic: AsyncState<LogicResponse>;
  roadmap: AsyncState<RoadmapResponse>;
  refreshAll: () => void;
}

const MonitorDataContext = createContext<MonitorDataValue | null>(null);

export function MonitorDataProvider({ children }: { children: ReactNode }) {
  const summary = usePolling<Summary>(getSummary, REFRESH_MS);
  const health = usePolling<Health>(getHealth, REFRESH_MS);
  const logic = usePolling<LogicResponse>(getLogic, REFRESH_MS);
  const roadmap = usePolling<RoadmapResponse>(getRoadmap, REFRESH_MS);

  const refreshAll = useCallback(() => {
    summary.refresh();
    health.refresh();
    logic.refresh();
    roadmap.refresh();
  }, [summary, health, logic, roadmap]);

  const value = useMemo<MonitorDataValue>(
    () => ({ summary, health, logic, roadmap, refreshAll }),
    [summary, health, logic, roadmap, refreshAll],
  );

  return <MonitorDataContext.Provider value={value}>{children}</MonitorDataContext.Provider>;
}

export function useMonitorData(): MonitorDataValue {
  const context = useContext(MonitorDataContext);
  if (!context) throw new Error("useMonitorData must be used within MonitorDataProvider");
  return context;
}
