import { StrictMode, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePolling } from "../src/hooks/use-polling";

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("polls every 15s, retains the last success offline, and recovers", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ count: 7 }).mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ count: 8 });
  const { result } = renderHook(() => usePolling(fetcher));
  await act(async () => {});
  expect(result.current.data).toEqual({ count: 7 });
  const loaded = result.current.lastLoaded;
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(result.current).toMatchObject({ data: { count: 7 }, error: "Offline", lastLoaded: loaded, loading: false });
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(result.current).toMatchObject({ data: { count: 8 }, error: null });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it("prevents overlapping manual refreshes and aborts on timeout/unmount", async () => {
  let signal: AbortSignal | undefined;
  const fetcher = vi.fn((next: AbortSignal) => {
    signal = next;
    return new Promise<never>((_, reject) => next.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))));
  });
  const { result, unmount } = renderHook(() => usePolling(fetcher));
  act(() => { result.current.refresh(); result.current.refresh(); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(12000));
  expect(signal?.aborted).toBe(true);
  expect(result.current.error).toBe("Quá thời gian chờ");
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(fetcher).toHaveBeenCalledTimes(2);
  unmount();
  expect(signal?.aborted).toBe(true);
});

it("ignores a late response from the aborted StrictMode mount", async () => {
  const resolve: ((value: string) => void)[] = [];
  const fetcher = vi.fn(() => new Promise<string>((done) => resolve.push(done)));
  const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
  const { result } = renderHook(() => usePolling(fetcher), { wrapper });
  expect(resolve).toHaveLength(2);
  await act(async () => resolve[1]("current"));
  await act(async () => resolve[0]("obsolete"));
  expect(result.current.data).toBe("current");
});

it("aborts filter changes and never displays a probe snapshot for production", async () => {
  const requests: { signal: AbortSignal; resolve: (value: string) => void }[] = [];
  const fetcher = (signal: AbortSignal) => new Promise<string>((resolve) => requests.push({ signal, resolve }));
  const { result, rerender } = renderHook(({ source }) => usePolling(fetcher, 15000, source), { initialProps: { source: "probe" } });
  await act(async () => requests[0].resolve("probe snapshot"));
  act(() => result.current.refresh());
  rerender({ source: "production" });
  expect(requests[1].signal.aborted).toBe(true);
  expect(result.current.data).toBeNull();
  await act(async () => requests[1].resolve("late probe snapshot"));
  expect(result.current.data).toBeNull();
  await act(async () => requests[2].resolve("production empty"));
  expect(result.current.data).toBe("production empty");
});
