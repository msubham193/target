import createClient from "openapi-fetch";

import { loadProfile } from "../lib/profile";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Project = Schemas["ProjectOut"];
export type ProjectPage = Schemas["ProjectPage"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export type Profile = Schemas["ProfileOut"];
export type ProfileList = Schemas["ProfileList"];
export type Crs = Schemas["CrsOut"];
export type CrsSearchResult = Schemas["CrsSearchResult"];
export type Health = Schemas["HealthOut"];
export type ModelHealth = Schemas["ModelHealthOut"];

// RFC 9457 problem body returned for every API error.
export interface Problem {
  title: string;
  status: number;
  detail?: string | null;
  code: string;
  request_id?: string | null;
  errors?: { field?: string; message?: string }[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    title: string,
    readonly detail?: string | null,
    readonly requestId?: string | null,
    readonly fieldErrors: { field?: string; message?: string }[] = [],
  ) {
    super(title);
  }
}

const client = createClient<paths>({ baseUrl: globalThis.location?.origin ?? "http://127.0.0.1:5173" });

client.use({
  onRequest({ request }) {
    const profile = loadProfile();
    if (profile) request.headers.set("X-Workspace-Profile", profile.id);
    return request;
  },
});

function toApiError(error: unknown, response: Response): ApiError {
  const problem = (error ?? {}) as Partial<Problem>;
  return new ApiError(
    response.status,
    problem.code ?? "UNKNOWN_ERROR",
    problem.title ?? `Request failed (${response.status})`,
    problem.detail,
    problem.request_id ?? response.headers.get("X-Request-ID"),
    problem.errors ?? [],
  );
}

async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result;
  try {
    result = await call;
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  if (result.error !== undefined || result.data === undefined) throw toApiError(result.error, result.response);
  return result.data;
}

// /health answers 503 with the same body shape when a dependency is down, so read it without unwrap.
export async function getHealth(): Promise<Health> {
  let response: Response;
  try {
    response = await fetch("/health");
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === "object" && "status" in body) return body as Health;
  throw new ApiError(response.status, "API_UNREACHABLE", "The local API is not reachable");
}

export const getModelHealth = () => unwrap(client.GET("/health/models"));

export const listProfiles = () => unwrap(client.GET("/api/v1/workspace/profiles"));

export const createProfile = (displayName: string) =>
  unwrap(client.POST("/api/v1/workspace/profiles", { body: { display_name: displayName } }));

export const listProjects = (query: {
  q?: string;
  tag?: string;
  archived?: boolean;
  limit?: number;
  cursor?: string;
}) => unwrap(client.GET("/api/v1/projects", { params: { query } }));

export const getProject = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } } }));

export const createProject = (body: ProjectCreate) => unwrap(client.POST("/api/v1/projects", { body }));

export const updateProject = (projectId: string, body: ProjectUpdate) =>
  unwrap(client.PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } }, body }));

export const searchCrs = (q: string, limit = 10) =>
  unwrap(client.GET("/api/v1/crs", { params: { query: { q, limit } } }));
