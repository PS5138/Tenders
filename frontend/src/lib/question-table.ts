// Filtering and sorting behind the tender workspace's question views. Everything reads the rows of
// the single `GET /tenders/{id}/questions` call; nothing here fetches per question.

export type TableQuestion = {
  section: string;
  number: string;
  text: string;
  order_index: number;
  status: string;
  coverage: string;
  assignee?: string | null;
  weighting?: number | null;
  word_limit?: number | null;
  current_answer?: { word_count: number; support_summary: { score?: number | null } } | null;
};

export const STATUS_ORDER = ['not_started', 'ai_draft', 'writer_edited', 'sme_verified', 'approved'] as const;
export const COVERAGE_ORDER = ['covered', 'partial', 'new', 'unknown'] as const;

// Compliance class (Green, Amber, Red) belongs to the specification requirements, not to the
// questions: see `@/lib/requirements`. Questions keep coverage.

/** Filter values. `''` means any; `'-'` means none (no owner); status `'unapproved'` is every status but approved. */
export type QuestionFilters = {
  search: string;
  section: string;
  status: string;
  coverage: string;
  assignee: string;
};

export const NO_FILTERS: QuestionFilters = { search: '', section: '', status: '', coverage: '', assignee: '' };

export function hasFilters(filters: QuestionFilters): boolean {
  return Object.values(filters).some(Boolean);
}

export function filterQuestions<T extends TableQuestion>(rows: readonly T[], filters: QuestionFilters): T[] {
  const search = filters.search.trim().toLowerCase();
  return rows.filter(
    (q) =>
      (!filters.section || q.section === filters.section) &&
      (!filters.status || (filters.status === 'unapproved' ? q.status !== 'approved' : q.status === filters.status)) &&
      (!filters.coverage || q.coverage === filters.coverage) &&
      (!filters.assignee || (filters.assignee === '-' ? !q.assignee : q.assignee === filters.assignee)) &&
      (!search || `${q.section} ${q.number} ${q.text} ${q.assignee ?? ''}`.toLowerCase().includes(search)),
  );
}

export type SortKey = 'order' | 'weighting' | 'coverage' | 'assignee' | 'status' | 'support' | 'words';
export type SortDirection = 'asc' | 'desc';
export type QuestionSort = { key: SortKey; direction: SortDirection };

export const SORT_LABELS: Record<SortKey, string> = {
  order: 'Buyer’s order',
  weighting: 'Weighting',
  coverage: 'Coverage',
  assignee: 'Owner',
  status: 'Status',
  support: 'Evidence coverage',
  words: 'Words against limit',
};

/** The direction a column sorts in when first chosen: the most useful end first. */
export const DEFAULT_DIRECTION: Record<SortKey, SortDirection> = {
  order: 'asc',
  weighting: 'desc',
  coverage: 'asc',
  assignee: 'asc',
  status: 'asc',
  support: 'asc',
  words: 'desc',
};

/** Choosing a column: the same column flips direction, a new one starts at its default. */
export function nextSort(current: QuestionSort, key: SortKey): QuestionSort {
  if (current.key === key) return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
  return { key, direction: DEFAULT_DIRECTION[key] };
}

/** Words used as a share of the limit; null when the question has no limit. An unanswered question uses none. */
export function wordUsage(q: TableQuestion): number | null {
  if (!q.word_limit) return null;
  return (q.current_answer?.word_count ?? 0) / q.word_limit;
}

const rank = (order: readonly string[], value: string | null | undefined) => {
  const i = value == null ? -1 : order.indexOf(value);
  return i < 0 ? null : i;
};

function sortValue(q: TableQuestion, key: SortKey): number | string | null {
  switch (key) {
    case 'order':
      return q.order_index;
    case 'weighting':
      return q.weighting ?? null;
    case 'coverage':
      return rank(COVERAGE_ORDER, q.coverage);
    case 'assignee':
      return q.assignee ? q.assignee.toLowerCase() : null;
    case 'status':
      return rank(STATUS_ORDER, q.status);
    case 'support':
      return q.current_answer?.support_summary.score ?? null;
    case 'words':
      return wordUsage(q);
  }
}

/**
 * Rows sorted by one column. Missing values (no weighting, no owner, no answer, no limit) always go
 * last whichever the direction, and ties keep the buyer's order so the table never reshuffles.
 */
export function sortQuestions<T extends TableQuestion>(rows: readonly T[], sort: QuestionSort): T[] {
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

/** Sections in the buyer's order, each listed once. */
export function sectionsOf(rows: readonly TableQuestion[]): string[] {
  return [...new Set([...rows].sort((a, b) => a.order_index - b.order_index).map((q) => q.section))];
}
