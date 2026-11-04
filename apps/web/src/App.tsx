import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { EmptyState, LinkButton } from "./components/ui";
import { useSignedIn } from "./lib/auth";
import { useProfile } from "./lib/profile";
import { AssetDetailPage } from "./pages/AssetDetailPage";
import { AssetsPage } from "./pages/AssetsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { GroundAnalysisPage } from "./pages/GroundAnalysisPage";
import { IngestionPage } from "./pages/IngestionPage";
import { LoginPage } from "./pages/LoginPage";
import { MapPage } from "./pages/MapPage";
import { MapSheetsPage } from "./pages/MapSheetsPage";
import { NewProjectPage } from "./pages/NewProjectPage";
import { ObservationsPage } from "./pages/ObservationsPage";
import { ProjectLayout } from "./pages/ProjectLayout";
import { ProjectOverviewPage } from "./pages/ProjectOverviewPage";
import { FindingsReportPage } from "./pages/FindingsReportPage";
import { PhotoWorkspacePage } from "./pages/PhotoWorkspacePage";
import { ReportsPage } from "./pages/ReportsPage";
import { SatellitePage } from "./pages/SatellitePage";
import { SettingsPage } from "./pages/SettingsPage";
import { ThreeDPage } from "./pages/ThreeDPage";
import { VideoPage } from "./pages/VideoPage";

function RequireProfile({ children }: { children: ReactNode }) {
  const { profile } = useProfile();
  const signedIn = useSignedIn();
  const location = useLocation();
  if (!signedIn || !profile) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function NotFound() {
  return (
    <EmptyState
      title="Page not found"
        pageTitle
      action={
        <LinkButton to="/dashboard" variant="secondary">
          Go to the dashboard
        </LinkButton>
      }
    >
      The address does not match any screen.
    </EmptyState>
  );
}

// Routes follow the specification's suggested routes (V2 section 8), plus a screen per analysis kind.
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireProfile>
            <AppShell />
          </RequireProfile>
        }
      >
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/projects" element={<Navigate to="/dashboard" replace />} />
        <Route path="/projects/new" element={<NewProjectPage />} />
        <Route path="/projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectOverviewPage />} />
          <Route path="assets" element={<AssetsPage />} />
          <Route path="assets/new" element={<IngestionPage />} />
          <Route path="assets/map-sheets" element={<MapSheetsPage />} />
          <Route path="assets/:assetId" element={<AssetDetailPage />} />
          <Route path="map" element={<MapPage />} />
          <Route path="workspace" element={<PhotoWorkspacePage />} />
          <Route path="ground" element={<GroundAnalysisPage />} />
          <Route path="assets/:assetId/ground" element={<GroundAnalysisPage />} />
          <Route path="satellite" element={<SatellitePage />} />
          <Route path="assets/:assetId/overhead" element={<SatellitePage />} />
          <Route path="video" element={<VideoPage />} />
          <Route path="assets/:assetId/video" element={<VideoPage />} />
          <Route path="3d" element={<ThreeDPage />} />
          <Route path="observations" element={<ObservationsPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="reports/findings" element={<FindingsReportPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
        <Route path="/settings" element={<Navigate to="/settings/models" replace />} />
        <Route path="/settings/:tab" element={<SettingsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
