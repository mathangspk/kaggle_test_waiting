import {
  Activity,
  Clock3,
  LayoutDashboard,
  ListChecks,
  Network,
  Route,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  description: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Tổng quan", description: "Tình trạng chung và tài khoản", icon: LayoutDashboard },
  { to: "/hours", label: "Khung giờ", description: "Xác suất cấp phát theo mẫu tài khoản", icon: Clock3 },
  { to: "/runs", label: "Lượt chạy", description: "Danh sách và bộ lọc", icon: ListChecks },
  { to: "/roadmap", label: "Lộ trình", description: "Issue, phụ thuộc, chặn", icon: Route },
  { to: "/logic", label: "Sơ đồ logic", description: "Máy trạng thái read-only", icon: Network },
  { to: "/operations", label: "Vận hành", description: "Health và định nghĩa chỉ số", icon: Activity },
];
