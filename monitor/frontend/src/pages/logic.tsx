import { useMemo, useState } from "react";
import { ReactFlow, Background, BackgroundVariant, Controls, MarkerType, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Network } from "lucide-react";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { PageHeader, SectionTitle } from "@/components/shared/layout-bits";
import { NoData, OfflineNotice, ToneBadge } from "@/components/shared/status-badge";
import { LoadingBlock } from "@/components/shared/data-state";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StateNode, type LogicNodeData, type LogicTone } from "@/components/logic/state-node";
import type { LogicEdge, LogicNode } from "@/types";
import { logicNodes } from "@/lib/logic";
import { formatBoolean, formatCount } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useTheme } from "@/hooks/use-theme";
import { DataFreshness } from "@/components/shared/data-freshness";

const nodeTypes = { state: StateNode };

const X_GAP = 270;
const Y_GAP = 104;

function toneFor(node: LogicNode): LogicTone {
  if (node.terminal) return node.occupancy && node.occupancy > 0 ? "success" : "neutral";
  if (node.active) return "warning";
  if (node.id === "provider") return "neutral";
  return "neutral";
}

export function buildLayout(nodes: LogicNode[], edges: LogicEdge[]): { nodes: Node<LogicNodeData>[]; edges: Edge[] } {
  const ids = new Set(nodes.map((node) => node.id));
  const adjacency = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  nodes.forEach((node) => {
    adjacency.set(node.id, []);
    indegree.set(node.id, 0);
  });

  const flowEdges: Edge[] = [];
  edges.forEach((edge, index) => {
    const from = edge.from ?? edge.source;
    const to = edge.to ?? edge.target;
    if (!from || !to || !ids.has(from) || !ids.has(to)) return;
    if (from !== to) {
      adjacency.get(from)?.push(to);
      indegree.set(to, (indegree.get(to) ?? 0) + 1);
    }
    flowEdges.push({
      id: `edge-${index}`,
      source: from,
      target: to,
      type: "smoothstep",
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
      label: edge.description && edge.description.length <= 22 ? edge.description : (edge.label ?? ""),
      labelStyle: { fill: "hsl(var(--muted-foreground))", fontSize: 10 },
    });
  });

  const layer = new Map<string, number>();
  const queue: string[] = nodes.filter((node) => (indegree.get(node.id) ?? 0) === 0 && node.kind !== "remote").map((node) => node.id);
  queue.forEach((id) => layer.set(id, 0));
  const order = [...queue];
  while (order.length > 0) {
    const id = order.shift() as string;
    const current = layer.get(id) ?? 0;
    for (const next of adjacency.get(id) ?? []) {
      layer.set(next, Math.max(layer.get(next) ?? 0, current + 1));
      indegree.set(next, (indegree.get(next) ?? 1) - 1);
      if (indegree.get(next) === 0) order.push(next);
    }
  }

  nodes.forEach((node) => {
    if (!layer.has(node.id)) layer.set(node.id, 0);
  });
  const maxLayer = Math.max(0, ...Array.from(layer.values()));

  const byLayer = new Map<number, string[]>();
  nodes.forEach((node) => {
    if (node.kind === "remote") return;
    const level = layer.get(node.id) ?? 0;
    if (!byLayer.has(level)) byLayer.set(level, []);
    byLayer.get(level)?.push(node.id);
  });

  const positioned = nodes.map((node) => {
    const level = node.kind === "remote" ? maxLayer + 1 : (layer.get(node.id) ?? 0);
    const group = byLayer.get(level) ?? [node.id];
    const index = group.indexOf(node.id);
    const center = (group.length - 1) / 2;
    return {
      id: node.id,
      type: "state" as const,
      position: { x: level * X_GAP, y: (index - center) * Y_GAP },
      data: {
        label: node.label ?? node.id,
        description: node.description ?? "",
        kind: node.kind ?? "app_state",
        active: node.active ?? false,
        terminal: node.terminal ?? false,
        occupancy: node.occupancy ?? null,
        activeOccupancy: node.active_occupancy ?? null,
        tone: toneFor(node),
      },
    };
  });

  return { nodes: positioned, edges: flowEdges };
}

