'use client';
import { useEffect, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { backendJson } from '@/lib/backend-api';
import { codePointRange, type Segment } from '@/lib/backend-stream';
import type { S } from './shared';
import { ErrorNote, label, panel } from './shared';
import { Button } from '@/components/ui/button';
import { Check, CircleAlert, Flag, LoaderCircle, PenLine, TriangleAlert, UserCheck, type LucideIcon } from 'lucide-react';
export type Source = S['DocumentSource'];
export type Highlight = { segment: number; start: number; end: number; threadId: string };

type MarkerStyle = { icon: LucideIcon | null; pill: string; text: string; meaning: string };
const MARKER: Record<string, MarkerStyle> = {
  supported: { icon: Check, pill: 'bg-tint text-accent', text: '', meaning: 'Backed by a source' },
  confirmed: { icon: UserCheck, pill: 'bg-tint text-accent', text: '', meaning: 'Confirmed accurate by a person' },
  weak: { icon: TriangleAlert, pill: 'bg-amber-bg text-amber', text: 'bg-amber-bg/60 decoration-dotted decoration-amber underline underline-offset-4', meaning: 'Weak or out-of-date source' },
  unsupported: { icon: CircleAlert, pill: 'bg-red-bg text-red', text: 'bg-red-bg/60 decoration-wavy decoration-red underline underline-offset-4', meaning: 'No supporting source' },
  disputed: { icon: Flag, pill: 'bg-red-bg text-red', text: 'bg-red-bg/60 decoration-wavy decoration-red underline underline-offset-4', meaning: 'Disputed by a reviewer' },
  human_authored: { icon: PenLine, pill: 'bg-violet-bg text-violet', text: 'bg-violet-bg/60', meaning: 'Written by a person, not yet confirmed' },
  connective: { icon: null, pill: 'bg-soft text-muted', text: '', meaning: 'Joining sentence, needs no source' },
  pending: { icon: LoaderCircle, pill: 'bg-soft text-muted', text: '', meaning: 'Checking the source' },
};

function markerKind(segment: Segment): string {
  if (segment.dispute) return 'disputed';
  if (segment.support_status === 'supported' && segment.sources?.some((s) => s.source_type === 'human_attestation')) return 'confirmed';
  return segment.support_status in MARKER ? segment.support_status : 'pending';
}

/** The key to the markers, shown above an answer. */
export function TraceLegend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-muted" aria-label="Sentence markers">
      {(['supported', 'confirmed', 'human_authored', 'weak', 'unsupported'] as const).map((key) => {
        const { icon: Icon, pill, meaning } = MARKER[key];
        return (
          <li key={key} className="flex items-center gap-1.5">
            <span className={`inline-flex size-4 items-center justify-center rounded-full ${pill}`}>{Icon ? <Icon className="size-2.5" aria-hidden /> : null}</span>
            {meaning}
          </li>
        );
      })}
    </ul>
  );
}

/** Splits one sentence into plain and commented pieces. Offsets are UTF-16 indices into the sentence. */
function pieces(text: string, ranges: Highlight[]) {
  const cuts = [...new Set([0, text.length, ...ranges.flatMap((r) => [r.start, r.end])])].filter((c) => c >= 0 && c <= text.length).sort((a, b) => a - b);
  return cuts.slice(0, -1).map((from, i) => {
    const to = cuts[i + 1];
    return { from, text: text.slice(from, to), threads: ranges.filter((r) => r.start <= from && r.end >= to).map((r) => r.threadId) };
  });
}

