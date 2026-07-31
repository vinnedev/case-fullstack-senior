import type { JobStatus } from "./types";

const ACTIVE_JOB_STATUSES = new Set<JobStatus>(["queued", "running"]);

export const jobQueryKeys = {
  list: (auth: string) => ["jobs", auth] as const,
  page: (auth: string, page: number, pageSize: number, status: JobStatus | null) =>
    ["jobs", auth, page, pageSize, status] as const,
  detail: (auth: string, jobId: number) => ["job", auth, jobId] as const,
  result: (auth: string, jobId: number) => ["job-result", auth, jobId] as const,
};

export type JobMutationCacheKey =
  | ReturnType<typeof jobQueryKeys.list>
  | ReturnType<typeof jobQueryKeys.detail>
  | ReturnType<typeof jobQueryKeys.result>;

export type JobCacheInvalidator = (queryKey: JobMutationCacheKey) => void | Promise<void>;

export type JobMutationVariables = Readonly<{
  auth: string;
  jobId: number;
}>;

export type CompanyJobsScope = Readonly<{
  auth: string;
  companyId: number;
  status: JobStatus | null;
}>;

export function shouldPollActiveJobs(status: JobStatus | undefined): boolean {
  return status !== undefined && ACTIVE_JOB_STATUSES.has(status);
}

export function isPaginationNeeded(page: number, pageSize: number, total: number): boolean {
  return page > 0 || total > pageSize;
}

export function clampPage(page: number, pageSize: number, total: number): number {
  return Math.min(page, Math.max(0, Math.ceil(total / pageSize) - 1));
}

export function hasCompanyJobsScopeChanged(previous: CompanyJobsScope, next: CompanyJobsScope): boolean {
  return previous.auth !== next.auth || previous.companyId !== next.companyId || previous.status !== next.status;
}

export function getQueryErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  const status = (error as { status?: unknown }).status;
  if (typeof status !== "number" || status < 400 || status > 599 || error.message.trim().length === 0) return fallback;
  return error.message;
}

export function getJobMutationInvalidationKeys({ auth, jobId }: JobMutationVariables): readonly JobMutationCacheKey[] {
  return [jobQueryKeys.list(auth), jobQueryKeys.detail(auth, jobId), jobQueryKeys.result(auth, jobId)];
}

export async function invalidateJobMutationCaches(
  invalidate: JobCacheInvalidator,
  variables: JobMutationVariables,
): Promise<void> {
  await Promise.all(getJobMutationInvalidationKeys(variables).map(invalidate));
}
