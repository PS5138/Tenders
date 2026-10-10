'use client';
import { useEffect, useRef, useState, type FocusEvent } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { backendJson } from '@/lib/backend-api';
import { codePointRange, type Segment } from '@/lib/backend-stream';
import type { S } from './shared';
import { ErrorNote, label, panel } from './shared';
import { Button } from '@/components/ui/button';
import { Check, CircleAlert, Flag, LoaderCircle, PenLine, TriangleAlert, UserCheck, type LucideIcon } from 'lucide-react';
export type Source = S['DocumentSource'];
/** What the source pane needs: a section, optional offsets to highlight, and a title and quote to fall back on. */
export type PaneSource = Pick<Source, 'locator' | 'quote' | 'document_title'>;
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

// ---------------------------------------------------------------------------
// Pure helpers behind the trace interaction. Exported so they can be unit tested
// without a DOM; the components below are thin wrappers around them.
// ---------------------------------------------------------------------------

/** Statuses a reviewer must look at. `supported` and `connective` sentences are skipped when moving through an answer. */
export const ATTENTION_STATUSES: ReadonlySet<string> = new Set(['weak', 'unsupported', 'human_authored']);

/** Indices of the sentences needing attention, in reading order. A disputed sentence is rendered as unsupported and counts. */
export function attentionIndices(segments: readonly Pick<Segment, 'index' | 'support_status' | 'dispute'>[]): number[] {
  return segments
    .filter((s) => ATTENTION_STATUSES.has(s.support_status) || Boolean(s.dispute))
    .map((s) => s.index)
    .sort((a, b) => a - b);
}

/**
 * The sentence to visit after `last`: the first index above it, wrapping to the first index at the end.
 * `last` is null when nothing has been visited yet for this answer version. Null when nothing needs attention.
 */
export function nextAttentionIndex(indices: readonly number[], last: number | null): number | null {
  if (!indices.length) return null;
  if (last == null) return indices[0];
  return indices.find((i) => i > last) ?? indices[0];
}

export type LiveSupportSummary = {
  substantive: number;
  supported: number;
  /** Substantive sentences that are not supported, including those still being checked. */
  needs_attention: number;
  /** Substantive sentences whose source is still being checked (streamed with `pending`). */
  pending: number;
  score: number | null;
};

/**
 * The support summary for segments as they stand, the same arithmetic as the backend's
 * `summarise_support`. Used while a draft streams, before `done` carries the stored summary: a
 * `pending` sentence counts as substantive and not yet supported, and is also reported on its own.
 */
export function summariseSegments(segments: readonly Pick<Segment, 'kind' | 'support_status'>[]): LiveSupportSummary {
  let substantive = 0,
    supported = 0,
    pending = 0;
  for (const s of segments) {
    if ((s.kind ?? 'substantive') !== 'substantive') continue;
    substantive += 1;
    if (s.support_status === 'supported') supported += 1;
    else if (s.support_status === 'pending') pending += 1;
  }
  return {
    substantive,
    supported,
    needs_attention: substantive - supported,
    pending,
    score: substantive ? Math.round((supported / substantive) * 100) / 100 : null,
  };
}

/** The one key that jumps to the next sentence needing attention. */
export const NEXT_SHORTCUT = 'n';

/**
 * True when a single-key shortcut must stay out of the way: the key would type into a field, or the
 * focus is inside a dialog (including this component's own popover) that owns the keyboard.
 */
export function isEditableTarget(target: unknown): boolean {
  if (!target || typeof target !== 'object') return false;
  const el = target as { tagName?: unknown; isContentEditable?: unknown; closest?: unknown };
  const tag = typeof el.tagName === 'string' ? el.tagName.toUpperCase() : '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable === true) return true;
  if (typeof el.closest !== 'function') return false;
  return Boolean(el.closest.call(el, '[contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"], [role="dialog"], [aria-modal="true"]'));
}

/** Whether a keydown is the plain `n` shortcut, pressed somewhere it may act. */
export function nextShortcutPressed(event: {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  defaultPrevented?: boolean;
  target: unknown;
}): boolean {
  return event.key === NEXT_SHORTCUT && !event.altKey && !event.ctrlKey && !event.metaKey && !event.defaultPrevented && !isEditableTarget(event.target);
}

/**
 * The scroll position that puts a highlighted span about a third of the way down its scroll container.
 * `markTop` is the span's top measured from the top of the container's scrollable content.
 */
export function scrollOffsetFor(markTop: number, containerHeight: number): number {
  return Math.max(0, Math.round(markTop - containerHeight / 3));
}

/** The source a marker click opens in the pane: the first document or fact source. Attestations have no pane. */
export function paneSource(segment: Pick<Segment, 'sources'>): Source | null {
  return (segment.sources ?? []).find((s): s is Source => s.source_type !== 'human_attestation') ?? null;
}

