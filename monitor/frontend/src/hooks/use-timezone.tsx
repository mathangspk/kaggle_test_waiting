import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { TimeZoneMode } from "@/lib/format";

interface TimezoneContextValue {
  mode: TimeZoneMode;
  setMode: (mode: TimeZoneMode) => void;
  toggle: () => void;
}

const TimezoneContext = createContext<TimezoneContextValue | null>(null);
const STORAGE_KEY = "kaggle-monitor-timezone";

function initialMode(): TimeZoneMode {
  if (typeof window === "undefined") return "vn";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "vn" || saved === "local") return saved;
  } catch {
    /* ignore storage access errors */
  }
  return "vn";
}

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<TimeZoneMode>(initialMode);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* ignore storage access errors */
    }
  }, [mode]);

  const value = useMemo<TimezoneContextValue>(
    () => ({
      mode,
      setMode: setModeState,
      toggle: () => setModeState((prev) => (prev === "vn" ? "local" : "vn")),
    }),
    [mode],
  );

  return <TimezoneContext.Provider value={value}>{children}</TimezoneContext.Provider>;
}

export function useTimezone(): TimezoneContextValue {
  const context = useContext(TimezoneContext);
  if (!context) throw new Error("useTimezone must be used within TimezoneProvider");
  return context;
}
