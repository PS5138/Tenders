'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, MessageSquarePlus } from 'lucide-react';
import { highlightRanges, quoteFor, relocate, type Anchor } from '@/lib/comment-anchors';
import { Badge } from '@/components/ui/badge';
import { buttonClasses } from '@/components/ui/button';
import { CoverageBadge, StatusBadge } from './badges';
import { CommentsPanel, useCommentThreads, type PlacedThread } from './comments';
import { People } from './people';
import { MentionText, MentionTextarea } from './mention-textarea';
import Link from 'next/link';
import { backendJson, BackendRequestError } from '@/lib/backend-api';
import { diffWords } from '@/lib/diff';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { ErrorNote, Field, field, label, panel, useResource, type S } from './shared';
import {
  Trace,
  TraceLegend,
  SourcePane,
  attentionIndices,
  focusSegmentMarker,
  nextAttentionIndex,
  nextShortcutPressed,
  summariseSegments,
  NEXT_SHORTCUT,
  type Source,
} from './trace';
import { formatEvidenceScore, wordCount } from '@/lib/format';
import { useDraftStream } from './use-draft-stream';
import type { Segment } from '@/lib/backend-stream';

type Member = { userId: string; displayName: string };
const NO_SEGMENTS: Segment[] = [];

/**
 * Whether the editor should take on a version that landed while it was open. The text is adopted
 * silently only while the person has not typed; otherwise their text is kept and a conflict is shown.
 */
export function adoptLandedVersion(
  editor: { seededId: string | null; seededText: string; text: string },
  landed: { id: string; text: string } | null,
): 'ignore' | 'adopt' | 'conflict' {
  if (!landed || landed.id === editor.seededId) return 'ignore';
  return editor.text === editor.seededText ? 'adopt' : 'conflict';
}

/**
 * What the editor does when the question's current version differs from the one it last saw. Null when
 * the prop has not changed. The gate is the prop changing, not a mismatch with `seeded`: a save and the
 * "Reload current answer" button move `seeded` ahead of the prop until the parent's reload lands, and
 * that reload must not revert the editor to the version it just left. A version the editor already
 * seeded itself from is only recorded as seen; a foreign one is adopted or flagged as a conflict.
 */
export function reconcileLandedVersion(
  state: { seenId: string | null; seeded: { id: string | null; text: string }; text: string },
  current: { id: string; text: string } | null,
): { seenId: string | null; seeded: { id: string | null; text: string } | null; outcome: 'ignore' | 'adopt' | 'conflict' } | null {
  const currentId = current?.id ?? null;
  if (currentId === state.seenId) return null;
  const outcome = adoptLandedVersion({ seededId: state.seeded.id, seededText: state.seeded.text, text: state.text }, current);
  return { seenId: currentId, seeded: outcome === 'ignore' ? null : { id: currentId, text: current?.text ?? '' }, outcome };
}

function Editor({ workspaceId, question, onSaved }: { workspaceId: string; question: S['QuestionDetail']; onSaved: () => void }) {
  const current = question.current_answer ?? null;
  const currentId = current?.id ?? null,
    currentText = current?.text ?? '';
  // The version the text was seeded from. The base version for a save is the same id until a save succeeds.
  const [seeded, setSeeded] = useState({ id: currentId, text: currentText });
  // The current version id the editor last reconciled against (the prop, not the seed).
  const [seenId, setSeenId] = useState(currentId);
  const [baseVersion, setBaseVersion] = useState(currentId);
  const [text, setText] = useState(currentText),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null),
    [conflict, setConflict] = useState<S['AnswerRecord'] | null>(null),
    [confirmReload, setConfirmReload] = useState(false);
  // A new current version landed while the editor was open (a draft finished, a colleague saved, a reply
  // was saved as the answer). State is adjusted during render, the React pattern for reacting to a prop
  // change; the compiler lint rules forbid the equivalent synchronous setState inside an effect.
  const landed = reconcileLandedVersion({ seenId, seeded, text }, current ? { id: currentId!, text: currentText } : null);
  if (landed) {
    setSeenId(landed.seenId);
    if (landed.seeded) setSeeded(landed.seeded);
    if (landed.outcome === 'adopt') {
      setText(currentText);
      setBaseVersion(currentId);
      setConflict(null);
    } else if (landed.outcome === 'conflict' && current) setConflict(current);
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await backendJson<{ answer: S['AnswerRecord'] }>(workspaceId, `/questions/${question.id}/answers`, 'POST', {
        text,
        base_version_id: baseVersion,
      });
      setSeeded({ id: saved.answer.id, text: saved.answer.text });
      setBaseVersion(saved.answer.id);
      setText(saved.answer.text);
      setConflict(null);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof BackendRequestError && e.status === 409 && e.detail.current_answer)
        setConflict(e.detail.current_answer as S['AnswerRecord']);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-4 space-y-3">
      <Field title="Edit answer">
        <textarea className={`${field} min-h-48 leading-6`} value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
      </Field>
      <p className="text-xs text-muted">
        {text.trim() ? text.trim().split(/\s+/).length : 0} / {question.word_limit ?? 'no limit'} words. Saving creates a new version and
        returns the answer to writer edited.
      </p>
      <Button busy={busy} disabled={!text.trim() || text === seeded.text} onClick={() => void save()}>
        Save answer
      </Button>
      <ErrorNote error={error} />
      {conflict ? (
        <div className="rounded border border-amber p-3 text-sm">
          <p className="font-semibold">Another version was saved. Your text is still above.</p>
          <pre className="my-2 whitespace-pre-wrap break-words font-sans">{conflict.text}</pre>
          <Button onClick={() => setConfirmReload(true)}>Reload current answer</Button>
        </div>
      ) : null}
      <Dialog
        open={confirmReload && Boolean(conflict)}
        onOpenChange={setConfirmReload}
        title="Reload the current version?"
        description="Your unsaved text in the editor will be replaced. Copy anything you want to keep first."
      >
        <Button
          variant="primary"
          onClick={() => {
            if (conflict) {
              setSeeded({ id: conflict.id, text: conflict.text });
              setText(conflict.text);
              setBaseVersion(conflict.id);
              setConflict(null);
              setError(null);
              onSaved();
            }
            setConfirmReload(false);
          }}
        >
          Reload current answer
        </Button>
      </Dialog>
    </div>
  );
}
/**
 * The support summary at the top of the answer. While sentences need attention the whole summary is a
 * button that jumps to the next one; the same jump is on the `n` key.
 */