export function Trace({
  segments,
  onSource,
  prefix = 'answer',
  highlights = [],
  activeThread,
  onHighlight,
}: {
  segments: Segment[];
  onSource: (source: Source) => void;
  prefix?: string;
  highlights?: Highlight[];
  activeThread?: string | null;
  onHighlight?: (threadId: string) => void;
}) {
  return (
    <div className="space-y-2 text-[15px] leading-7 wrap-anywhere">
      {segments.map((segment) => {
        const kind = markerKind(segment);
        const marker = MARKER[kind];
        const Icon = marker.icon;
        return (
          <span
            key={segment.index}
            id={`${prefix}-segment-${segment.index}`}
            data-segment={segment.index}
            className={segment.paragraph > (segments[segment.index - 1]?.paragraph ?? 0) ? 'block pt-3' : ''}
          >
            <span data-seg-text={segment.index} className={`rounded-sm ${marker.text}`}>
              {pieces(
                segment.text,
                highlights.filter((h) => h.segment === segment.index),
              ).map((piece) =>
                piece.threads.length ? (
                  <mark
                    key={piece.from}
                    data-threads={piece.threads.join(' ')}
                    className={`cursor-pointer rounded-sm text-ink ${piece.threads.includes(activeThread ?? '') ? 'bg-amber/40' : 'bg-amber-bg'}`}
                    onClick={() => onHighlight?.(piece.threads[0])}
                  >
                    {piece.text}
                  </mark>
                ) : (
                  <span key={piece.from}>{piece.text}</span>
                ),
              )}
            </span>
            <Popover.Root>
              <Popover.Trigger asChild>
                <button
                  type="button"
                  className={`mx-1 inline-flex h-5 items-center gap-0.5 rounded-full px-1.5 align-[1px] text-[11px] font-semibold not-italic leading-none hover:brightness-95 focus:outline-none focus:ring-2 focus:ring-accent ${marker.pill}`}
                  aria-label={`Sentence ${segment.index + 1}: ${marker.meaning}. Show source`}
                  title={marker.meaning}
                >
                  {segment.index + 1}
                  {Icon ? <Icon className={`size-3 ${kind === 'pending' ? 'animate-spin' : ''}`} aria-hidden /> : null}
                </button>
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Content
                  side="top"
                  sideOffset={6}
                  collisionPadding={12}
                  className="z-50 w-72 max-w-[calc(100vw-24px)] max-h-80 overflow-y-auto rounded border border-line bg-bg p-3 text-xs font-normal not-italic text-ink shadow-lg"
                >
                  <span className="mb-2 block font-semibold">
                    Sentence {segment.index + 1} · {marker.meaning}
                  </span>
                  {segment.dispute ? (
                    <span className="mb-2 block text-red">
                      Disputed by {segment.dispute.disputed_by}: {segment.dispute.note}
                    </span>
                  ) : null}
                  {(segment.sources ?? []).map((source, i) =>
                    source.source_type === 'human_attestation' ? (
                      <span key={i} className="block">
                        Confirmed by {source.attested_by}: {source.note}
                      </span>
                    ) : (
                      <span key={i} className="mb-2 block">
                        <button type="button" className="font-semibold text-accent underline" onClick={() => onSource(source)}>
                          {source.document_title}
                        </button>
                        <span className="block">{source.quote}</span>
                        <span className="block text-muted">
                          {label(source.doc_type)}
                          {source.doc_kind ? ` · ${label(source.doc_kind)}` : ''} · {source.effective_date ?? 'Undated'} ·{' '}
                          {source.locator.cell_ref ?? source.locator.heading_path?.join(' / ')}
                        </span>
                      </span>
                    ),
                  )}
                  {!segment.sources?.length ? <span>No verified document source.</span> : null}
                </Popover.Content>
              </Popover.Portal>
            </Popover.Root>{' '}
          </span>
        );
      })}
    </div>
  );
}
export function SourcePane({ workspaceId, source, onClose }: { workspaceId: string; source: Source; onClose: () => void }) {
  const [data, setData] = useState<S['SectionResponse'] | null>(null),
    [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const { start, end, section_id } = source.locator;
    const query = start != null && end != null ? `?start=${start}&end=${end}` : '';
    void backendJson<S['SectionResponse']>(workspaceId, `/sections/${section_id}${query}`)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [workspaceId, source]);
  const text = data?.section.text ?? '';
  const range = data?.highlight ? codePointRange(text, data.highlight.start, data.highlight.end) : null;
  return (
    <aside className={`${panel} sticky top-3 max-h-[75vh] overflow-y-auto`} aria-label="Source section">
      <div className="flex justify-between gap-3">
        <h3 className="font-semibold">{data?.document.filename ?? source.document_title}</h3>
        <Button size="sm" onClick={onClose}>
          Close source
        </Button>
      </div>
      <ErrorNote error={error} />
      {!data && !error ? <p>Loading source…</p> : null}
      {data ? (
        <>
          <p className="my-2 text-xs text-muted">{data.section.cell_ref ?? data.section.heading_path.join(' / ')}</p>
          {!range ? <p className="my-2 text-xs text-amber">Span not located in this section. Candidate quote: {source.quote}</p> : null}
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6">
            {range ? (
              <>
                {text.slice(0, range.start)}
                <mark>{text.slice(range.start, range.end)}</mark>
                {text.slice(range.end)}
              </>
            ) : (
              text
            )}
          </pre>
        </>
      ) : null}
    </aside>
  );
}
