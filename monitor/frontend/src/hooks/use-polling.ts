import { useCallback, useEffect, useRef, useState } from "react";
import type { AsyncState } from "@/types";

const REQUEST_TIMEOUT_MS = 12000;

/**
 * Polls a fetcher on an interval while:
 *  - aborting requests on timeout or unmount,
 *  - skipping a cycle while one is already running (no overlapping requests),
 *  - retaining the last successful data when a refresh fails (offline).
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs = 15000,
  requestKey = "default",
): AsyncState<T> & { refresh: () => void } {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    lastLoaded: null,
    loading: false,
  });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const inFlight = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [loadedKey, setLoadedKey] = useState(requestKey);

  const run = useCallback(async () => {
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setState((prev) => ({ ...prev, loading: true }));
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const data = await fetcherRef.current(controller.signal);
      if (!mounted.current || inFlight.current !== controller || controller.signal.aborted) return;
      setState({ data, error: null, lastLoaded: Date.now(), loading: false });
    } catch (error) {
      if (!mounted.current || inFlight.current !== controller) return;
      const message =
        error instanceof DOMException && error.name === "AbortError"
          ? "Quá thời gian chờ"
          : error instanceof Error
            ? error.message
            : "Lỗi không xác định";
      setState((prev) => ({ ...prev, error: message, loading: false }));
    } finally {
      clearTimeout(timer);
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    setLoadedKey(requestKey);
    setState({ data: null, error: null, lastLoaded: null, loading: true });
    void run();
    const id = setInterval(() => void run(), intervalMs);
    return () => {
      mounted.current = false;
      clearInterval(id);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [run, intervalMs, requestKey]);

  return { ...(loadedKey === requestKey ? state : { data: null, error: null, lastLoaded: null, loading: true }), refresh: () => void run() };
}
