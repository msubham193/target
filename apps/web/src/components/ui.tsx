/**
 * The design system: every screen is built from these pieces, so spacing, type, colour and states stay consistent.
 * Colours come from the tokens in index.css. No component library is used.
 */
import {
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from "react";
import { Link, type LinkProps } from "react-router-dom";

import { ApiError } from "../api/client";
import { IconAlert, IconCheck, IconClock, IconClose, IconInfo } from "./icons";

// --- buttons ---------------------------------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  // disabled: grey rather than a dimmed blue, so it does not read as the action to take
  primary:
    "bg-accent text-accent-fg hover:bg-accent-strong font-medium shadow-[inset_0_1px_0_rgb(255_255_255/0.18),0_4px_14px_rgb(91_155_255/0.22)] disabled:bg-overlay disabled:text-fg-muted disabled:shadow-none",
  secondary: "border border-line-strong bg-raised text-fg hover:bg-overlay",
  ghost: "text-fg-muted hover:bg-overlay hover:text-fg",
  danger: "border border-danger/50 bg-danger-soft text-danger hover:bg-danger/20",
};
const SIZES: Record<Size, string> = { sm: "h-7 px-2.5 text-xs gap-1.5", md: "h-8.5 px-3 text-sm gap-2" };

export function buttonClass(variant: Variant = "primary", size: Size = "md", extra = ""): string {
  return (
    `inline-flex shrink-0 items-center justify-center rounded-lg whitespace-nowrap transition-all duration-150 active:scale-[0.98] ` +
    `disabled:cursor-not-allowed disabled:opacity-45 ${VARIANTS[variant]} ${SIZES[size]} ${extra}`
  );
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return <button type="button" {...props} className={buttonClass(variant, size, className)} />;
}