/** Where in the document a source sits, from the locator alone. */
export function locationText(locator: Source['locator']): string {
  const parts: string[] = [];
  if (locator.page != null) parts.push(`Page ${locator.page}`);
  if (locator.table != null) parts.push(`Table ${locator.table + 1}`);
  if (locator.cell_ref) parts.push(`Row ${locator.cell_ref}`);
  else if (locator.heading_path?.length) parts.push(locator.heading_path.join(' / '));
  return parts.join(' · ');
}

export function markerId(prefix: string, index: number): string {
  return `${prefix}-marker-${index}`;
}

/** Focus a sentence's marker and bring it into view. Returns false when that sentence is not rendered. */
export function focusSegmentMarker(prefix: string, index: number): boolean {
  const marker = document.getElementById(markerId(prefix, index));
  if (!marker) return false;
  marker.focus({ preventScroll: true });
  marker.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return true;
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

/** How long the pointer may be off both the sentence and the popover before the popover closes. */
const CLOSE_DELAY_MS = 150;

/**
 * One sentence with its marker and source popover. The popover is controlled: it opens on hover of the
 * sentence or its marker and on keyboard focus of the marker, with no click, and everything it shows is
 * read from the segment record. A click on the marker (or Enter or Space) opens the source pane directly
 * for the first document or fact source. The down arrow on the marker moves the keyboard into the popover,
 * where Tab cycles every "Verify source" button and Escape returns to the marker, so a sentence's second
 * source is reachable without a pointer.
 */
function Sentence({
  segment,
  newParagraph,
  prefix,
  highlights,
  activeThread,
  onHighlight,
  onSource,
}: {
  segment: Segment;
  newParagraph: boolean;
  prefix: string;
  highlights: Highlight[];
  activeThread?: string | null;
  onHighlight?: (threadId: string) => void;
  onSource: (source: Source) => void;
}) {
  const kind = markerKind(segment);
  const marker = MARKER[kind];
  const Icon = marker.icon;
  const n = segment.index + 1;
  const target = paneSource(segment);
  const [open, setOpen] = useState(false);
  const markerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set when Escape or a pane open closed the popover while the marker kept focus, so focus alone does
  // not reopen it. Cleared when focus leaves the marker.
  const dismissed = useRef(false);
  useEffect(
    () => () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    },
    [],
  );
  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };
  const show = () => {
    cancelClose();
    setOpen(true);
  };
  // Hovering opens the popover, except while a button is held: dragging a selection across sentences
  // to comment on it must not pop a source over every sentence it crosses.
  const hover = (e: { buttons: number }) => {
    if (!e.buttons) show();
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
  };
  const leaveFocus = (e: FocusEvent) => {
    const next = e.relatedTarget as Node | null;
    if (next && (markerRef.current?.contains(next) || contentRef.current?.contains(next))) return;
    dismissed.current = false;
    cancelClose();
    setOpen(false);
  };
  const openPane = (source: Source) => {
    dismissed.current = true;
    cancelClose();
    setOpen(false);
    // Keep the keyboard in the answer so the next shortcut still works once the pane is open.
    markerRef.current?.focus({ preventScroll: true });
    onSource(source);
  };
  const sources = segment.sources ?? [];
  return (
    <span id={`${prefix}-segment-${segment.index}`} data-segment={segment.index} className={newParagraph ? 'block pt-3' : ''}>
      <span data-seg-text={segment.index} className={`rounded-sm ${marker.text}`} onPointerEnter={hover} onPointerLeave={scheduleClose}>
        {pieces(segment.text, highlights).map((piece) =>
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
      {/* Radix reports Escape, outside pointer-downs and focus moving outside through onOpenChange. */}
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Anchor asChild>
          <button
            ref={markerRef}
            id={markerId(prefix, segment.index)}
            type="button"
            className={`mx-1 inline-flex h-5 items-center gap-0.5 rounded-full px-1.5 align-[1px] text-[11px] font-semibold not-italic leading-none hover:brightness-95 focus:outline-none focus:ring-2 focus:ring-accent ${marker.pill}`}
            aria-label={`Sentence ${n}: ${marker.meaning}. ${target ? 'Open the source. Press the down arrow for every source' : 'Show details'}`}
            aria-expanded={open}
            aria-haspopup="dialog"
            title={target ? `${marker.meaning}. Hover for the excerpt, click to open the source, down arrow for every source` : marker.meaning}
            onPointerEnter={hover}
            onPointerLeave={scheduleClose}
            onFocus={() => {
              if (!dismissed.current) show();
            }}
            onBlur={leaveFocus}
            onClick={() => {
              if (target) openPane(target);
              else setOpen((o) => !o);
            }}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowDown' || e.altKey || e.ctrlKey || e.metaKey) return;
              e.preventDefault();
              if (!open) {
                // Reopen a popover that Escape dismissed while the marker kept focus.
                dismissed.current = false;
                show();
                return;
              }
              // Into the popover: the move survives leaveFocus (relatedTarget is inside the content) and
              // Radix's focus-outside dismissal (the target is inside the layer).
              contentRef.current?.querySelector<HTMLElement>('button')?.focus();
            }}
          >
            {n}
            {Icon ? <Icon className={`size-3 ${kind === 'pending' ? 'animate-spin' : ''}`} aria-hidden /> : null}
          </button>
        </Popover.Anchor>
        <Popover.Portal>
          <Popover.Content
            ref={contentRef}
            side="top"
            sideOffset={6}
            collisionPadding={12}
            aria-label={`Source for sentence ${n}`}
            onOpenAutoFocus={(e) => e.preventDefault()}
            onCloseAutoFocus={(e) => e.preventDefault()}
            onEscapeKeyDown={() => {
              const active = document.activeElement;
              if (active === markerRef.current || contentRef.current?.contains(active)) {
                // Escape while the marker or the popover has focus: stay closed until focus leaves the
                // marker, and bring the keyboard back to it. `dismissed` is set first, otherwise the
                // marker's onFocus would reopen the popover.
                dismissed.current = true;
                markerRef.current?.focus({ preventScroll: true });
              }
            }}
            onPointerEnter={cancelClose}
            onPointerLeave={scheduleClose}
            onBlur={leaveFocus}
            className="z-50 w-72 max-w-[calc(100vw-24px)] max-h-80 overflow-y-auto rounded border border-line bg-bg p-3 text-xs font-normal not-italic text-ink shadow-lg"
          >
            <span className="mb-2 block font-semibold">
              Sentence {n} · {marker.meaning}
            </span>
            {segment.dispute ? (
              <span className="mb-2 block text-red">
                Disputed by {segment.dispute.disputed_by}: {segment.dispute.note}
              </span>
            ) : null}
            {sources.map((source, i) =>
              source.source_type === 'human_attestation' ? (
                <span key={i} className="block">
                  Confirmed by {source.attested_by}
                  {source.note ? `: ${source.note}` : ''}
                </span>
              ) : (
                <span key={i} className="mb-2 block">
                  <span className="block font-semibold">{source.document_title}</span>
                  <span className="block">{source.quote}</span>
                  <span className="block text-muted">
                    {label(source.doc_type)}
                    {source.doc_kind ? ` · ${label(source.doc_kind)}` : ''} · {source.effective_date ?? 'Undated'}
                    {locationText(source.locator) ? ` · ${locationText(source.locator)}` : ''}
                  </span>
                  <Button
                    size="sm"
                    variant={i === sources.findIndex((s) => s.source_type !== 'human_attestation') ? 'primary' : 'secondary'}
                    className="mt-1.5"
                    aria-label={`Verify source: ${source.document_title}`}
                    onClick={() => openPane(source)}
                  >
                    Verify source
                  </Button>
                </span>
              ),
            )}
            {!sources.length ? <span className="block">No verified document source.</span> : null}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>{' '}
    </span>
  );
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
      {segments.map((segment, i) => (
        <Sentence
          key={segment.index}
          segment={segment}
          newParagraph={segment.paragraph > (segments[i - 1]?.paragraph ?? 0)}
          prefix={prefix}
          highlights={highlights.filter((h) => h.segment === segment.index)}
          activeThread={activeThread}
          onHighlight={onHighlight}
          onSource={onSource}
        />
      ))}
    </div>
  );
}

export function SourcePane({ workspaceId, source, onClose }: { workspaceId: string; source: PaneSource; onClose: () => void }) {
  const [data, setData] = useState<S['SectionResponse'] | null>(null),
    [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const markRef = useRef<HTMLElement>(null);
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
  // Once the section is in, scroll the pane (never the window) so the highlighted span sits a third of the way down.
  useEffect(() => {
    const container = containerRef.current,
      mark = markRef.current;
    if (!data || !container || !mark) return;
    const markTop = mark.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
    container.scrollTop = scrollOffsetFor(markTop, container.clientHeight);
  }, [data]);
  const text = data?.section.text ?? '';
  const range = data?.highlight ? codePointRange(text, data.highlight.start, data.highlight.end) : null;
  return (
    <aside className={`${panel} sticky top-3`} aria-label="Source section">
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
          <div ref={containerRef} className="relative max-h-[60vh] overflow-y-auto" data-testid="source-scroll">
            <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6">
              {range ? (
                <>
                  {text.slice(0, range.start)}
                  <mark ref={markRef}>{text.slice(range.start, range.end)}</mark>
                  {text.slice(range.end)}
                </>
              ) : (
                text
              )}
            </pre>
          </div>
        </>
      ) : null}
    </aside>
  );
}
