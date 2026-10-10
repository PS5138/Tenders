import { describe, expect, it } from 'vitest';
import {
  NO_REQUIREMENT_FILTERS,
  classOf,
  filterRequirements,
  hasRequirementFilters,
  needsAttention,
  nextRequirementSort,
  ratingOf,
  ratingSummaryText,
  sortRequirements,
  summariseRequirements,
  type SpecRequirement,
} from '@/lib/requirements';

// The Specification tab: the RAG rating is the stored compliance class shown as Green, Amber or Red.
// A suggestion never counts until it is accepted. Everything here is pure, so it is pinned without a DOM.

const r = (over: Partial<SpecRequirement> & { id: string }): SpecRequirement & { id: string } => ({
  ref: null,
  text: 'The supplier must host data in the UK.',
  order_index: 0,
  priority: null,
  compliance_class: null,
  suggested_class: null,
  comment: null,
  owner: null,
  document_filename: 'specification.docx',
  ...over,
});

const rows = [
  r({ id: 'a', order_index: 0, ref: '1.1', priority: 'must', compliance_class: 'A', owner: 'Sam' }),
  r({ id: 'b', order_index: 1, ref: '1.2', priority: 'must', suggested_class: 'A', text: 'Hosting must meet ISO 27001.' }),
  r({ id: 'c', order_index: 2, ref: '1.3', priority: 'should', compliance_class: 'C', comment: 'No on-site support', owner: 'alex' }),
  r({ id: 'd', order_index: 3, ref: '2.1', priority: null, compliance_class: 'B', document_filename: 'contract-terms.docx' }),
  r({ id: 'e', order_index: 4, ref: '2.2', priority: 'could' }),
];
const ids = (list: { id: string }[]) => list.map((x) => x.id);

describe('the RAG rating', () => {
  it('maps the stored class to a display name and back, never storing a colour', () => {
    expect(['A', 'B', 'C', null, undefined, 'X'].map((v) => ratingOf(v))).toEqual(['green', 'amber', 'red', 'unrated', 'unrated', 'unrated']);
    expect([classOf('green'), classOf('amber'), classOf('red'), classOf('unrated')]).toEqual(['A', 'B', 'C', null]);
  });

  it('counts confirmed ratings only; a suggestion is counted apart and stays unrated', () => {
    expect(summariseRequirements(rows)).toEqual({ green: 1, amber: 1, red: 1, unrated: 2, total: 5, suggested_unrated: 1 });
    expect(summariseRequirements([])).toEqual({ green: 0, amber: 0, red: 0, unrated: 0, total: 0, suggested_unrated: 0 });
  });

  it('reads the summary as words, leaving out empty parts', () => {
    expect(ratingSummaryText({ green: 41, amber: 6, red: 2, unrated: 9, total: 58 })).toBe('41 Green · 6 Amber · 2 Red · 9 not rated');
    expect(ratingSummaryText({ green: 3, amber: 0, red: 0, unrated: 1, total: 4 })).toBe('3 Green · 1 not rated');
    expect(ratingSummaryText({ green: 0, amber: 0, red: 0, unrated: 0, total: 0 })).toBe('No requirements yet');
  });
});

describe('filtering the specification', () => {
  it('keeps every row with no filters', () => {
    expect(ids(filterRequirements(rows, NO_REQUIREMENT_FILTERS))).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect(hasRequirementFilters(NO_REQUIREMENT_FILTERS)).toBe(false);
  });

  it('filters by rating, including not rated, and by priority with "-" meaning not stated', () => {
    expect(ids(filterRequirements(rows, { ...NO_REQUIREMENT_FILTERS, rating: 'red' }))).toEqual(['c']);
    expect(ids(filterRequirements(rows, { ...NO_REQUIREMENT_FILTERS, rating: 'unrated' }))).toEqual(['b', 'e']);
    expect(ids(filterRequirements(rows, { ...NO_REQUIREMENT_FILTERS, priority: 'must' }))).toEqual(['a', 'b']);
    expect(ids(filterRequirements(rows, { ...NO_REQUIREMENT_FILTERS, priority: '-' }))).toEqual(['d']);
  });

  it('keeps Reds and unrated Musts for the quick filter', () => {
    expect(rows.map(needsAttention)).toEqual([false, true, true, false, false]);
    const filters = { ...NO_REQUIREMENT_FILTERS, attention: true };
    expect(ids(filterRequirements(rows, filters))).toEqual(['b', 'c']);
    expect(hasRequirementFilters(filters)).toBe(true);
  });

  it('searches the reference, text, comment, owner and document case-insensitively', () => {
    const search = (text: string) => ids(filterRequirements(rows, { ...NO_REQUIREMENT_FILTERS, search: text }));
    expect(search('2.1')).toEqual(['d']);
    expect(search('iso 27001')).toEqual(['b']);
    expect(search('ON-SITE')).toEqual(['c']);
    expect(search('ALEX')).toEqual(['c']);
    expect(search('contract-terms')).toEqual(['d']);
  });
});

describe('sorting the specification', () => {
  it('sorts by document order', () => {
    expect(ids(sortRequirements([...rows].reverse(), { key: 'order', direction: 'asc' }))).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect(ids(sortRequirements(rows, { key: 'order', direction: 'desc' }))).toEqual(['e', 'd', 'c', 'b', 'a']);
  });

  it('sorts Reds first by rating, then unrated, Amber and Green, ties in document order', () => {
    expect(ids(sortRequirements(rows, { key: 'rating', direction: 'asc' }))).toEqual(['c', 'b', 'e', 'd', 'a']);
  });

  it('sorts priority Must, Should, Could with no priority last in either direction', () => {
    expect(ids(sortRequirements(rows, { key: 'priority', direction: 'asc' }))).toEqual(['a', 'b', 'c', 'e', 'd']);
    expect(ids(sortRequirements(rows, { key: 'priority', direction: 'desc' }))).toEqual(['e', 'c', 'a', 'b', 'd']);
  });

  it('sorts owners case-insensitively with unowned rows last', () => {
    expect(ids(sortRequirements(rows, { key: 'owner', direction: 'asc' }))).toEqual(['c', 'a', 'b', 'd', 'e']);
  });

  it('flips direction on the same column and starts a new column ascending', () => {
    expect(nextRequirementSort({ key: 'rating', direction: 'asc' }, 'rating')).toEqual({ key: 'rating', direction: 'desc' });
    expect(nextRequirementSort({ key: 'rating', direction: 'desc' }, 'priority')).toEqual({ key: 'priority', direction: 'asc' });
  });
});