export function LinkButton({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: LinkProps & { variant?: Variant; size?: Size }) {
  return <Link {...props} className={buttonClass(variant, size, className)} />;
}

/** An icon-only button. `label` is required: it is the accessible name and the tooltip. */
export function IconButton({
  label,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className={`inline-flex size-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-overlay hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
    >
      {children}
    </button>
  );
}

export function TextLink({ className = "", ...props }: LinkProps) {
  return <Link {...props} className={`text-accent underline-offset-2 hover:underline ${className}`} />;
}

export function ExternalishLink({ className = "", ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <a {...props} className={`text-accent underline-offset-2 hover:underline ${className}`} />;
}

// --- form controls ----------------------------------------------------------------------------------------

export const inputClass =
  "block w-full rounded-lg border border-line-strong bg-raised px-3 py-1.5 text-sm text-fg placeholder:text-fg-subtle " +
  "transition-colors hover:border-fg-subtle focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:bg-surface disabled:text-fg-subtle";

export const selectClass = `${inputClass} pr-8`;

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[13px] font-medium text-fg">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-fg-subtle">{hint}</p>
      ) : null}
    </div>
  );
}

// --- surfaces ----------------------------------------------------------------------------------------------

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
  padded = true,
  as: Tag = "section",
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
  as?: "section" | "div" | "aside";
}) {
  const headingId = useId();
  return (
    <Tag
      className={`rounded-xl border border-line/80 bg-surface shadow-[0_1px_0_rgb(255_255_255/0.03)] ${className}`}
      aria-labelledby={title ? headingId : undefined}
    >
      {title || actions ? (
        <div className="flex flex-wrap items-start justify-between gap-2 border-b border-line/70 px-4 py-3.5">
          <div className="min-w-0">
            {title ? (
              <h2 id={headingId} className="text-sm font-semibold text-fg">
                {title}
              </h2>
            ) : null}
            {description ? <p className="mt-0.5 text-xs text-fg-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </Tag>
  );
}

/** The heading block at the top of a screen: title, one line of purpose, and the screen's main actions. */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="rise-in relative mb-6 flex flex-wrap items-end justify-between gap-3 pb-4 after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-gradient-to-r after:from-accent/40 after:via-line after:to-transparent">
      <div className="min-w-0">
        <h1 className="wrap-anywhere text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-fg-muted">{description}</p> : null}
        {meta ? <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-subtle">{meta}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Kept for screens that only need a title line. */
export function PageTitle({ title, actions }: { title: string; actions?: ReactNode }) {
  return <PageHeader title={title} actions={actions} />;
}

// --- status ------------------------------------------------------------------------------------------------

export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "planned";

const BADGE_TONES: Record<Tone, string> = {
  neutral: "bg-overlay text-fg-muted",
  accent: "bg-accent-soft text-accent",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  planned: "border border-dashed border-line-strong text-fg-subtle",
};

export function Badge({
  tone = "neutral",
  size = "sm",
  children,
  className = "",
}: {
  tone?: Tone;
  /** "sm" (11 px) is the default everywhere; "md" (12 px) is for badges that carry technical information. */
  size?: "sm" | "md";
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${size === "md" ? "text-xs" : "text-[11px]"} font-medium whitespace-nowrap ${BADGE_TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

/** Marks something that is designed but not built. Never used for something that works. */
export function PlannedBadge({ label = "Planned" }: { label?: string }) {
  return <Badge tone="planned">{label}</Badge>;
}

const NOTICE_TONES = {
  info: { box: "border-line bg-raised text-fg-muted", icon: IconInfo, title: "text-fg" },
  warning: { box: "border-warn/35 bg-warn-soft text-fg-muted", icon: IconAlert, title: "text-warn" },
  error: { box: "border-danger/40 bg-danger-soft text-fg-muted", icon: IconAlert, title: "text-danger" },
  success: { box: "border-ok/35 bg-ok-soft text-fg-muted", icon: IconCheck, title: "text-ok" },
};

export function Notice({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: keyof typeof NOTICE_TONES;
  title?: string;
  children?: ReactNode;
  className?: string;
}) {
  const style = NOTICE_TONES[tone];
  const Icon = style.icon;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex gap-2.5 rounded-xl border px-3.5 py-3 text-[13px] ${style.box} ${className}`}
    >
      <Icon size={16} className={`mt-0.5 shrink-0 ${style.title}`} />
      <div className="min-w-0 space-y-0.5">
        {title ? <p className={`font-medium ${style.title}`}>{title}</p> : null}
        {children}
      </div>
    </div>
  );
}

/** Shows the readable message, the stable diagnostic code and the request ID of an API failure. */
export function ErrorNotice({ error, title }: { error: unknown; title?: string }) {
  if (error instanceof ApiError) {
    return (
      <Notice tone="error" title={title ?? error.message}>
        {title ? <p>{error.message}</p> : null}
        {error.detail ? <p>{error.detail}</p> : null}
        {error.fieldErrors.length > 0 ? (
          <ul className="list-disc pl-5">
            {error.fieldErrors.map((item, index) => (
              <li key={index}>
                {item.field ? `${item.field}: ` : ""}
                {item.message}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="font-mono text-[11px] text-fg-subtle">
          Code: {error.code}
          {error.requestId ? ` · Request: ${error.requestId}` : ""}
        </p>
        {error.code === "API_UNREACHABLE" ? (
          <p className="text-xs">Start the database and the API on this machine (scripts\dev.ps1), then retry.</p>
        ) : null}
      </Notice>
    );
  }
  return <Notice tone="error" title="Something went wrong" />;
}

export function Loading({ label = "Loading…", className = "" }: { label?: string; className?: string }) {
  return (
    <p role="status" className={`flex items-center gap-2 text-sm text-fg-muted ${className}`}>
      <span className="size-3.5 animate-spin rounded-full border-2 border-line-strong border-t-accent" aria-hidden="true" />
      {label}
    </p>
  );
}

export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-overlay ${className}`} aria-hidden="true" />;
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className = "",
  pageTitle = false,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
  /** The empty state is the whole page (e.g. not found): its title is the page heading. */
  pageTitle?: boolean;
}) {
  const Title = pageTitle ? "h1" : "p";
  return (
    <div role="status" className={`flex flex-col items-center px-6 py-10 text-center ${className}`}>
      {icon ? <div className="mb-3 flex size-14 items-center justify-center rounded-full bg-raised text-fg-subtle ring-1 ring-line/70">{icon}</div> : null}
      <Title className="text-sm font-medium text-fg">{title}</Title>
      {children ? <div className="mt-1 max-w-md text-[13px] text-fg-muted">{children}</div> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/**
 * A region reserved for a feature that is designed but not built. It says so plainly, and it draws nothing that
 * could be mistaken for real output: no tiles, no terrain, no detections.
 */
export function PlannedArea({
  icon,
  title,
  children,
  className = "",
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={`flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-canvas px-6 py-12 text-center ${className}`}
    >
      {icon ? <div className="mb-3 text-fg-subtle">{icon}</div> : null}
      <PlannedBadge />
      <p className="mt-2 text-sm font-medium text-fg">{title}</p>
      {children ? <div className="mt-1 max-w-md text-[13px] text-fg-muted">{children}</div> : null}
    </div>
  );
}

// --- data display ----------------------------------------------------------------------------------------

export function DescriptionList({
  rows,
  labelWidth = "sm",
}: {
  rows: [string, ReactNode][];
  labelWidth?: "sm" | "md";
}) {
  const columns =
    labelWidth === "md"
      ? "grid-cols-[8.5rem_minmax(0,1fr)] sm:grid-cols-[12rem_minmax(0,1fr)]"
      : "grid-cols-[7.5rem_minmax(0,1fr)]";
  return (
    <dl className={`grid ${columns} gap-x-4 gap-y-1.5 text-[13px]`}>
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-fg-subtle">{label}</dt>
          <dd className="wrap-anywhere text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export const muted = <span className="text-fg-subtle">unknown</span>;
export const notAvailable = <span className="text-fg-subtle">not available</span>;

export const tableClass = "w-full border-collapse text-left text-[13px]";
export const thClass = "border-b border-line bg-raised px-3 py-2 text-xs font-medium text-fg-muted whitespace-nowrap";
export const tdClass = "border-b border-line px-3 py-2 align-top";

/** A horizontally scrollable frame for tables, so a wide table never widens the page. */
export function TableFrame({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface" role={label ? "region" : undefined} aria-label={label} tabIndex={label ? 0 : undefined}>
      {children}
    </div>
  );
}

// --- navigation ------------------------------------------------------------------------------------------

export function Breadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-fg-subtle">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="flex min-w-0 items-center gap-1">
            {index > 0 ? <span aria-hidden="true">/</span> : null}
            {item.to && index < items.length - 1 ? (
              <Link className="truncate hover:text-fg" to={item.to}>
                {item.label}
              </Link>
            ) : (
              <span className="truncate text-fg-muted" aria-current={index === items.length - 1 ? "page" : undefined}>
                {item.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Tabs that switch content in place. Arrow keys move between tabs, as for any tab list. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    onChange(tabs[next]!.value);
    refs.current[next]?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-line">
      {tabs.map((tab, index) => {
        const selected = tab.value === value;
        return (
          <button
            key={tab.value}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight") move(index + 1);
              if (event.key === "ArrowLeft") move(index - 1);
            }}
            className={`-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors ${
              selected ? "border-accent font-medium text-fg" : "border-transparent text-fg-muted hover:text-fg"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

/** A short explanation attached to its trigger by aria-describedby; shown on hover and on keyboard focus. */
export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  const id = useId();
  return (
    <span className="group relative inline-flex" aria-describedby={id}>
      {children}
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-64 -translate-x-1/2 rounded-md border border-line bg-overlay px-2 py-1 text-xs text-fg opacity-0 shadow-panel transition-opacity group-focus-within:opacity-100 group-hover:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

/** A modal dialog built on the native <dialog> element: focus is contained and Escape closes it. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal?.();
    if (!open && dialog.open) dialog.close?.();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={title}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-line bg-surface p-0 text-fg shadow-panel backdrop:bg-black/60"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <IconButton label="Close" onClick={onClose}>
          <IconClose />
        </IconButton>
      </div>
      <div className="px-4 py-3 text-[13px] text-fg-muted">{children}</div>
      {footer ? <div className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</div> : null}
    </dialog>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-sm border border-line-strong bg-raised px-1 font-mono text-[11px] text-fg-muted">{children}</kbd>
  );
}

export function WaitingIcon() {
  return <IconClock size={14} className="text-fg-subtle" />;
}
