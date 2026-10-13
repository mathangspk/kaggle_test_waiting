import { useMemo, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Ban, Database, Globe, Menu, Moon, RefreshCw, Sun } from "lucide-react";
import { SidebarNav } from "@/components/layout/sidebar";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NAV_ITEMS } from "@/lib/nav";
import { useMonitorData } from "@/hooks/use-monitor-data";
import { useTheme } from "@/hooks/use-theme";
import { useTimezone } from "@/hooks/use-timezone";
import { formatBoolean, formatDateTime, freshnessAge, freshnessText } from "@/lib/format";
import { cn } from "@/lib/utils";

type Connection = "loading" | "online" | "offline";

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const { summary, health, refreshAll } = useMonitorData();
  const { theme, toggle: toggleTheme } = useTheme();
  const { mode, toggle: toggleTimezone } = useTimezone();

  const current = useMemo(
    () => NAV_ITEMS.find((item) => item.to === location.pathname) ?? NAV_ITEMS[0],
    [location.pathname],
  );

  const meta = summary.data?.meta;
  const staleAfter = typeof meta?.stale_after_seconds === "number" ? meta.stale_after_seconds : null;
  const connection: Connection = summary.error
    ? "offline"
    : summary.data
      ? "online"
      : "loading";
  const age = freshnessAge(meta?.data_updated_at);
  const schemaVersion = summary.data?.schema_version ?? health.data?.schema_version ?? null;

  const connectionLabel =
    connection === "online"
      ? "API kết nối tốt"
      : connection === "offline"
        ? "Bản dữ liệu gần nhất (đang lỗi)"
        : "Đang kết nối";

  return (
    <div className="min-h-dvh bg-background">
      <div className="mx-auto flex min-h-dvh w-full max-w-[1500px]">
        <aside className="sticky top-0 hidden h-dvh w-72 shrink-0 border-r border-border bg-card lg:block">
          <SidebarNav schemaVersion={schemaVersion} />
        </aside>

        <div className="flex min-h-dvh w-full min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
            <div className="flex flex-wrap items-center gap-2 px-3 py-3 sm:px-5">
              <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
                <SheetTrigger asChild>
                  <Button variant="outline" size="icon" className="lg:hidden" aria-label="Mở điều hướng">
                    <Menu />
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-72 p-0">
                   <SheetTitle className="sr-only">Điều hướng chính</SheetTitle>
                  <SheetDescription className="sr-only">Chọn một trong sáu trang theo dõi.</SheetDescription>
                  <SidebarNav onNavigate={() => setMenuOpen(false)} schemaVersion={schemaVersion} />
                </SheetContent>
              </Sheet>

              <div className="mr-auto min-w-0">
                <div className="text-[11px] font-semibold uppercase tracking-widest text-primary">{current.label}</div>
                <div className="truncate text-sm font-medium text-muted-foreground">{current.description}</div>
              </div>

              <div className="flex items-center gap-1.5">
                <Badge
                  variant={
                    connection === "online" ? "success" : connection === "offline" ? "destructive" : "secondary"
                  }
                  className="hidden sm:inline-flex"
                  title={meta?.heartbeat_utc ? `Heartbeat: ${formatDateTime(meta.heartbeat_utc, mode)}` : undefined}
                >
                  <span
                    className={cn(
                      "mr-1 size-1.5 rounded-full",
                      connection === "online" ? "bg-success-foreground" : "bg-current",
                    )}
                  />
                  {connectionLabel}
                </Badge>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={toggleTimezone}
                  title="Chuyển múi giờ hiển thị"
                  aria-label="Chuyển múi giờ hiển thị"
                >
                  <Globe />
                  {mode === "vn" ? "VN" : "Máy"}
                </Button>

                <Button
                  variant="outline"
                  size="icon"
                  onClick={toggleTheme}
                  title={theme === "dark" ? "Chuyển sang sáng" : "Chuyển sang tối"}
                  aria-label="Chuyển giao diện sáng hoặc tối"
                >
                  {theme === "dark" ? <Sun /> : <Moon />}
                </Button>

                <Button size="sm" onClick={refreshAll} disabled={summary.loading} aria-label="Làm mới dữ liệu">
                  <RefreshCw className={cn(summary.loading && "animate-spin")} />
                  <span className="hidden sm:inline">Làm mới</span>
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/60 px-3 py-1.5 text-xs text-muted-foreground sm:px-5">
              <span>
                Bộ thu thập: {freshnessText(meta?.data_updated_at, staleAfter)}
                {age !== null && staleAfter != null && age < -30 ? " · lệch đồng hồ" : ""}
              </span>
              <span className="hidden sm:inline">
                Bằng chứng cập nhật: {formatDateTime(meta?.evidence_updated_at, mode)}
              </span>
              <span>Tự làm mới mỗi 15 giây</span>
              <span className="ml-auto hidden items-center gap-1 md:flex">
                <Ban className="size-3.5" />
                Hỗ trợ hủy (backend): {formatBoolean(meta?.cancellation_supported)}
              </span>
            </div>
          </header>

          <main className="w-full flex-1 px-3 py-5 sm:px-5 sm:py-6">
            <Outlet />
          </main>

          <footer className="border-t border-border px-3 py-4 text-xs text-muted-foreground sm:px-5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="inline-flex items-center gap-1.5">
                <Database className="size-3.5" />
                Kaggle · Theo dõi lượt chạy · read-only
              </span>
              <span>Schema v{schemaVersion ?? "—"}</span>
              <span className="ml-auto">Dữ liệu thực · Không thay dữ liệu thiếu bằng số liệu mẫu</span>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}
