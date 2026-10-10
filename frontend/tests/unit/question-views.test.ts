import { describe, expect, it } from 'vitest';
import {
  NO_FILTERS,
  filterQuestions,
  hasFilters,
  nextSort,
  sectionsOf,
  sortQuestions,
  wordUsage,
  type TableQuestion,
} from '@/lib/question-table';
import { JOB_POLL_FAILURES_BEFORE_ERROR, jobRetryDelay, shouldShowPollError } from '@/lib/job-poll';
import { formatEvidenceScore, supportedText } from '@/lib/format';
import { summariseSegments } from '@/features/backend/trace';
import { wordsText } from '@/features/backend/question-views';
import { appliedPartOfRefusedPatch } from '@/server/backend/refused-patch';

// The tender workspace's table, the evidence-coverage figure, the live summary while a draft streams and
// job polling. Everything here is pure so the behaviour the bid lead relies on is pinned without a DOM.

const q = (over: Partial<TableQuestion> & { id: string }): TableQuestion & { id: string } => ({
  section: 'Clinical safety',
  number: '1.1',
  text: 'Describe your DCB0129 approach.',
  order_index: 0,
  status: 'not_started',
  coverage: 'unknown',
  assignee: null,
  weighting: null,
  word_limit: null,
  current_answer: null,
  ...over,
});
const answer = (word_count: number, score: number | null) => ({ word_count, support_summary: { score } });

const rows = [
  q({ id: 'a', order_index: 0, number: '1.1', status: 'approved', coverage: 'covered', assignee: 'Sam', weighting: 5, word_limit: 200, current_answer: answer(150, 0.9) }),
  q({ id: 'b', order_index: 1, number: '1.2', status: 'ai_draft', coverage: 'partial', weighting: 15, word_limit: 100, current_answer: answer(120, 0.5) }),
  q({ id: 'c', order_index: 2, number: '2.1', section: 'Information governance', status: 'not_started', coverage: 'new', assignee: 'alex', word_limit: 300 }),
  q({ id: 'd', order_index: 3, number: '2.2', section: 'Information governance', status: 'writer_edited', coverage: 'covered', assignee: 'Jordan', weighting: 10, current_answer: answer(80, 1) }),
];
const ids = (list: { id: string }[]) => list.map((r) => r.id);

describe('filtering the question table', () => {
  it('keeps every row with no filters and reports none set', () => {
    expect(ids(filterQuestions(rows, NO_FILTERS))).toEqual(['a', 'b', 'c', 'd']);
    expect(hasFilters(NO_FILTERS)).toBe(false);
  });

  it('filters to questions not yet approved (MVP step 5)', () => {
    const filters = { ...NO_FILTERS, status: 'unapproved' };
    expect(ids(filterQuestions(rows, filters))).toEqual(['b', 'c', 'd']);
    expect(hasFilters(filters)).toBe(true);
  });

  it('filters by section, coverage and owner, with "-" meaning none', () => {
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, section: 'Information governance' }))).toEqual(['c', 'd']);
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, coverage: 'covered' }))).toEqual(['a', 'd']);
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, assignee: '-' }))).toEqual(['b']);
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, assignee: 'Sam', status: 'unapproved' }))).toEqual([]);
  });

  it('searches number, text and owner case-insensitively', () => {
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, search: '2.2' }))).toEqual(['d']);
    expect(ids(filterQuestions(rows, { ...NO_FILTERS, search: 'jordan' }))).toEqual(['d']);
  });

  it('lists sections once, in the buyer’s order', () => {
    expect(sectionsOf([...rows].reverse())).toEqual(['Clinical safety', 'Information governance']);
  });
});

