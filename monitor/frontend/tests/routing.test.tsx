import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HashRouter } from "react-router-dom";
import App from "../src/App";

// Exercise the actual shell, links, lazy routes and Router 7 with isolated pages.
vi.mock("../src/pages/overview", () => ({ default: () => <h1>Overview route</h1> }));
vi.mock("../src/pages/hours", () => ({ default: () => <h1>Hours route</h1> }));
vi.mock("../src/pages/runs", () => ({ default: () => <h1>Runs route</h1> }));
vi.mock("../src/pages/roadmap", () => ({ default: () => <h1>Roadmap route</h1> }));
vi.mock("../src/pages/logic", () => ({ default: () => <h1>Logic route</h1> }));
vi.mock("../src/pages/operations", () => ({ default: () => <h1>Operations route</h1> }));
vi.mock("../src/hooks/use-monitor-data", () => ({
  useMonitorData: () => ({
    summary: { data: null, error: null, loading: false },
    health: { data: null, error: null, loading: false },
    refreshAll: vi.fn(),
  }),
}));
vi.mock("../src/hooks/use-theme", () => ({ useTheme: () => ({ theme: "dark", toggle: vi.fn() }) }));
vi.mock("../src/hooks/use-timezone", () => ({ useTimezone: () => ({ mode: "vn", toggle: vi.fn() }) }));

afterEach(() => { cleanup(); window.history.replaceState(null, "", "/"); });

it("preserves six hash routes and active sidebar links under Router 7", async () => {
  window.history.replaceState(null, "", "/static/#/runs");
  render(<HashRouter><App /></HashRouter>);
  await screen.findByRole("heading", { name: "Runs route" });
  const routes = [
    ["Tổng quan", "Overview route", "#/"],
    ["Khung giờ", "Hours route", "#/hours"],
    ["Lượt chạy", "Runs route", "#/runs"],
    ["Lộ trình", "Roadmap route", "#/roadmap"],
    ["Sơ đồ logic", "Logic route", "#/logic"],
    ["Vận hành", "Operations route", "#/operations"],
  ];
  for (const [label, heading, hash] of routes) {
    const link = screen.getByRole("link", { name: new RegExp(label) });
    fireEvent.click(link);
    await screen.findByRole("heading", { name: heading });
    expect(window.location.hash).toBe(hash);
    expect(link.getAttribute("aria-current")).toBe("page");
    expect(screen.getAllByRole("link").filter((item) => item.getAttribute("aria-current") === "page")).toHaveLength(1);
  }
});

it("keeps direct unknown hash links falling back to Overview", async () => {
  window.history.replaceState(null, "", "/static/#/unknown");
  await act(async () => { render(<HashRouter><App /></HashRouter>); });
  await screen.findByRole("heading", { name: "Overview route" });
  await waitFor(() => expect(window.location.hash).toBe("#/"));
});
