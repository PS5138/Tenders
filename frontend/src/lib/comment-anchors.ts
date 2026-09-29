/**
 * Comment anchors point into one answer version: a segment index and a UTF-16 offset into that
 * segment's text, for the start and (exclusive) end of the commented span. Nothing here stores
 * answer text; quotes are always read from the version the anchor belongs to.
 */
export type AnchorPoint = { segment: number; offset: number };
export type Anchor = { start: AnchorPoint; end: AnchorPoint };
type Seg = { index: number; text: string };

// Sentences are joined with a separator no sentence contains, so a found quote maps back unambiguously.
const JOIN = '\n';

function flatten(segments: Seg[]) {
  const ordered = [...segments].sort((a, b) => a.index - b.index);
  const starts: number[] = [];
  let position = 0;
  for (const s of ordered) {
    starts.push(position);
    position += s.text.length + JOIN.length;
  }
  return { ordered, starts, text: ordered.map((s) => s.text).join(JOIN) };
}

function valid(anchor: Anchor, ordered: Seg[]): boolean {
  const ok = (p: AnchorPoint) => p.segment >= 0 && p.segment < ordered.length && p.offset >= 0 && p.offset <= ordered[p.segment].text.length;
  const { start, end } = anchor;
  return ok(start) && ok(end) && (start.segment < end.segment || (start.segment === end.segment && start.offset < end.offset));
}

/** The commented words, or null when the anchor no longer fits these segments. */
export function quoteFor(segments: Seg[], anchor: Anchor): string | null {
  const { ordered, starts, text } = flatten(segments);
  if (!valid(anchor, ordered)) return null;
  return text.slice(starts[anchor.start.segment] + anchor.start.offset, starts[anchor.end.segment] + anchor.end.offset).replaceAll(JOIN, ' ');
}

/**
 * Carries an anchor from the version it was made on into a newer one. The quoted words must appear
 * in the newer version; the occurrence nearest the old position wins. Null means the text changed.
 */
export function relocate(from: Seg[], anchor: Anchor, to: Seg[]): Anchor | null {
  const old = flatten(from);
  if (!valid(anchor, old.ordered)) return null;
  const startPos = old.starts[anchor.start.segment] + anchor.start.offset;
  const quote = old.text.slice(startPos, old.starts[anchor.end.segment] + anchor.end.offset);
  const next = flatten(to);
  let best = -1;
  for (let at = next.text.indexOf(quote); at !== -1; at = next.text.indexOf(quote, at + 1))
    if (best === -1 || Math.abs(at - startPos) < Math.abs(best - startPos)) best = at;
  if (best === -1 || !quote.trim()) return null;
  const point = (position: number, end: boolean): AnchorPoint => {
    for (let i = next.starts.length - 1; i >= 0; i--) {
      const offset = position - next.starts[i];
      if (offset >= 0 && (offset < next.ordered[i].text.length || (end && offset === next.ordered[i].text.length)))
        return { segment: i, offset };
    }
    return { segment: 0, offset: 0 };
  };
  return { start: point(best, false), end: point(best + quote.length, true) };
}

/** Per-sentence ranges for rendering a highlight. */
export function highlightRanges(segments: Seg[], anchor: Anchor, threadId: string) {
  const { ordered } = flatten(segments);
  if (!valid(anchor, ordered)) return [];
  const ranges: { segment: number; start: number; end: number; threadId: string }[] = [];
  for (let i = anchor.start.segment; i <= anchor.end.segment; i++) {
    const start = i === anchor.start.segment ? anchor.start.offset : 0;
    const end = i === anchor.end.segment ? anchor.end.offset : ordered[i].text.length;
    if (end > start) ranges.push({ segment: ordered[i].index, start, end, threadId });
  }
  return ranges;
}
