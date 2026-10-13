import type { LogicNode, LogicOccupancy, LogicResponse } from "@/types";

/** Counts are backend observations, never inferred from the truncated runs list. */
export function logicNodes(data: LogicResponse | null): LogicNode[] {
  if (!data) return [];
  const counts = data.counts;
  const structured = counts && typeof counts.by_state === "object"
    ? counts as LogicOccupancy
    : data.occupancy;
  const byState = structured?.by_state ?? counts as Record<string, number | null> | undefined;
  return data.nodes.map((node) => {
    const count = byState?.[node.id];
    return {
      ...node,
      active: node.active ?? data.active_states?.includes(node.id),
      terminal: node.terminal ?? data.terminal_states?.includes(node.id),
      occupancy: byState && Object.prototype.hasOwnProperty.call(byState, node.id)
        ? typeof count === "number" && Number.isFinite(count) && count >= 0 ? count : null
        : node.occupancy ?? null,
      active_occupancy: structured?.active_by_state?.[node.id] ?? node.active_occupancy ?? null,
    };
  });
}