describe('sorting the question table', () => {
  it('sorts by buyer’s order and by weighting, highest first, missing values last', () => {
    expect(ids(sortQuestions([...rows].reverse(), { key: 'order', direction: 'asc' }))).toEqual(['a', 'b', 'c', 'd']);
    expect(ids(sortQuestions(rows, { key: 'weighting', direction: 'desc' }))).toEqual(['b', 'd', 'a', 'c']);
    expect(ids(sortQuestions(rows, { key: 'weighting', direction: 'asc' }))).toEqual(['a', 'd', 'b', 'c']);
  });

  it('sorts coverage and status in their own order, not alphabetically', () => {
    expect(ids(sortQuestions(rows, { key: 'coverage', direction: 'asc' }))).toEqual(['a', 'd', 'b', 'c']);
    expect(ids(sortQuestions(rows, { key: 'status', direction: 'desc' }))).toEqual(['a', 'd', 'b', 'c']);
  });

  it('sorts owners case-insensitively with unowned questions last', () => {
    expect(ids(sortQuestions(rows, { key: 'assignee', direction: 'asc' }))).toEqual(['c', 'd', 'a', 'b']);
  });

  it('sorts by evidence coverage, unanswered questions last in either direction', () => {
    expect(ids(sortQuestions(rows, { key: 'support', direction: 'asc' }))).toEqual(['b', 'a', 'd', 'c']);
    expect(ids(sortQuestions(rows, { key: 'support', direction: 'desc' }))).toEqual(['d', 'a', 'b', 'c']);
  });

  it('sorts by words against the limit, over-limit answers first and questions without a limit last', () => {
    expect(wordUsage(rows[1])).toBeCloseTo(1.2);
    expect(wordUsage(rows[2])).toBe(0);
    expect(wordUsage(rows[3])).toBeNull();
    expect(ids(sortQuestions(rows, { key: 'words', direction: 'desc' }))).toEqual(['b', 'a', 'c', 'd']);
  });

  it('flips direction on the same column and starts a new column at its default', () => {
    expect(nextSort({ key: 'support', direction: 'asc' }, 'support')).toEqual({ key: 'support', direction: 'desc' });
    expect(nextSort({ key: 'order', direction: 'asc' }, 'weighting')).toEqual({ key: 'weighting', direction: 'desc' });
    expect(nextSort({ key: 'weighting', direction: 'desc' }, 'support')).toEqual({ key: 'support', direction: 'asc' });
  });
});

describe('evidence coverage', () => {
  it('is shown as a decimal to two places, never a percentage', () => {
    expect(formatEvidenceScore(0.91)).toBe('0.91');
    expect(formatEvidenceScore(1)).toBe('1.00');
    expect(formatEvidenceScore(0)).toBe('0.00');
    expect(formatEvidenceScore(null)).toBe('—');
    expect(supportedText({ supported: 14, substantive: 16 })).toBe('14 of 16 supported');
  });

  it('shows the word limit on cards even before there is an answer', () => {
    expect(wordsText({ word_limit: 250, current_answer: null })).toBe('No answer yet · 250 word limit');
    expect(wordsText({ word_limit: null, current_answer: null })).toBe('No answer yet');
    expect(wordsText({ word_limit: 250, current_answer: { word_count: 120 } as never })).toBe('120 / 250 words');
  });

  it('is computed from the streamed sentences while a draft streams, pending counted on its own', () => {
    const live = summariseSegments([
      { kind: 'substantive', support_status: 'supported' },
      { kind: 'substantive', support_status: 'pending' },
      { kind: 'connective', support_status: 'connective' },
      { kind: 'substantive', support_status: 'unsupported' },
      { kind: 'substantive', support_status: 'pending' },
    ]);
    expect(live).toEqual({ substantive: 4, supported: 1, needs_attention: 3, pending: 2, score: 0.25 });
    expect(summariseSegments([])).toEqual({ substantive: 0, supported: 0, needs_attention: 0, pending: 0, score: null });
  });
});

describe('job polling', () => {
  it('backs off after failed requests, capped at thirty seconds', () => {
    expect([1, 2, 3, 4, 5, 10].map(jobRetryDelay)).toEqual([2000, 4000, 8000, 16000, 30000, 30000]);
  });

  it('shows an error only after a run of failures', () => {
    expect(shouldShowPollError(1)).toBe(false);
    expect(shouldShowPollError(JOB_POLL_FAILURES_BEFORE_ERROR - 1)).toBe(false);
    expect(shouldShowPollError(JOB_POLL_FAILURES_BEFORE_ERROR)).toBe(true);
  });
});

describe('a refused status in a question PATCH', () => {
  it('notifies the assignment the backend saved alongside the refusal', () => {
    const refusal = { detail: 'Blocked', to: 'approved', blockers: [], question: { assignee: 'Sam' } };
    expect(appliedPartOfRefusedPatch({ status: 'approved', assignee: 'Sam' }, refusal)).toEqual({ assignee: 'Sam' });
  });

  it('notifies nothing when only the status was sent, or the assignee did not land', () => {
    expect(appliedPartOfRefusedPatch({ status: 'approved' }, { question: { assignee: 'Sam' } })).toBeNull();
    expect(appliedPartOfRefusedPatch({ status: 'approved', assignee: 'Sam' }, { question: { assignee: 'Alex' } })).toBeNull();
    expect(appliedPartOfRefusedPatch({ status: 'approved', assignee: 'Sam' }, { detail: 'Blocked', blockers: [] })).toBeNull();
    expect(appliedPartOfRefusedPatch({ status: 'approved', assignee: 'Sam' }, null)).toBeNull();
  });
});
