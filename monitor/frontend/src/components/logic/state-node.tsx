import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type LogicTone = "neutral" | "success" | "warning" | "danger";

export type LogicNodeData = {
  label: string;
  description: string;
  kind: string;
  active: boolean;
  terminal: boolean;
  occupancy: number | null;
  activeOccupancy: number | null;
  tone: LogicTone;
};

const TONE_BORDER: Record<LogicTone, string> = {
  neutral: "border-border",
  success: "border-success/50",
  warning: "border-warning/50",
  danger: "border-destructive/50",
};

export function StateNode({ data }: NodeProps) {
  const node = data as unknown as LogicNodeData;
  const remote = node.kind === "remote";
  return (
    <div
      className={cn(
        "w-52 rounded-lg border-2 bg-card px-3 py-2 text-card-foreground shadow-sm",
        TONE_BORDER[node.tone],
      )}
      title={node.description}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold" title={node.label}>
          {node.label}
        </span>
        {remote ? (
          <Badge variant="outline" className="text-[10px]">
            read-only
          </Badge>
         ) : (
          <Badge variant={node.activeOccupancy ? "warning" : "secondary"} className="text-[10px] tabular-nums">
             {node.occupancy ?? "—"}
            {node.activeOccupancy ? ` · ${node.activeOccupancy} chạy` : ""}
          </Badge>
         )}
      </div>
      <div className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground">
        <span>{remote ? "Remote provider" : "App state"}</span>
        {node.active ? <span className="text-warning">· hoạt động</span> : null}
        {node.terminal ? <span className="text-muted-foreground">· kết thúc</span> : null}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
