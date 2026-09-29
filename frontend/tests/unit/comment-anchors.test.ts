import { describe, expect, it } from 'vitest';
import { highlightRanges, quoteFor, relocate } from '@/lib/comment-anchors';

const v1 = [
  { index: 0, text: 'We hold ISO 27001 certification.' },
  { index: 1, text: 'Our clinical safety officer reviews the case monthly.' },
  { index: 2, text: 'Support runs from 8am to 6pm.' },
];

describe('comment anchors', () => {
  it('quotes a span inside one sentence and across sentences', () => {
    expect(quoteFor(v1, { start: { segment: 1, offset: 4 }, end: { segment: 1, offset: 27 } })).toBe('clinical safety officer');
    expect(quoteFor(v1, { start: { segment: 0, offset: 27 }, end: { segment: 1, offset: 3 } })).toBe('tion. Our');
    expect(quoteFor(v1, { start: { segment: 3, offset: 0 }, end: { segment: 3, offset: 1 } })).toBeNull();
    expect(quoteFor(v1, { start: { segment: 1, offset: 5 }, end: { segment: 1, offset: 5 } })).toBeNull();
  });

  it('carries a span into a new version when the words survive, and reports outdated when they do not', () => {
    const anchor = { start: { segment: 1, offset: 4 }, end: { segment: 1, offset: 27 } };
    const v2 = [{ index: 0, text: 'Intro sentence added first.' }, ...v1.map((s) => ({ ...s, index: s.index + 1 }))];
    const moved = relocate(v1, anchor, v2)!;
    expect(moved).toEqual({ start: { segment: 2, offset: 4 }, end: { segment: 2, offset: 27 } });
    expect(quoteFor(v2, moved)).toBe('clinical safety officer');
    const v3 = [{ index: 0, text: 'Our named lead reviews the case monthly.' }];
    expect(relocate(v1, anchor, v3)).toBeNull();
  });

  it('prefers the occurrence nearest the original position', () => {
    const from = [{ index: 0, text: 'monthly reviews and monthly audits' }];
    const anchor = { start: { segment: 0, offset: 20 }, end: { segment: 0, offset: 27 } };
    const to = [{ index: 0, text: 'monthly reviews and monthly audits, updated' }];
    expect(relocate(from, anchor, to)).toEqual({ start: { segment: 0, offset: 20 }, end: { segment: 0, offset: 27 } });
  });

  it('splits a multi-sentence span into per-sentence highlight ranges', () => {
    const ranges = highlightRanges(v1, { start: { segment: 0, offset: 8 }, end: { segment: 2, offset: 7 } }, 't1');
    expect(ranges).toEqual([
      { segment: 0, start: 8, end: v1[0].text.length, threadId: 't1' },
      { segment: 1, start: 0, end: v1[1].text.length, threadId: 't1' },
      { segment: 2, start: 0, end: 7, threadId: 't1' },
    ]);
  });
});
