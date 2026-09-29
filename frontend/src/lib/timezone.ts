// Converts between a wall-clock time in an IANA timezone and UTC without a
// date library. Deadlines are entered in the tender's timezone and stored in UTC.

function offsetMs(utcMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(utcMs));
  const v = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return Date.UTC(+v.year, +v.month - 1, +v.day, +v.hour, +v.minute, +v.second) - utcMs;
}

/** "2026-10-09" + "12:00" in Europe/London -> the UTC instant. */
export function zonedToUtc(date: string, time: string, timeZone: string): Date {
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = (time || '12:00').split(':').map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  let utc = wall - offsetMs(wall, timeZone);
  const second = offsetMs(utc, timeZone);
  if (wall - second !== utc) utc = wall - second;
  return new Date(utc);
}

/** The UTC instant shown as date and time strings in the given timezone. */
export function utcToZoned(value: Date | string, timeZone: string): { date: string; time: string } {
  const ms = new Date(value).getTime();
  const local = new Date(ms + offsetMs(ms, timeZone));
  return { date: local.toISOString().slice(0, 10), time: local.toISOString().slice(11, 16) };
}

export const COMMON_TIMEZONES = [
  'Europe/London',
  'Europe/Dublin',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Amsterdam',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Asia/Singapore',
  'Australia/Sydney',
  'UTC',
];
