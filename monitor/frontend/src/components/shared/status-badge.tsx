import { AlertTriangle, WifiOff } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { statusMeta, type Tone } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TONE_VARIANT: Record<Tone, "secondary" | "success" | "warning" | "destructive"> = {
  neutral: "secondary",
  success: "success",
  warning: "warning",
  danger: "destructive",
};

export function StatusBadge({ status, className }: { status: unknown; className?: string }) {
  const meta = statusMeta(status);
  return (
    <Badge variant={TONE_VARIANT[meta.tone]} className={cn(className)} title={meta.raw ?? undefined}>
      {meta.label}
    </Badge>
  );
}

export function ToneBadge({
  value,
  tone,
  children,
  className,
}: {
  value?: string;
  tone: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Badge variant={TONE_VARIANT[tone]} className={cn(className)} title={value}>
      {children}
    </Badge>
  );
}

export function OfflineNotice({ error, hasData }: { error: string | null; hasData: boolean }) {
  if (!error) return null;
  return (
    <Alert variant="warning" className="mb-4">
      <AlertTriangle className="text-warning" />
      <AlertTitle>Không cập nhật được dữ liệu</AlertTitle>
      <AlertDescription>
        {error}.{" "}
        {hasData
          ? "Đang hiển thị bản dữ liệu thành công gần nhất và sẽ tự thử lại sau 15 giây."
          : "Chưa có dữ liệu để hiển thị. Sẽ tự thử lại sau 15 giây."}
      </AlertDescription>
    </Alert>
  );
}

export function NoData({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
      <WifiOff className="size-4 shrink-0" />
      {message}
    </div>
  );
}
