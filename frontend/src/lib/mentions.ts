/**
 * @mentions are written as "@Display Name" in comment text. The same matching is used to
 * highlight mentions in the browser and to decide who is notified on the server, so what
 * a person sees tagged is exactly who hears about it.
 */
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function pattern(names: string[]): RegExp | null {
  // Longest names first, so "@Sam Jones" wins over "@Sam".
  const alternatives = [...new Set(names.filter(Boolean))].sort((a, b) => b.length - a.length).map(escape);
  if (!alternatives.length) return null;
  return new RegExp(`(^|[^\\p{L}\\p{N}_])@(${alternatives.join('|')})(?![\\p{L}\\p{N}_])`, 'giu');
}

/** The display names mentioned in the text, in the members' own spelling. */
export function mentionedNames(text: string, names: string[]): string[] {
  const re = pattern(names);
  if (!re) return [];
  const found = new Set<string>();
  for (const match of text.matchAll(re)) {
    const name = names.find((n) => n.toLocaleLowerCase() === match[2].toLocaleLowerCase());
    if (name) found.add(name);
  }
  return [...found];
}

/** Splits text into plain runs and mentions, for rendering mentions as tags. */
export function splitMentions(text: string, names: string[]): Array<{ text: string; mention: boolean }> {
  const re = pattern(names);
  if (!re) return [{ text, mention: false }];
  const parts: Array<{ text: string; mention: boolean }> = [];
  let last = 0;
  for (const match of text.matchAll(re)) {
    const start = match.index + match[1].length;
    if (start > last) parts.push({ text: text.slice(last, start), mention: false });
    parts.push({ text: `@${match[2]}`, mention: true });
    last = start + 1 + match[2].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), mention: false });
  return parts;
}

/** The partial mention being typed just before the caret, if any. */
export function activeMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at === -1) return null;
  if (at > 0 && /[\p{L}\p{N}_]/u.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (query.length > 40 || /[\n@]/.test(query)) return null;
  return { start: at, query };
}
