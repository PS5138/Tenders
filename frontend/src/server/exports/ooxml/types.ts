export class ExtractionError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  constructor(code: string, message: string, retryable = false) {
    super(message);
    this.name = 'ExtractionError';
    this.code = code;
    this.retryable = retryable;
  }
}

export type ExtractionLimits = {
  maxDecompressedBytes: number;
  maxZipEntries: number;
  maxPassages: number;
  maxPassageChars: number;
  timeoutMs: number;
};

export const DEFAULT_LIMITS: ExtractionLimits = {
  maxDecompressedBytes: 200 * 1024 * 1024,
  maxZipEntries: 5000,
  maxPassages: 20000,
  maxPassageChars: 1800,
  timeoutMs: 120_000,
};
