// Formatting helpers shared by server and client components. Dates are stored
// in UTC and shown in en-GB style; deadlines also show the tender's timezone.

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export function toDate(value: Date | string | null | undefined): Date | null {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(value: Date | string | null | undefined, timeZone = 'Europe/London'): string {
  const d = toDate(value);
  if (!d) return 'Not set';
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone }).format(d);
}

export function formatShortDate(value: Date | string | null | undefined, timeZone = 'Europe/London'): string {
  const d = toDate(value);
  if (!d) return 'Not set';
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone }).format(d);
}

export function formatDateTime(value: Date | string | null | undefined, timeZone = 'Europe/London'): string {
  const d = toDate(value);
  if (!d) return 'Not set';
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone, timeZoneName: 'short' }).format(d);
}

/** Calendar date stored as `date` (no time), e.g. source expiry. */
export function formatCalendarDate(value: Date | string | null | undefined): string {
  const d = toDate(value);
  return d ? dateFmt.format(d) : 'Not set';
}

export function relativeTime(value: Date | string | null | undefined, now = new Date()): string {
  const d = toDate(value);
  if (!d) return '';
  const seconds = Math.round((d.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat('en-GB', { numeric: 'auto' });
  if (abs < 60) return rtf.format(seconds, 'second');
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), 'day');
  return formatDate(d);
}

export function daysUntil(value: Date | string | null | undefined, now = new Date()): number | null {
  const d = toDate(value);
  if (!d) return null;
  return Math.ceil((d.getTime() - now.getTime()) / 86400000);
}

export function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function pluralise(n: number, one: string, many = one + 's'): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
