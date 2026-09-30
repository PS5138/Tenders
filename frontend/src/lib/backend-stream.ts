import type { components } from '@/server/backend/schema';

type Schema = components['schemas'];
export type Segment = Schema['SegmentRecord'];
export type Answer = Schema['AnswerRecord'];
export type Message = Schema['ThreadMessage'];
export type DraftEvent =
  | ({ type: 'verbatim' } & Schema['VerbatimOffer'])
  | ({ type: 'segment' } & Segment)
  | { type: 'gaps'; gaps: string[] }
  | { type: 'fact_checklist'; fact_checklist: Schema['FactChecklistEntry'][] }
  | {
      type: 'support';
      index: number;
      support_status: Segment['support_status'];
      sources: Segment['sources'];
    }
  | ({ type: 'done' } & (Answer | Message))
  | { type: 'error'; code: string; message: string };

export type DraftState = {
  segments: Segment[];
  gaps: string[];
  factChecklist: Schema['FactChecklistEntry'][];
  verbatim: Schema['VerbatimOffer'] | null;
  result: Answer | Message | null;
  error: string | null;
};

export function emptyDraft(): DraftState {
  return {
    segments: [],
    gaps: [],
    factChecklist: [],
    verbatim: null,
    result: null,
    error: null,
  };
}

export function applyDraftEvent(state: DraftState, event: DraftEvent): DraftState {
  switch (event.type) {
    case 'verbatim':
      return { ...state, verbatim: event };
    case 'segment':
      return {
        ...state,
        segments: [...state.segments.filter((s) => s.index !== event.index), event].sort((a, b) => a.index - b.index),
      };
    case 'gaps':
      return { ...state, gaps: event.gaps };
    case 'fact_checklist':
      return { ...state, factChecklist: event.fact_checklist };
    case 'support':
      return {
        ...state,
        segments: state.segments.map((s) =>
          s.index === event.index
            ? {
                ...s,
                support_status: event.support_status,
                sources: event.sources,
              }
            : s,
        ),
      };
    case 'done':
      return {
        ...state,
        result: event,
        segments: event.segments ?? [],
        gaps: event.gaps ?? [],
        factChecklist: event.fact_checklist ?? [],
        verbatim: event.verbatim ?? null,
      };
    case 'error':
      return { ...emptyDraft(), error: event.message };
  }
}

/** Split UTF-8 safely even when network chunks split a character or a JSON line. */
export async function consumeDraftStream(body: ReadableStream<Uint8Array>, onEvent: (event: DraftEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '';
  let terminal = false;
  const emit = (line: string) => {
    if (!line.trim()) return;
    if (terminal) throw new Error('The backend sent data after the stream finished.');
    const event = JSON.parse(line) as DraftEvent;
    if (!event || !['verbatim', 'segment', 'gaps', 'fact_checklist', 'support', 'done', 'error'].includes(event.type)) {
      throw new Error('Unrecognised backend stream event.');
    }
    onEvent(event);
    terminal = event.type === 'done' || event.type === 'error';
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let end: number;
      while ((end = pending.indexOf('\n')) >= 0) {
        emit(pending.slice(0, end));
        pending = pending.slice(end + 1);
      }
      if (pending.length > 8_000_000) throw new Error('The backend stream line is too large.');
      if (done) {
        emit(pending);
        break;
      }
    }
    if (!terminal) throw new Error('The stream disconnected before completion.');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/** Backend offsets count Unicode code points; JS selection APIs count UTF-16 units. */
export function codePointRange(text: string, start: number, end: number): { start: number; end: number } | null {
  const points = Array.from(text);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > points.length) return null;
  return {
    start: points.slice(0, start).join('').length,
    end: points.slice(0, end).join('').length,
  };
}
