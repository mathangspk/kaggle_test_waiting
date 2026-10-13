import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { LoadingBlock } from "@/components/shared/data-state";

const OverviewPage = lazy(() => import("@/pages/overview"));
const HoursPage = lazy(() => import("@/pages/hours"));
const RunsPage = lazy(() => import("@/pages/runs"));
const RoadmapPage = lazy(() => import("@/pages/roadmap"));
const LogicPage = lazy(() => import("@/pages/logic"));
const OperationsPage = lazy(() => import("@/pages/operations"));

export default function App() {
  return (
    <Suspense fallback={<LoadingBlock className="min-h-dvh rounded-none border-0" />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="hours" element={<HoursPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="roadmap" element={<RoadmapPage />} />
          <Route path="logic" element={<LogicPage />} />
          <Route path="operations" element={<OperationsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}