function SupportSummary({
  summary,
  words,
  streaming = false,
  onNext,
}: {
  summary: Pick<S['SupportSummary'], 'supported' | 'substantive' | 'needs_attention' | 'score'> & { pending?: number };
  words: number;
  /** A draft is streaming: the counts are the streamed sentences so far, and `pending` ones are still being checked. */
  streaming?: boolean;
  onNext: () => void;
}) {
  const pending = summary.pending ?? 0;
  // Sentences still being checked are not yet something to jump to; they are reported on their own.
  const attention = summary.needs_attention - pending;
  const body = (
    <>
      <strong>
        {summary.supported} of {summary.substantive}
      </strong>{' '}
      substantive sentences supported{streaming ? ' so far' : ''}
      {attention > 0 ? `, ${attention} ${attention === 1 ? 'needs' : 'need'} attention` : ''}
      {pending ? `, ${pending} being checked` : ''} · Evidence coverage {formatEvidenceScore(summary.score)} · {words} words
      <span className="block font-normal text-muted">
        {attention > 0 ? `Click here or press ${NEXT_SHORTCUT} to jump to the next sentence needing attention. ` : ''}
        {streaming ? 'Drafting: sources are checked as each sentence arrives. ' : ''}
        Evidence coverage is the share of substantive sentences with a verified source, not a measure of correctness.
      </span>
    </>
  );
  return attention > 0 ? (
    <button
      type="button"
      className="flex w-full items-center justify-between gap-3 rounded-md bg-soft px-3 py-2 text-left text-xs hover:bg-line/60 focus:outline-none focus:ring-2 focus:ring-accent"
      title={`Jump to the next sentence needing attention (press ${NEXT_SHORTCUT})`}
      aria-keyshortcuts={NEXT_SHORTCUT}
      // Keep focus where it is so the jump decides what is focused next, not the click.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onNext}
    >
      <span>{body}</span>
      <ChevronRight className="size-4 shrink-0" aria-hidden />
    </button>
  ) : (
    <p className="rounded-md bg-soft px-3 py-2 text-xs">{body}</p>
  );
}

const STEPS = ['not_started', 'ai_draft', 'writer_edited', 'sme_verified', 'approved'];
const RANK = Object.fromEntries(STEPS.map((s, i) => [s, i]));

function ReviewSteps({ status }: { status: string }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs" aria-label="Review progress">
      {STEPS.map((s, i) => (
        <li key={s} aria-current={s === status ? 'step' : undefined} className="flex items-center gap-1">
          {i ? <span className="text-line-strong" aria-hidden>→</span> : null}
          <span
            className={`rounded-full px-2 py-0.5 ${s === status ? 'bg-accent font-semibold text-on-accent' : RANK[s] < RANK[status] ? 'bg-tint text-accent' : 'bg-soft text-muted'}`}
          >
            {label(s)}
          </span>
        </li>
      ))}
    </ol>
  );
}

type Tab = 'details' | 'comments' | 'assistant' | 'activity';

