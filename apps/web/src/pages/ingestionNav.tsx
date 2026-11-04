import { NavLink } from "react-router-dom";

/** Switches between the parts of Add Files: adding files, adding map sheets, and the files already added. */
export function IngestionNav({ projectId, count }: { projectId: string; count?: number }) {
  const item = ({ isActive }: { isActive: boolean }) =>
    `-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors ${
      isActive ? "border-accent font-medium text-fg" : "border-transparent text-fg-muted hover:text-fg"
    }`;
  return (
    <nav aria-label="Ingestion" className="mb-4 flex gap-1 border-b border-line">
      <NavLink to={`/projects/${projectId}/assets/new`} end className={item}>
        Upload files
      </NavLink>
      <NavLink to={`/projects/${projectId}/assets/map-sheets`} end className={item}>
        Map sheets
      </NavLink>
      <NavLink to={`/projects/${projectId}/assets`} end className={item}>
        Assets{count !== undefined ? ` (${count})` : ""}
      </NavLink>
    </nav>
  );
}
