import type { ReactNode } from "react";

/**
 * The three-pane layout of the analysis screens: a list on the left, the main view in the middle, details on the
 * right. Side panes stack below the main view on narrow screens. A real map or 3D engine can be placed in `main`
 * later without rebuilding the screen.
 */
export function Workspace({
  left,
  main,
  right,
  leftLabel,
  rightLabel,
}: {
  left?: ReactNode;
  main: ReactNode;
  right?: ReactNode;
  leftLabel?: string;
  rightLabel?: string;
}) {
  const columns =
    left && right
      ? "xl:grid-cols-[14rem_minmax(0,1fr)_24rem] lg:grid-cols-[14rem_minmax(0,1fr)]"
      : left
        ? "lg:grid-cols-[15rem_minmax(0,1fr)]"
        : right
          ? "lg:grid-cols-[minmax(0,1fr)_24rem]"
          : "";
  return (
    <div className={`grid grid-cols-1 items-start gap-4 ${columns}`}>
      {left ? (
        <aside aria-label={leftLabel} className="order-2 min-w-0 lg:order-none">
          {left}
        </aside>
      ) : null}
      <div className="order-1 min-w-0 lg:order-none">{main}</div>
      {right ? (
        <aside aria-label={rightLabel} className={`order-3 min-w-0 ${left ? "lg:col-span-2 xl:col-span-1" : ""}`}>
          {right}
        </aside>
      ) : null}
    </div>
  );
}

/** A selectable list of files used in the left pane of the analysis screens. */
export function FileList({
  title,
  items,
  selectedId,
  empty,
  renderItem,
}: {
  title: string;
  items: { id: string }[];
  selectedId: string | null;
  empty: ReactNode;
  renderItem: (item: { id: string }, selected: boolean) => ReactNode;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface">
      <p className="border-b border-line px-3 py-2 text-xs font-medium text-fg-muted">
        {title} <span className="text-fg-subtle">({items.length})</span>
      </p>
      {items.length === 0 ? (
        <div className="p-3 text-[13px] text-fg-muted">{empty}</div>
      ) : (
        <ul className="max-h-[60vh] overflow-y-auto p-1" aria-label={title}>
          {items.map((item) => (
            <li key={item.id}>{renderItem(item, item.id === selectedId)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