export default function LogicPage() {
  const { logic } = useMonitorData();
  const data = logic.data;
  const { theme } = useTheme();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const observedNodes = useMemo(() => logicNodes(data), [data]);
  const selected = observedNodes.find((node) => node.id === selectedId);

  const { nodes, edges } = useMemo(() => buildLayout(observedNodes, data?.edges ?? []), [observedNodes, data?.edges]);

  return (
    <div>
      <PageHeader
        title="Sơ đồ logic"
        description="Máy trạng thái read-only do backend cung cấp. Chi tiết chuyển tiếp nằm ở bảng dưới; không có lượt chạy nào bị thay đổi từ trang này."
        actions={
          <Badge variant="outline" className="gap-1">
            <Network className="size-3.5" />
            Hủy: {formatBoolean(data?.cancellation_supported)}
          </Badge>
        }
      />

      <OfflineNotice error={logic.error} hasData={Boolean(data)} />
      <DataFreshness state={logic} timestamp={data?.generated_at} />

      {!data && logic.loading ? (
        <LoadingBlock className="h-[68vh] w-full" />
      ) : !data ? (
        <NoData message="Chưa tải được sơ đồ logic." />
      ) : (
        <div className="space-y-6">
          <Card>
            <CardContent className="p-0">
              <div className="h-[68vh] w-full" role="region" aria-label="Đồ thị chuyển trạng thái chỉ đọc" onKeyDown={(event) => {
                const id = (event.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
                if (id && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  setSelectedId(id);
                }
              }}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  fitViewOptions={{ padding: 0.25 }}
                  minZoom={0.15}
                  maxZoom={1.5}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  elementsSelectable
                  colorMode={theme}
                  onNodeClick={(_, node) => setSelectedId(node.id)}
                  edgesFocusable={false}
                  proOptions={{ hideAttribution: true }}
                  onNodesChange={() => {}}
                  onEdgesChange={() => {}}
                >
                  <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
                  <Controls position="bottom-left" showInteractive={false} />
                </ReactFlow>
              </div>
            </CardContent>
          </Card>

          <section aria-label="Danh sách trạng thái thay thế đồ thị">
            <SectionTitle>Trạng thái và số lượt hiện tại</SectionTitle>
            <p className="mb-3 text-xs text-muted-foreground">Kéo nền để di chuyển, dùng nút +/− để thu phóng. Chọn node hoặc nút dưới đây để xem chi tiết. Số lượt lấy trực tiếp từ backend.</p>
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {observedNodes.map((node) => (
                <li key={node.id} className="rounded-lg border p-3">
                  <Button variant="link" className="h-auto whitespace-normal p-0 text-left" onClick={() => setSelectedId(node.id)}>{node.label ?? node.id}</Button>
                  <p className="text-xs text-muted-foreground">{node.id} · {formatCount(node.occupancy)} lượt</p>
                  <p className="mt-1 text-sm">{node.description || "Chưa có mô tả."}</p>
                </li>
              ))}
            </ul>
            {observedNodes.length === 0 ? <NoData message="Backend chưa cung cấp trạng thái." /> : null}
          </section>

          <Dialog open={Boolean(selected)} onOpenChange={(open) => { if (!open) setSelectedId(null); }}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{selected?.label ?? selected?.id}</DialogTitle>
                <DialogDescription>{selected?.id} · {formatCount(selected?.occupancy)} lượt hiện tại</DialogDescription>
              </DialogHeader>
              <p className="text-sm">{selected?.description || "Chưa có mô tả."}</p>
              <ul className="space-y-2 text-xs">
                {data.edges.filter((edge) => (edge.source ?? edge.from) === selectedId || (edge.target ?? edge.to) === selectedId).map((edge, index) => (
                  <li key={index}>{edge.source ?? edge.from} → {edge.target ?? edge.to}: {edge.label ?? edge.description ?? "Chưa có mô tả"}</li>
                ))}
              </ul>
            </DialogContent>
          </Dialog>

          <div className="grid gap-4 lg:grid-cols-2">
            <section>
              <SectionTitle>Tập trạng thái</SectionTitle>
              <Card>
                <CardContent className="space-y-3 pt-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">App states ({data.states?.length ?? 0})</span>
                    {data.states?.map((state) => (
                      <Badge key={state} variant="outline" className="font-mono text-[10px]">
                        {state}
                      </Badge>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">Provider ({data.provider_states?.length ?? 0})</span>
                    {data.provider_states?.map((state) => (
                      <Badge key={state} variant="secondary" className="font-mono text-[10px]">
                        {state}
                      </Badge>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <ToneBadge tone="warning">Hoạt động</ToneBadge>
                    <ToneBadge tone="success">Kết thúc</ToneBadge>
                    <ToneBadge tone="neutral">Không hoạt động</ToneBadge>
                  </div>
                  {data.notes ? <p className="text-xs text-muted-foreground">{data.notes}</p> : null}
                </CardContent>
              </Card>
            </section>

            <section>
              <SectionTitle>
                Chuyển tiếp{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  ({formatCount(data.occupancy?.total)} lượt đã ghi nhận, {formatCount(data.occupancy?.active_total)} đang hoạt động)
                </span>
              </SectionTitle>
              <Card>
                <CardContent className="max-h-[24rem] overflow-y-auto nice-scroll p-0 pt-0">
                  {data.edges.length === 0 ? (
                    <div className="p-5">
                      <NoData message="Backend chưa cung cấp chuyển tiếp." />
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {data.edges.map((edge, index) => (
                        <li key={index} className="px-5 py-2.5">
                          <div className="font-mono text-xs">
                            {edge.from ?? edge.source} <span className="text-muted-foreground">→</span>{" "}
                            {edge.to ?? edge.target}
                          </div>
                          {edge.description || edge.label ? (
                            <div className="mt-0.5 text-xs text-muted-foreground">{edge.description ?? edge.label}</div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
