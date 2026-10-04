// Polling a background job. A job is polled every two seconds while queued or running; a failed
// request is retried with backoff instead of ending the poll, and only a run of failures is shown.

/** Interval between polls of a queued or running job. */
export const JOB_POLL_INTERVAL_MS = 2000;
/** Consecutive failed requests before the progress shows an error. Polling carries on regardless. */
export const JOB_POLL_FAILURES_BEFORE_ERROR = 5;
const MAX_RETRY_MS = 30_000;

/** Delay before retrying after `failures` consecutive failed requests (1 or more): 2 s, 4 s, 8 s, … capped at 30 s. */
export function jobRetryDelay(failures: number): number {
  return Math.min(MAX_RETRY_MS, JOB_POLL_INTERVAL_MS * 2 ** Math.max(0, failures - 1));
}

/** Whether a run of failures is long enough to show. */
export function shouldShowPollError(failures: number): boolean {
  return failures >= JOB_POLL_FAILURES_BEFORE_ERROR;
}
