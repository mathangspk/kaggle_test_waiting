import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth, getLogic, getRoadmap, getSummary } from "../src/lib/api";
import { logicNodes } from "../src/lib/logic";
import { boundsText, formatBoolean, formatDateTime, freshnessText } from "../src/lib/format";
import { buildLayout } from "../src/pages/logic";

const signal = new AbortController().signal;
function respond(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })));
}
afterEach(() => vi.unstubAllGlobals());

describe("Backend contracts", () => {
  it("reads the top-level roadmap contract and blocked issue IDs", async () => {
    const roadmap = { repo: "test/repo", synced_at: "2026-10-13T00:00:00Z", issues: [{ number: 4, title: "Issue", state: "OPEN", body: "Details", updatedAt: "2026-10-12T00:00:00Z" }], dependencies: [{ from: 4, to: 12 }], blocked: [4] };
    respond(roadmap);
    expect(await getRoadmap(signal)).toEqual(roadmap);
    expect(fetch).toHaveBeenCalledWith("/api/roadmap", expect.objectContaining({ signal, cache: "no-store" }));
  });

  it("keeps compatibility with wrapped roadmap snapshots", async () => {
    respond({ available: true, snapshot: { issues: [], dependencies: [], blocked: [] } });
    expect((await getRoadmap(signal)).snapshot?.issues).toEqual([]);
  });

  it("uses backend state counts, preserving unknown occupancy", async () => {
    respond({ nodes: [{ id: "queued", label: "Queued", description: "Wait" }, { id: "running" }, { id: "unknown" }], edges: [{ source: "queued", target: "running", label: "Start" }], counts: { queued: 3, running: 0 } });
    const data = await getLogic(signal);
    expect(logicNodes(data).map((node) => node.occupancy)).toEqual([3, 0, null]);
    expect(data.edges[0].label).toBe("Start");
  });

  it("accepts structured occupancy from older responses", () => {
    const occupancy = { total: 7, active_total: 2, by_state: { running: 2 }, active_by_state: { running: 2 } };
    expect(logicNodes({ nodes: [{ id: "running" }], edges: [], counts: occupancy })[0]).toMatchObject({ occupancy: 2, active_occupancy: 2 });
  });

  it("accepts unhealthy 503 JSON and rejects API errors/malformed data", async () => {
    respond({ ok: false, worker_alive: false }, 503);
    expect((await getHealth(signal)).ok).toBe(false);
    respond({ detail: "Unavailable" }, 503);
    await expect(getSummary(signal)).rejects.toThrow("HTTP 503");
    respond({ hours: {}, runs: [], accounts: [] });
    await expect(getSummary(signal)).rejects.toThrow("cấu trúc dữ liệu");
  });

  it("normalizes the hourly alias without inventing metrics", async () => {
    respond({ hourly: [{ hour: 1, median_queue_seconds: null }], runs: [], accounts: [] });
    expect((await getSummary(signal)).hours[0].median_queue_seconds).toBeNull();
  });
});

describe("Graph and evidence semantics", () => {
  it("terminates layout for cycles and preserves self-loops", () => {
    const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }];
    const edges = [{ source: "a", target: "b" }, { source: "b", target: "c" }, { source: "c", target: "b" }, { source: "a", target: "a" }];
    const graph = buildLayout(nodes, edges);
    expect(graph.nodes).toHaveLength(3);
    expect(graph.edges).toHaveLength(4);
    expect(graph.nodes.every((node) => Number.isFinite(node.position.x))).toBe(true);
  });

  it("keeps unknown queue bounds and booleans unknown", () => {
    expect(boundsText(42, null)).toContain("chưa biết cận trên");
    expect(boundsText(null, null)).toBe("Chưa biết cận");
    expect(formatBoolean(undefined)).toBe("Chưa rõ");
    expect(freshnessText(new Date().toISOString(), null)).toContain("Chưa rõ ngưỡng");
    expect(formatDateTime("2026-10-13T00:00:00Z")).toContain("07:00:00");
  });
});
