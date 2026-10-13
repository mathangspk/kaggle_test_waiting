import { NavLink } from "react-router-dom";
import { Ban, Database } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function SidebarNav({
  onNavigate,
  schemaVersion,
}: {
  onNavigate?: () => void;
  schemaVersion?: number | null;
}) {
  return (
    <div className="flex h-full flex-col overflow-y-auto nice-scroll">
      <div className="flex items-center gap-3 px-4 py-5">
        <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Database className="size-5" />
        </div>
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-primary">Kaggle</div>
          <div className="text-sm font-semibold">Xác suất cấp phát theo mẫu</div>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-3" aria-label="Điều hướng chính">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "flex items-start gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )
              }
            >
              <Icon className="mt-0.5 size-4 shrink-0" />
              <span className="min-w-0">
                <span className="block font-medium">{item.label}</span>
                <span className="block text-xs text-muted-foreground">{item.description}</span>
              </span>
            </NavLink>
          );
        })}
      </nav>
      <div className="space-y-2 border-t border-border px-4 py-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <Ban className="size-3.5" />
          Giao diện chỉ đọc
        </div>
         <div className="tabular-nums">Schema v{schemaVersion ?? "—"} · read-only</div>
        <div>Theo tài khoản · không đo GPU rảnh toàn hệ thống</div>
      </div>
    </div>
  );
}