export function QuestionWorkspace({
  workspaceId,
  tenderId,
  questionId,
  members,
  me,
  initialThread = null,
}: {
  workspaceId: string;
  tenderId: string;
  questionId: string;
  members: Member[];
  me: string;
  /** Opened from My tasks: show this comment thread. */
  initialThread?: string | null;
}) {
  const question = useResource<S['QuestionDetail']>(workspaceId, `/questions/${questionId}`),
    versions = useResource<S['AnswerRecord'][]>(workspaceId, `/questions/${questionId}/answers`),
    events = useResource<S['EventRecord'][]>(workspaceId, `/questions/${questionId}/events`),
    documents = useResource<S['DocumentWithJob'][]>(workspaceId, '/documents');
  const comments = useCommentThreads(workspaceId, questionId);
  const q = question.data,
    answer = q?.current_answer;
  const [source, setSource] = useState<Source | null>(null),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [oldVersion, setOldVersion] = useState(''),
    [edit, setEdit] = useState(false),
    [tab, setTab] = useState<Tab>(initialThread ? 'comments' : 'details'),
    [draftComment, setDraftComment] = useState<{ anchor: Anchor; quote: string } | null>(null),
    [selection, setSelection] = useState<{ anchor: Anchor; quote: string; top: number; left: number } | null>(null),
    [activeThread, setActiveThread] = useState<string | null>(initialThread),
    [note, setNote] = useState(''),
    [noteAction, setNoteAction] = useState<{
      path: string;
      body?: Record<string, unknown>;
      title: string;
      required: boolean;
      description?: string;
    } | null>(null),
    [displace, setDisplace] = useState<{
      path: string;
      body: Record<string, unknown>;
      stream: boolean;
    } | null>(null);
  const answerRef = useRef<HTMLDivElement>(null);
  const stream = useDraftStream(workspaceId);
  const { reload: reloadQuestion } = question;
  const { reload: reloadVersions } = versions;
  const { reload: reloadEvents } = events;
  const { reload: reloadComments } = comments;
  const refresh = useCallback(() => {
    void reloadQuestion();
    void reloadVersions();
    void reloadEvents();
    void reloadComments();
  }, [reloadQuestion, reloadVersions, reloadEvents, reloadComments]);
  useEffect(() => {
    if (stream.state.result) refresh();
  }, [stream.state.result, refresh]);
  // Bring a thread opened from My tasks into view once the threads have loaded.
  const initialThreadLoaded = Boolean(initialThread && comments.threads.some((t) => t.id === initialThread));
  useEffect(() => {
    if (!initialThreadLoaded || !initialThread) return;
    requestAnimationFrame(() => document.getElementById(`comment-thread-${initialThread}`)?.scrollIntoView({ block: 'center' }));
  }, [initialThreadLoaded, initialThread]);
  useEffect(() => {
    if (!q?.draft_in_progress || stream.busy) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [q?.draft_in_progress, stream.busy, refresh]);
  async function act(path: string, body: Record<string, unknown> = {}, method = 'POST', draft = false) {
    setError(null);
    setBusy(true);
    try {
      if (draft) {
        const previous = answer?.id;
        await stream.start(path, body, async () => {
          const next = await backendJson<S['QuestionDetail']>(workspaceId, `/questions/${questionId}`);
          return {
            inProgress: next.draft_in_progress ?? false,
            result: next.current_answer?.id !== previous ? (next.current_answer ?? null) : null,
          };
        });
      } else {
        await backendJson(workspaceId, path, method, body);
        refresh();
      }
    } catch (e) {
      if (e instanceof BackendRequestError && e.detail.code === 'displacement') setDisplace({ path, body, stream: draft });
      else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visibleSegments = stream.busy ? stream.state.segments : (answer?.segments ?? NO_SEGMENTS);
  // Place every thread against the current version: as made, carried over, or outdated.
  const placed: PlacedThread[] = comments.threads.map((t) => {
    if (answer && t.answerId === answer.id) return { ...t, current: t.anchor, quote: quoteFor(answer.segments, t.anchor), carried: false };
    const made = versions.data?.find((v) => v.id === t.answerId);
    const current = made && answer ? relocate(made.segments, t.anchor, answer.segments) : null;
    return { ...t, current, quote: made ? quoteFor(made.segments, t.anchor) : null, carried: Boolean(current) };
  });
  const highlights =
    answer && !stream.busy
      ? placed.filter((t) => t.current && !t.resolvedAt).flatMap((t) => highlightRanges(answer.segments, t.current!, t.id))
      : [];
  const openComments = placed.filter((t) => !t.resolvedAt).length;
  function focusThread(id: string) {
    setActiveThread(id);
    setTab('comments');
    requestAnimationFrame(() => document.getElementById(`comment-thread-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
  }
  function captureSelection() {
    const root = answerRef.current,
      current = window.getSelection();
    if (!root || !current || current.isCollapsed || !current.rangeCount || !answer || stream.busy) return setSelection(null);
    const range = current.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return setSelection(null);
    const parts = [...root.querySelectorAll<HTMLElement>('[data-seg-text]')].filter((el) => range.intersectsNode(el));
    if (!parts.length) return setSelection(null);
    const offsetIn = (el: HTMLElement, node: Node, offset: number) => {
      const r = document.createRange();
      r.setStart(el, 0);
      r.setEnd(node, offset);
      return r.toString().length;
    };
    const first = parts[0],
      last = parts[parts.length - 1];
    const anchor: Anchor = {
      start: {
        segment: Number(first.dataset.segText),
        offset: first.contains(range.startContainer) ? offsetIn(first, range.startContainer, range.startOffset) : 0,
      },
      end: {
        segment: Number(last.dataset.segText),
        offset: last.contains(range.endContainer) ? offsetIn(last, range.endContainer, range.endOffset) : (last.textContent ?? '').length,
      },
    };
    const quote = quoteFor(answer.segments, anchor);
    if (!quote?.trim()) return setSelection(null);
    const rect = range.getBoundingClientRect(),
      box = root.getBoundingClientRect();
    setSelection({ anchor, quote, top: rect.top - box.top - 38, left: Math.max(0, Math.min(rect.left - box.left, box.width - 130)) });
  }
  // The last sentence visited by the jump control, so repeated presses move through the answer and wrap.
  // It starts again whenever a different version becomes current.
  const lastVisited = useRef<number | null>(null);
  const answerId = answer?.id ?? null;
  useEffect(() => {
    lastVisited.current = null;
  }, [answerId]);
  const jumpToNext = useCallback(() => {
    const next = nextAttentionIndex(attentionIndices(visibleSegments), lastVisited.current);
    if (next == null) return;
    lastVisited.current = next;
    focusSegmentMarker('answer', next);
  }, [visibleSegments]);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!nextShortcutPressed(event)) return;
      event.preventDefault();
      jumpToNext();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [jumpToNext]);
  function showSentence(index: number) {
    lastVisited.current = index;
    focusSegmentMarker('answer', index);
  }
  function confirmSentence(index: number) {
    if (!answer) return;
    setNoteAction({
      path: `/answers/${answer.id}/segments/${index}/attest`,
      title: `Confirm sentence ${index + 1} is accurate`,
      description:
        'Use this when a sentence has no document source, for example something you wrote yourself. It records that you have checked the sentence is true. Your name, the time and your note go in the activity log. If the sentence is changed later, it needs confirming again.',
      required: false,
    });
  }
  const previous = versions.data?.find((v) => v.id === oldVersion);
  const transitions = q?.allowed_transitions?.filter((t) => !['not_started', 'ai_draft'].includes(t.to)) ?? [];
  const actionLabel = (to: string) =>
    to === 'approved'
      ? 'Approve'
      : to === 'sme_verified'
        ? 'Mark SME verified'
        : q && RANK[q.status] > RANK.writer_edited
          ? 'Send back to writer'
          : 'Mark writer edited';
  type Blocker = NonNullable<(typeof transitions)[number]['blockers']>[number];
  const blockerText = (b: Blocker) => {
    if (b.kind === 'segment') {
      const segment = answer?.segments.find((s) => s.index === b.index);
      const n = (b.index ?? 0) + 1;
      if (segment?.dispute) return `Sentence ${n} is disputed: ${segment.dispute.note}`;
      if (segment?.support_status === 'human_authored') return `Sentence ${n} was written by a person and has not been confirmed`;
      if (segment?.support_status === 'weak') return `Sentence ${n} rests on a weak or out-of-date source`;
      return `Sentence ${n} has no supporting source`;
    }
    return b.kind === 'gap'
      ? `Acknowledge the gap: ${b.gap}`
      : b.kind === 'needs_review'
        ? 'Resolve the items marked as needing review'
        : b.kind === 'no_answer'
          ? 'Write or generate an answer first'
          : label(b.kind);
  };
  // One list of distinct blockers across the targets, so the same sentence is not listed twice.
  const blockers = [
    ...new Map(
      transitions
        .filter((t) => !t.allowed && q?.status !== t.to)
        .flatMap((t) => t.blockers ?? [])
        .map((b) => [`${b.kind}:${b.index ?? ''}:${b.gap ?? ''}`, b] as const),
    ).values(),
  ];
  return (
    <>
      <Link className="text-xs text-accent" href={`/w/${workspaceId}/tenders/${tenderId}`}>
        ← Tender workspace
      </Link>
      <ErrorNote error={question.error ?? versions.error ?? events.error ?? error} />
      {q ? (
        <>
          <header className="my-4 space-y-2">
            <p className="text-xs text-muted">
              {q.section} · {q.number}
            </p>
            <h1 className="text-xl font-semibold leading-snug wrap-anywhere">{q.text}</h1>
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge status={q.status} />
              <CoverageBadge coverage={q.coverage} />
              {q.mandatory ? <Badge>Mandatory</Badge> : null}
              <Badge>{q.word_limit ? `${q.word_limit} word limit` : 'No word limit'}</Badge>
              {q.weighting ? <Badge>Weighting {q.weighting}</Badge> : null}
              <Badge>{label(q.response_type)}</Badge>
              {q.needs_review ? <Badge tone="red">Needs review</Badge> : null}
            </div>
          </header>
          <section className={`${panel} mb-4 space-y-3`} aria-label="Review">
            <ReviewSteps status={q.status} />
            <div className="flex flex-wrap gap-2">
              {transitions.map((t) => (
                <Button
                  key={t.to}
                  size="sm"
                  variant={t.to === 'approved' ? 'primary' : 'secondary'}
                  busy={busy}
                  disabled={!t.allowed || q.status === t.to}
                  onClick={() => void act(`/questions/${q.id}`, { status: t.to }, 'PATCH')}
                >
                  {actionLabel(t.to)}
                </Button>
              ))}
            </div>
            {blockers.length ? (
              <div className="text-xs">
                <p className="font-semibold">Before this answer can move forward:</p>
                <ul className="mt-1 space-y-1.5">
                  {blockers.map((b, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-2">
                      <span className="text-muted">{blockerText(b)}</span>
                      {b.kind === 'segment' && b.index != null ? (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => showSentence(b.index!)}>
                            Show
                          </Button>
                          {!answer?.segments.find((s) => s.index === b.index)?.dispute ? (
                            <Button size="sm" onClick={() => confirmSentence(b.index!)}>
                              Confirm accurate
                            </Button>
                          ) : null}
                        </>
                      ) : null}
                      {b.kind === 'gap' && b.gap ? (
                        <Button
                          size="sm"
                          onClick={() =>
                            setNoteAction({
                              path: `/questions/${q.id}/gaps/acknowledge`,
                              body: { gap: b.gap },
                              title: 'Acknowledge evidence gap',
                              description: 'Record why this answer can go forward without evidence for this point.',
                              required: true,
                            })
                          }
                        >
                          Acknowledge
                        </Button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(320px,1fr)]">
            <section className={`${panel} space-y-4`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Answer · {label(q.status)}</h2>
                <div className="flex flex-wrap gap-2">
                  {answer ? (
                    <Button size="sm" onClick={() => setEdit(!edit)}>
                      {edit ? 'Hide editor' : 'Edit answer'}
                    </Button>
                  ) : null}
                  {q.response_type !== 'pricing' ? (
                    <Button
                      size="sm"
                      variant="primary"
                      busy={stream.busy || busy}
                      disabled={q.draft_in_progress}
                      onClick={() => void act(`/questions/${q.id}/draft`, {}, 'POST', true)}
                    >
                      Generate draft
                    </Button>
                  ) : (
                    <p className="text-xs">Pricing is written by a person.</p>
                  )}
                </div>
              </div>
              {stream.recovering ? (
                <p role="status" className="text-sm text-amber">
                  Connection lost. Checking the saved result; generation continues on the server.
                </p>
              ) : null}
              <ErrorNote error={stream.state.error} />
              {stream.busy ? (
                stream.state.segments.length ? (
                  <SupportSummary
                    summary={summariseSegments(stream.state.segments)}
                    words={wordCount(stream.state.segments.map((s) => s.text).join(' '))}
                    streaming
                    onNext={jumpToNext}
                  />
                ) : null
              ) : answer ? (
                <SupportSummary summary={answer.support_summary} words={answer.word_count} onNext={jumpToNext} />
              ) : null}
              {visibleSegments.length ? <TraceLegend /> : null}
              <div ref={answerRef} className="relative" onMouseUp={captureSelection} onKeyUp={captureSelection}>
                {selection ? (
                  <button
                    type="button"
                    className={`${buttonClasses('primary', 'sm')} absolute z-10 shadow-md`}
                    style={{ top: Math.max(-36, selection.top), left: selection.left }}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      setDraftComment({ anchor: selection.anchor, quote: selection.quote });
                      setSelection(null);
                      setTab('comments');
                      window.getSelection()?.removeAllRanges();
                    }}
                  >
                    <MessageSquarePlus aria-hidden /> Comment
                  </button>
                ) : null}
                <Trace
                  segments={visibleSegments}
                  onSource={setSource}
                  highlights={highlights}
                  activeThread={activeThread}
                  onHighlight={focusThread}
                />
              </div>
              {!answer && !stream.busy ? <p className="text-sm text-muted">Generate a draft or write the first answer below.</p> : null}
              {edit || !answer ? <Editor key={questionId} workspaceId={workspaceId} question={q} onSaved={refresh} /> : null}
              {answer && !stream.busy ? (
                <details className="rounded-md border border-line px-3 py-2">
                  <summary className="cursor-pointer text-sm font-semibold">Verify sentences</summary>
                  <div className="mt-3 space-y-3">
                    {answer.segments.map((s) => (
                      <div className="border-t border-line pt-2" key={s.index}>
                        <p className="text-sm">
                          {s.index + 1}. {s.text}
                        </p>
                        <p className="my-1 text-xs text-muted">
                          {label(s.support_status)}
                          {s.dispute ? ` · Disputed: ${s.dispute.note}` : ''}
                        </p>
                        {['unsupported', 'weak', 'human_authored'].includes(s.support_status) ? (
                          <Button
                            size="sm"
                            onClick={() =>
                              confirmSentence(s.index)
                            }
                          >
                            Confirm accurate
                          </Button>
                        ) : s.support_status === 'supported' ? (
                          <Button
                            size="sm"
                            onClick={() =>
                              setNoteAction({
                                path: `/answers/${answer.id}/segments/${s.index}/dispute`,
                                title: `Dispute sentence ${s.index + 1}`,
                                required: true,
                              })
                            }
                          >
                            Dispute
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
              {answer?.gaps.length ? (
                <div className="rounded-md border border-amber/40 bg-amber-bg/40 px-3 py-2">
                  <h3 className="text-sm font-semibold">Evidence gaps</h3>
                  {answer.gaps.map((g) => (
                    <div className="my-2 flex flex-wrap items-center justify-between gap-2 text-sm" key={g}>
                      <p className="min-w-0 flex-1">{g}</p>
                      {q.gap_acknowledgements?.some(
                        (a) => a.gap.toLowerCase().replace(/\s+/g, ' ').trim() === g.toLowerCase().replace(/\s+/g, ' ').trim(),
                      ) ? (
                        <Badge tone="green">Acknowledged</Badge>
                      ) : (
                        <Button
                          size="sm"
                          onClick={() =>
                            setNoteAction({
                              path: `/questions/${q.id}/gaps/acknowledge`,
                              body: { gap: g },
                              title: 'Acknowledge evidence gap',
                              required: true,
                            })
                          }
                        >
                          Acknowledge gap
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              ) : null}
              {answer?.fact_checklist.length ? (
                <div>
                  <h3 className="text-sm font-semibold">Facts used</h3>
                  {answer.fact_checklist.map((f) => (
                    <p className="my-2 flex flex-wrap items-baseline gap-2 text-sm" key={f.fact_id}>
                      <span className="min-w-0 flex-1">{f.statement}</span>
                      <span className="text-xs text-muted">{f.effective_date ?? 'Undated'}</span>
                      <Badge tone={f.status === 'current' ? 'green' : 'amber'}>{label(f.status)}</Badge>
                    </p>
                  ))}
                </div>
              ) : null}
              {answer?.verbatim ? (
                <div className="rounded border border-line p-3">
                  <h3 className="text-sm font-semibold">Previously submitted wording</h3>
                  <Trace segments={answer.verbatim.segments} onSource={setSource} prefix="verbatim" />
                  <Button
                    size="sm"
                    busy={busy}
                    onClick={() =>
                      void act(`/questions/${q.id}/verbatim`, {
                        source_item_id: answer.verbatim!.source_item_id,
                      })
                    }
                  >
                    Use this wording
                  </Button>
                </div>
              ) : null}
              <details className="rounded-md border border-line px-3 py-2">
                <summary className="cursor-pointer text-sm font-semibold">Version history ({versions.data?.length ?? 0})</summary>
                <select className={`${field} my-3`} value={oldVersion} onChange={(e) => setOldVersion(e.target.value)}>
                  <option value="">Compare an earlier version with the current answer</option>
                  {versions.data?.map((v) => (
                    <option key={v.id} value={v.id}>
                      Version {v.version} · {v.author_name ?? 'AI draft'} · {new Date(v.created_at).toLocaleString('en-GB')}
                    </option>
                  ))}
                </select>
                {previous ? (
                  <div className="whitespace-pre-wrap text-sm">
                    {diffWords(previous.text, answer?.text ?? '').map((part, i) =>
                      part.kind === 'added' ? (
                        <ins key={i} className="bg-tint">
                          {part.text}
                        </ins>
                      ) : part.kind === 'removed' ? (
                        <del key={i} className="bg-red-bg">
                          {part.text}
                        </del>
                      ) : (
                        <span key={i}>{part.text}</span>
                      ),
                    )}
                  </div>
                ) : null}
              </details>
            </section>
            <div className="min-w-0 space-y-4 xl:sticky xl:top-3">
              {source ? (
                <SourcePane key={JSON.stringify(source.locator)} workspaceId={workspaceId} source={source} onClose={() => setSource(null)} />
              ) : null}
              <section className={panel}>
                <div role="tablist" aria-label="Question panels" className="mb-4 grid grid-cols-4 gap-1 rounded-lg bg-soft p-1">
                  {(
                    [
                      ['details', 'Details'],
                      ['comments', `Comments${openComments ? ` (${openComments})` : ''}`],
                      ['assistant', 'Assistant'],
                      ['activity', 'Activity'],
                    ] as const
                  ).map(([key, name]) => (
                    <button
                      key={key}
                      role="tab"
                      aria-selected={tab === key}
                      className={`rounded-md px-1 py-1.5 text-xs ${tab === key ? 'bg-bg font-semibold shadow-sm' : 'text-muted hover:text-ink'}`}
                      onClick={() => setTab(key)}
                    >
                      {name}
                    </button>
                  ))}
                </div>
                {tab === 'details' ? (
                  <div className="space-y-5">
                    <People
                      workspaceId={workspaceId}
                      questionId={q.id}
                      members={members}
                      owner={q.assignee ?? null}
                      busy={busy}
                      onOwner={(name) => void act(`/questions/${q.id}`, { assignee: name }, 'PATCH')}
                    />
                    <div className="space-y-2 border-t border-line pt-4">
                      <h3 className="text-sm font-semibold">Evidence to attach</h3>
                      {q.evidence?.map((e) => (
                        <div className="flex items-start justify-between gap-2 text-xs" key={e.document_id}>
                          <div className="min-w-0">
                            <Link className="text-accent wrap-anywhere" href={`/w/${workspaceId}/library/${e.document_id}`}>
                              {e.filename}
                            </Link>
                            {e.note ? <p className="text-muted">{e.note}</p> : null}
                          </div>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              try {
                                const response = await fetch(`/api/w/${workspaceId}/backend/questions/${q.id}/evidence/${e.document_id}`, {
                                  method: 'DELETE',
                                  headers: { 'x-ten-request': '1' },
                                });
                                if (!response.ok) throw new Error('Evidence could not be removed.');
                                refresh();
                              } catch (e) {
                                setError((e as Error).message);
                              }
                            }}
                          >
                            Remove
                          </Button>
                        </div>
                      ))}
                      <form
                        className="space-y-2"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const f = new FormData(e.currentTarget);
                          void act(`/questions/${q.id}/evidence`, {
                            document_id: f.get('document'),
                            note: f.get('note') || null,
                          });
                        }}
                      >
                        <Field title="Library document">
                          <select required className={field} name="document">
                            <option value="">Choose a document</option>
                            {documents.data?.map((d) => (
                              <option key={d.id} value={d.id}>
                                {d.filename}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field title="Note (optional)">
                          <input className={field} name="note" />
                        </Field>
                        <Button size="sm" type="submit" busy={busy}>
                          Attach evidence
                        </Button>
                      </form>
                    </div>
                    <p className="border-t border-line pt-4 text-xs text-muted">Topics: {q.topics.map((t) => label(t)).join(', ') || 'none'}</p>
                  </div>
                ) : null}
                {tab === 'comments' ? (
                  <CommentsPanel
                    workspaceId={workspaceId}
                    me={me}
                    members={members}
                    threads={placed}
                    error={comments.error}
                    endpoint={comments.endpoint}
                    answerId={answer?.id ?? null}
                    draft={draftComment}
                    onDraftDone={() => setDraftComment(null)}
                    activeThread={activeThread}
                    onSelect={setActiveThread}
                    onChanged={() => void reloadComments()}
                  />
                ) : null}
                {tab === 'assistant' ? (
                  q.thread_id ? (
                    <Assistant
                      key={q.thread_id}
                      workspaceId={workspaceId}
                      threadId={q.thread_id}
                      question={q}
                      onSaved={refresh}
                      onSource={setSource}
                    />
                  ) : (
                    <p className="text-sm text-muted">The assistant is not available for this question.</p>
                  )
                ) : null}
                {tab === 'activity' ? (
                  <div className="space-y-4">
                    <form
                      className="space-y-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void act(`/questions/${q.id}/comments`, { text: note });
                        setNote('');
                      }}
                    >
                      <label className="block text-xs text-muted" htmlFor="question-note">
                        Note on the whole question
                      </label>
                      <MentionTextarea
                        id="question-note"
                        members={members}
                        required
                        value={note}
                        onValue={setNote}
                        placeholder="Type @ to mention someone."
                      />
                      <Button size="sm" type="submit" busy={busy}>
                        Add note
                      </Button>
                    </form>
                    {q.comments?.map((c) => (
                      <div className="text-sm" key={c.id}>
                        <p className="text-xs">
                          <strong>{c.author}</strong>
                        </p>
                        <MentionText text={c.text} members={members} />
                      </div>
                    ))}
                    <ol className="space-y-1.5 border-t border-line pt-3">
                      {events.data?.map((e) => (
                        <li className="text-xs text-muted" key={e.id}>
                          <span className="text-ink">{label(e.event_type)}</span> · {e.actor} ·{' '}
                          {new Date(e.created_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </section>
            </div>
          </div>
          <Dialog
            open={Boolean(noteAction)}
            onOpenChange={(open) => {
              if (!open) setNoteAction(null);
            }}
            title={noteAction?.title ?? 'Review note'}
            description={noteAction?.description ?? 'Your name and this note will be recorded in the activity log.'}
          >
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                if (noteAction)
                  void act(noteAction.path, {
                    ...noteAction.body,
                    note: f.get('note') || null,
                  });
                setNoteAction(null);
              }}
            >
              <Field title="Note">
                <textarea className={field} name="note" required={noteAction?.required} />
              </Field>
              <Button variant="primary" type="submit">
                Confirm
              </Button>
            </form>
          </Dialog>
          <Dialog
            open={Boolean(displace)}
            onOpenChange={(open) => {
              if (!open) setDisplace(null);
            }}
            title="Replace the current answer?"
            description="A person has worked on this answer. The new draft becomes current and moves it back to AI draft. The previous answer stays in version history."
          >
            <Button
              variant="primary"
              onClick={() => {
                if (displace) {
                  void act(displace.path, { ...displace.body, confirm_displace: true }, 'POST', displace.stream);
                  setDisplace(null);
                }
              }}
            >
              Replace current answer
            </Button>
          </Dialog>
        </>
      ) : null}
    </>
  );
}
function Assistant({
  workspaceId,
  threadId,
  question,
  onSaved,
  onSource,
}: {
  workspaceId: string;
  threadId: string;
  question: S['QuestionDetail'];
  onSaved: () => void;
  onSource: (s: Source) => void;
}) {
  const thread = useResource<S['ThreadRecord']>(workspaceId, `/threads/${threadId}`),
    stream = useDraftStream(workspaceId);
  // A save-as-answer or verbatim request the backend refused with a displacement 409, waiting for confirmation.
  const [error, setError] = useState<string | null>(null),
    [pending, setPending] = useState<{ path: string; body: Record<string, unknown>; description: string } | null>(null),
    [busy, setBusy] = useState(false);
  const { reload: reloadThread } = thread;
  useEffect(() => {
    if (stream.state.result) void reloadThread();
  }, [stream.state.result, reloadThread]);
  useEffect(() => {
    if (!thread.data?.reply_in_progress || stream.busy) return;
    const timer = setInterval(() => void reloadThread(), 2000);
    return () => clearInterval(timer);
  }, [thread.data?.reply_in_progress, stream.busy, reloadThread]);
  /**
   * Make an AI version current: save a reply as the answer, or use previously submitted wording. The
   * request goes without `confirm_displace` first; only when the backend answers 409 `displacement`
   * (a person has worked on the current answer) is the person asked, and the confirmed retry carries it.
   */
  async function replaceAnswer(path: string, body: Record<string, unknown>, description: string, confirmed = false) {
    setBusy(true);
    setError(null);
    try {
      await backendJson(workspaceId, path, 'POST', confirmed ? { ...body, confirm_displace: true } : body);
      await reloadThread();
      onSaved();
      setPending(null);
    } catch (e) {
      if (!confirmed && e instanceof BackendRequestError && e.detail.code === 'displacement') setPending({ path, body, description });
      else {
        setPending(null);
        setError((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }
  const save = (id: string) =>
    replaceAnswer(
      `/messages/${id}/save-as-answer`,
      {},
      'This reply will become the current AI draft. The existing answer remains in version history.',
    );
  return (
    <section className="space-y-4">
      <h2 className="sr-only">Question assistant</h2>
      <p className="text-xs text-muted">Ask for changes to the draft. Replies are traced to sources like the answer, and you can save one as the answer.</p>
      <ErrorNote error={thread.error ?? error ?? stream.state.error} />
      {thread.data?.messages?.map((m) => (
        <article className="border-b border-line pb-3" key={m.id}>
          <p className="mb-2 text-xs font-semibold">{m.role === 'assistant' ? 'Assistant' : 'Team'}</p>
          {m.segments ? (
            <Trace segments={m.segments} onSource={onSource} prefix={m.id} />
          ) : (
            <p className="whitespace-pre-wrap text-sm">{m.content}</p>
          )}
          {m.gaps?.map((g) => (
            <p key={g} className="mt-2 text-xs text-amber">
              Gap: {g}
            </p>
          ))}
          {m.fact_checklist?.map((f) => (
            <p key={f.fact_id} className="mt-2 text-xs">
              {f.statement} · {f.status}
            </p>
          ))}
          {m.verbatim ? (
            <div className="my-3 rounded border border-line p-2">
              <p className="text-xs font-semibold">Previously submitted wording</p>
              <Trace segments={m.verbatim.segments} onSource={onSource} prefix={`${m.id}-verbatim`} />
              <Button
                size="sm"
                busy={busy}
                onClick={() =>
                  void replaceAnswer(
                    `/questions/${question.id}/verbatim`,
                    { source_item_id: m.verbatim!.source_item_id },
                    'The previously submitted wording will become the current AI draft. The existing answer remains in version history.',
                  )
                }
              >
                Use this wording
              </Button>
            </div>
          ) : null}
          {m.role === 'assistant' && question.response_type !== 'pricing' ? (
            <Button className="mt-3" size="sm" busy={busy} onClick={() => void save(m.id)}>
              Save as answer
            </Button>
          ) : null}
        </article>
      ))}
      {stream.busy ? (
        <>
          <p role="status" className="text-xs">
            {stream.recovering ? 'Checking the saved reply…' : 'Drafting reply…'}
          </p>
          <Trace segments={stream.state.segments} onSource={onSource} prefix="reply" />
        </>
      ) : null}
      <form
        className="space-y-2"
        onSubmit={async (e) => {
          e.preventDefault();
          const form = e.currentTarget;
          const content = String(new FormData(form).get('content'));
          const previous = new Set(thread.data?.messages?.map((m) => m.id));
          setError(null);
          try {
            await stream.start(`/threads/${threadId}/messages`, { content }, async () => {
              const next = await backendJson<S['ThreadRecord']>(workspaceId, `/threads/${threadId}`);
              return {
                inProgress: next.reply_in_progress ?? false,
                result: [...(next.messages ?? [])].reverse().find((m) => m.role === 'assistant' && !previous.has(m.id)) ?? null,
              };
            });
            form.reset();
            void reloadThread();
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        <Field title="Instruction">
          <textarea className={field} name="content" required placeholder="For example, make this shorter." />
        </Field>
        <Button type="submit" busy={stream.busy} disabled={thread.data?.reply_in_progress || question.response_type === 'pricing'}>
          Ask assistant
        </Button>
      </form>
      <Dialog
        open={Boolean(pending)}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title="Replace the current answer?"
        description={`A person has worked on this answer. ${pending?.description ?? ''}`}
      >
        <Button
          variant="primary"
          busy={busy}
          onClick={() => {
            if (pending) void replaceAnswer(pending.path, pending.body, pending.description, true);
          }}
        >
          Replace current answer
        </Button>
      </Dialog>
    </section>
  );
}
