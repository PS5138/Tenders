/**
 * Structured domain errors. Every code carries a user-facing explanation so
 * route handlers can return it unchanged.
 */
export const ERROR_CODES = {
  UNAUTHENTICATED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  VALIDATION_FAILED: 400,
  CONFLICT: 409,
  REVISION_CONFLICT: 409,
  OUTDATED_JOB: 409,
  STALE_REVISION: 409,
  EVIDENCE_NOT_REVIEWED: 422,
  REVIEWER_REQUIRED: 422,
  APPROVAL_BLOCKED: 422,
  EXPORT_BLOCKED: 422,
  DOCUMENT_UNSUPPORTED: 415,
  FILE_TOO_LARGE: 413,
  AI_UNAVAILABLE: 503,
  LIVE_AI_REQUIRED: 503,
  RATE_LIMITED: 429,
  INTERNAL: 500,
} as const;

export type ErrorCode = keyof typeof ERROR_CODES;

export class AppError extends Error {
  readonly code: ErrorCode;
  readonly details?: unknown;

  constructor(code: ErrorCode, message: string, details?: unknown) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.details = details;
  }

  get status(): number {
    return ERROR_CODES[this.code];
  }
}

export const notFound = (what = 'That item') => new AppError('NOT_FOUND', `${what} was not found or you do not have access to it.`);
export const forbidden = (message = 'You do not have permission to do that.') => new AppError('FORBIDDEN', message);
export const invalid = (message: string, details?: unknown) => new AppError('VALIDATION_FAILED', message, details);
