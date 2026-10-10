// The specification view: one row per requirement the buyer's documents state, rated Green, Amber
// or Red. The backend stores the supplier's compliance class (A compliant now, B compliant by a
// date, C cannot comply); Green, Amber and Red are display names only and are never sent or stored.
// Everything here reads the rows of the single `GET /tenders/{id}/requirements` call.

export type ComplianceClass = 'A' | 'B' | 'C';
export type Rating = 'green' | 'amber' | 'red' | 'unrated';

export type SpecRequirement = {
  id: string;
  ref?: string | null;
  text: string;
  order_index: number;
  priority?: string | null;
  compliance_class?: string | null;
  suggested_class?: string | null;
  comment?: string | null;
  owner?: string | null;
  document_filename?: string;
};

export const RATING_ORDER: readonly Rating[] = ['green', 'amber', 'red', 'unrated'];
export const PRIORITY_ORDER = ['must', 'should', 'could'] as const;

const RATING_OF_CLASS: Record<ComplianceClass, Rating> = { A: 'green', B: 'amber', C: 'red' };
const CLASS_OF_RATING: Record<Exclude<Rating, 'unrated'>, ComplianceClass> = { green: 'A', amber: 'B', red: 'C' };

export const RATING_LABELS: Record<Rating, string> = { green: 'Green', amber: 'Amber', red: 'Red', unrated: 'Not rated' };
export const RATING_MEANINGS: Record<Rating, string> = {
  green: 'Compliant now',
  amber: 'Compliant by a date, in part or with a workaround',
  red: 'Cannot comply',
  unrated: 'No rating confirmed yet',
};
export const PRIORITY_LABELS: Record<string, string> = { must: 'Must', should: 'Should', could: 'Could' };

/** The display name of a stored class; anything else (null, unknown) is not rated. */
export function ratingOf(value: string | null | undefined): Rating {
  return value && value in RATING_OF_CLASS ? RATING_OF_CLASS[value as ComplianceClass] : 'unrated';
}

/** The class to send for a chosen rating; null clears it. */
export function classOf(rating: Rating): ComplianceClass | null {
  return rating === 'unrated' ? null : CLASS_OF_RATING[rating];
}

export type RatingCounts = { green: number; amber: number; red: number; unrated: number; total: number };

/** Counts by confirmed rating. A suggestion never counts until a person accepts it. */
export function summariseRequirements(rows: readonly Pick<SpecRequirement, 'compliance_class' | 'suggested_class'>[]): RatingCounts & {
  suggested_unrated: number;
} {
  const counts = { green: 0, amber: 0, red: 0, unrated: 0, total: rows.length, suggested_unrated: 0 };
  for (const row of rows) {
    const rating = ratingOf(row.compliance_class);
    counts[rating] += 1;
    if (rating === 'unrated' && ratingOf(row.suggested_class) !== 'unrated') counts.suggested_unrated += 1;
  }
  return counts;
}

/** "41 Green · 6 Amber · 2 Red · 9 not rated"; a part with no requirements is left out. */
export function ratingSummaryText(counts: RatingCounts): string {
  if (!counts.total) return 'No requirements yet';
  const parts = [
    counts.green ? `${counts.green} Green` : null,
    counts.amber ? `${counts.amber} Amber` : null,
    counts.red ? `${counts.red} Red` : null,
    counts.unrated ? `${counts.unrated} not rated` : null,
  ].filter(Boolean);
  return parts.join(' · ');
}

/**
 * Filter values. `''` means any. `rating` is a rating or `unrated`; `priority` is must, should, could or
 * `'-'` for not stated. `attention` keeps the rows a bid lead must look at first: every Red, and every
 * Must that nobody has rated yet.
 */
export type RequirementFilters = { search: string; rating: string; priority: string; attention: boolean };

export const NO_REQUIREMENT_FILTERS: RequirementFilters = { search: '', rating: '', priority: '', attention: false };

export function hasRequirementFilters(filters: RequirementFilters): boolean {
  return Boolean(filters.search || filters.rating || filters.priority || filters.attention);
}

export function needsAttention(row: Pick<SpecRequirement, 'compliance_class' | 'priority'>): boolean {
  const rating = ratingOf(row.compliance_class);
  return rating === 'red' || (rating === 'unrated' && row.priority === 'must');
}

export function filterRequirements<T extends SpecRequirement>(rows: readonly T[], filters: RequirementFilters): T[] {
  const search = filters.search.trim().toLowerCase();
  return rows.filter(
    (row) =>
      (!filters.rating || ratingOf(row.compliance_class) === filters.rating) &&
      (!filters.priority || (filters.priority === '-' ? !row.priority : row.priority === filters.priority)) &&
      (!filters.attention || needsAttention(row)) &&
      (!search ||
        `${row.ref ?? ''} ${row.text} ${row.comment ?? ''} ${row.owner ?? ''} ${row.document_filename ?? ''}`.toLowerCase().includes(search)),
  );
}

export type RequirementSortKey = 'order' | 'priority' | 'rating' | 'owner';
export type RequirementSort = { key: RequirementSortKey; direction: 'asc' | 'desc' };

export const REQUIREMENT_SORT_LABELS: Record<RequirementSortKey, string> = {
  order: 'Document order',
  priority: 'Priority',
  rating: 'RAG rating',
  owner: 'Owner',
};

// Red first when sorting by rating: the rows that can fail a bid lead the list.
const RATING_SORT: readonly Rating[] = ['red', 'unrated', 'amber', 'green'];

function sortValue(row: SpecRequirement, key: RequirementSortKey): number | string | null {
  switch (key) {
    case 'order':
      return row.order_index;
    case 'priority': {
      const i = PRIORITY_ORDER.indexOf((row.priority ?? '') as (typeof PRIORITY_ORDER)[number]);
      return i < 0 ? null : i;
    }
    case 'rating':
      return RATING_SORT.indexOf(ratingOf(row.compliance_class));
    case 'owner':
      return row.owner ? row.owner.toLowerCase() : null;
  }
}

/** Rows sorted by one column. Missing values go last in either direction and ties keep document order. */
export function sortRequirements<T extends SpecRequirement>(rows: readonly T[], sort: RequirementSort): T[] {
  const sign = sort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = sortValue(a, sort.key),
      y = sortValue(b, sort.key);
    if (x !== y) {
      if (x == null) return 1;
      if (y == null) return -1;
      const compared = typeof x === 'string' && typeof y === 'string' ? x.localeCompare(y) : Number(x) - Number(y);
      if (compared) return compared * sign;
    }
    return a.order_index - b.order_index;
  });
}

/** Choosing a column header: the same column flips direction, a new one starts ascending. */
export function nextRequirementSort(current: RequirementSort, key: RequirementSortKey): RequirementSort {
  if (current.key === key) return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
  return { key, direction: 'asc' };
}
