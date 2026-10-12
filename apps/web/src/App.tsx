import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppLayout } from "./components/AppLayout";
import { useProfile } from "./lib/profile";
import { AssetDetailPage } from "./pages/AssetDetailPage";
import { AssetsPage } from "./pages/AssetsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { IngestionPage } from "./pages/IngestionPage";
import { LoginPage } from "./pages/LoginPage";
import { NewProjectPage } from "./pages/NewProjectPage";
import { NotYetPage } from "./pages/NotYetPage";
import { ProjectLayout } from "./pages/ProjectLayout";
import { ProjectOverviewPage } from "./pages/ProjectOverviewPage";
import { SettingsPage } from "./pages/SettingsPage";

function RequireProfile({ children }: { children: ReactNode }) {
  const { profile } = useProfile();
  const location = useLocation();
  if (!profile) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function NotFound() {
  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Link className="text-blue-700 underline" to="/dashboard">
        Go to the dashboard
      </Link>
    </main>
  );
}

// Routes follow the specification (V2 section 8). `/projects/:projectId` itself is an added overview page.
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireProfile>
            <AppLayout />
          </RequireProfile>
        }
      >
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/projects" element={<DashboardPage />} />
        <Route path="/projects/new" element={<NewProjectPage />} />
        <Route path="/projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectOverviewPage />} />
          <Route path="assets" element={<AssetsPage />} />
          <Route path="assets/new" element={<IngestionPage />} />
          <Route path="assets/:assetId" element={<AssetDetailPage />} />
          <Route
            path="map"
            element={
              <NotYetPage
                title="Map & Analysis"
                phase={4}
                detail="The 2D map of georeferenced layers. Assets without a valid CRS will never be placed by guesswork."
              />
            }
          />
          <Route
            path="assets/:assetId/ground"
            element={<NotYetPage title="Ground Image Analysis" phase={3} detail="Results of the ground-image pipeline." />}
          />
          <Route
            path="assets/:assetId/overhead"
            element={
              <NotYetPage title="Satellite/Aerial Analysis" phase={3} detail="Results of the overhead-raster pipeline." />
            }
          />
          <Route
            path="assets/:assetId/video"
            element={<NotYetPage title="Video Analysis" phase={3} detail="Results of the video pipeline." />}
          />
          <Route
            path="3d"
            element={
              <NotYetPage
                title="3D View"
                phase={6}
                detail="Viewing compatible terrain, mesh or point-cloud data. No 3D data can be loaded yet."
              />
            }
          />
          <Route
            path="observations"
            element={
              <NotYetPage title="Observations" phase={4} detail="Consolidated review of evidence-linked observations." />
            }
          />
          <Route
            path="reports"
            element={<NotYetPage title="Reports" phase={5} detail="Versioned, evidence-based reports and exports." />}
          />
        </Route>
        <Route path="/settings" element={<Navigate to="/settings/diagnostics" replace />} />
        <Route path="/settings/:tab" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
