import { parseJobs } from "./types";
import type { JobsPage } from "./types";

export const API: string = import.meta.env.VITE_API_URL;

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function parseError(r: Response): Promise<ApiError> {
  const fallback = `Erro ${r.status}`;
  try {
    const body: unknown = await r.json();
    const detail = typeof body === "object" && body !== null && "detail" in body ? (body as { detail: unknown }).detail : null;
    return new ApiError(r.status, typeof detail === "string" ? detail : fallback);
  } catch {
    return new ApiError(r.status, fallback);
  }
}

export async function get<T>(path: string, auth: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${API}${path}`, { headers: { "X-Auth": auth }, signal });
  if (!r.ok) throw await parseError(r);
  return parse(await r.json());
}

export async function post<T>(
  path: string,
  auth: string,
  parse: (value: unknown) => T,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "X-Auth": auth, "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw await parseError(r);
  return parse(await r.json());
}

export type Page<T> = { items: T[]; total: number };

export async function fetchPage<T>(
  path: string,
  auth: string,
  parseItems: (value: unknown) => T[],
  signal?: AbortSignal,
): Promise<Page<T>> {
  const r = await fetch(`${API}${path}`, { headers: { "X-Auth": auth }, signal });
  if (!r.ok) throw await parseError(r);
  const items = parseItems(await r.json());
  const total = Number(r.headers.get("X-Total-Count") ?? items.length);
  if (!Number.isSafeInteger(total) || total < 0) throw new TypeError("Header inválido: X-Total-Count");
  return { items, total };
}

export async function fetchJobsPage(auth: string, params: URLSearchParams, signal?: AbortSignal): Promise<JobsPage> {
  const { items, total } = await fetchPage(`/jobs?${params.toString()}`, auth, parseJobs, signal);
  return { jobs: items, total };
}
