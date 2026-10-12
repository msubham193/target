import type { ButtonHTMLAttributes, ReactNode } from "react";

import { ApiError } from "../api/client";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const styles =
    variant === "primary"
      ? "bg-slate-900 text-white hover:bg-slate-700"
      : "border border-slate-300 bg-white text-slate-800 hover:bg-slate-100";
  return (
    <button
      type="button"
      {...props}
      className={`rounded px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    />
  );
}

export const inputClass =
  "w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm focus-visible:outline-2 focus-visible:outline-blue-600 disabled:bg-slate-100 disabled:text-slate-500";

export function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-slate-600">{hint}</p> : null}
    </div>
  );
}

const tones = {
  info: "border-slate-300 bg-white text-slate-700",
  warning: "border-amber-400 bg-amber-50 text-amber-900",
  error: "border-red-400 bg-red-50 text-red-900",
};

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: keyof typeof tones;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded border px-3 py-2 text-sm ${tones[tone]}`}>
      {title ? <p className="font-semibold">{title}</p> : null}
      {children}
    </div>
  );
}

/** Shows the readable message, the stable diagnostic code and the request ID. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return (
      <Notice tone="error" title={error.message}>
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
        <p className="mt-1 text-xs">
          Code: {error.code}
          {error.requestId ? ` · Request: ${error.requestId}` : ""}
        </p>
        {error.code === "API_UNREACHABLE" ? (
          <p className="mt-1 text-xs">Start the database and the API on this machine, then retry.</p>
        ) : null}
      </Notice>
    );
  }
  return <Notice tone="error" title="Something went wrong" />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" className="text-sm text-slate-600">
      {label}
    </p>
  );
}

export function PageTitle({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-xl font-semibold">{title}</h1>
      {actions}
    </div>
  );
}
