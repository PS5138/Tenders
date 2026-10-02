import { describe, expect, it } from 'vitest';
import {
  attentionIndices,
  isEditableTarget,
  locationText,
  nextAttentionIndex,
  nextShortcutPressed,
  paneSource,
  scrollOffsetFor,
} from '@/features/backend/trace';
import { adoptLandedVersion, reconcileLandedVersion } from '@/features/backend/question';

// The trace interaction is the moment the demo exists to show. These tests pin the pure parts behind it:
// which sentences a reviewer is moved through and in what order, when the single-key shortcut may fire,
// where the source pane scrolls, and what a marker click opens.

const seg = (index: number, support_status: string, dispute: object | null = null) => ({ index, support_status, dispute } as never);

describe('moving through the sentences needing attention', () => {
  const segments = [
    seg(0, 'supported'),
    seg(1, 'weak'),
    seg(2, 'connective'),
    seg(3, 'unsupported'),
    seg(4, 'human_authored'),
    seg(5, 'supported', { disputed_by: 'Sam', note: 'Check this.', at: 'now' }),
    seg(6, 'pending'),
  ];

  it('lists weak, unsupported, human-authored and disputed sentences in reading order', () => {
    expect(attentionIndices(segments)).toEqual([1, 3, 4, 5]);
    expect(attentionIndices([seg(2, 'weak'), seg(0, 'unsupported')])).toEqual([0, 2]);
    expect(attentionIndices([seg(0, 'supported'), seg(1, 'connective')])).toEqual([]);
  });

  it('starts at the first, advances from the last visited and wraps around', () => {
    const indices = attentionIndices(segments);
    expect(nextAttentionIndex(indices, null)).toBe(1);
    expect(nextAttentionIndex(indices, 1)).toBe(3);
    expect(nextAttentionIndex(indices, 4)).toBe(5);
    expect(nextAttentionIndex(indices, 5)).toBe(1);
    // A last-visited index that no longer needs attention still advances to the next one after it.
    expect(nextAttentionIndex(indices, 2)).toBe(3);
    expect(nextAttentionIndex(indices, 99)).toBe(1);
  });

  it('has nowhere to go when every sentence is supported', () => {
    expect(nextAttentionIndex([], null)).toBeNull();
    expect(nextAttentionIndex([], 3)).toBeNull();
  });
});

describe('the n shortcut', () => {
  const element = (tagName: string, extra: Record<string, unknown> = {}) => ({ tagName, closest: () => null, ...extra });
  const press = (target: unknown, key = 'n', mods: Partial<{ altKey: boolean; ctrlKey: boolean; metaKey: boolean }> = {}) =>
    nextShortcutPressed({ key, altKey: false, ctrlKey: false, metaKey: false, target, ...mods });

  it('stays out of inputs, textareas, selects, contenteditable regions and open dialogs', () => {
    expect(isEditableTarget(element('INPUT'))).toBe(true);
    expect(isEditableTarget(element('textarea'))).toBe(true);
    expect(isEditableTarget(element('SELECT'))).toBe(true);
    expect(isEditableTarget(element('DIV', { isContentEditable: true }))).toBe(true);
    expect(isEditableTarget(element('SPAN', { closest: (selector: string) => (selector.includes('[role="dialog"]') ? {} : null) }))).toBe(true);
    expect(isEditableTarget(element('BUTTON'))).toBe(false);
    expect(isEditableTarget(element('BODY'))).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
    expect(isEditableTarget(undefined)).toBe(false);
  });

  it('fires only for a plain n on a non-editable target', () => {
    expect(press(element('BUTTON'))).toBe(true);
    expect(press(element('BODY'))).toBe(true);
    expect(press(element('TEXTAREA'))).toBe(false);
    expect(press(element('BUTTON'), 'N')).toBe(false);
    expect(press(element('BUTTON'), 'm')).toBe(false);
    expect(press(element('BUTTON'), 'n', { metaKey: true })).toBe(false);
    expect(press(element('BUTTON'), 'n', { ctrlKey: true })).toBe(false);
    expect(press(element('BUTTON'), 'n', { altKey: true })).toBe(false);
    expect(nextShortcutPressed({ key: 'n', altKey: false, ctrlKey: false, metaKey: false, defaultPrevented: true, target: element('BUTTON') })).toBe(false);
  });
});

describe('the source pane', () => {
  it('scrolls so the highlighted span sits about a third of the way down, never past the top', () => {
    expect(scrollOffsetFor(900, 600)).toBe(700);
    expect(scrollOffsetFor(100, 600)).toBe(0);
    expect(scrollOffsetFor(0, 600)).toBe(0);
    expect(scrollOffsetFor(250, 300)).toBe(150);
  });

  it('opens the first document or fact source and never an attestation', () => {
    const item = { source_type: 'knowledge_item', source_id: 'a', quote: 'q', locator: { document_id: 'd', section_id: 's' }, document_title: 'Doc' };
    const fact = { ...item, source_type: 'fact', source_id: 'f' };
    const attestation = { source_type: 'human_attestation', attested_by: 'Jane', at: 'now' };
    expect(paneSource({ sources: [attestation, fact, item] } as never)).toEqual(fact);
    expect(paneSource({ sources: [item, fact] } as never)).toEqual(item);
    expect(paneSource({ sources: [attestation] } as never)).toBeNull();
    expect(paneSource({ sources: [] })).toBeNull();
    expect(paneSource({})).toBeNull();
  });

  it('describes the location from the locator alone', () => {
    expect(locationText({ document_id: 'd', section_id: 's', page: 12, table: 1, cell_ref: 'Sheet1!5' })).toBe('Page 12 · Table 2 · Row Sheet1!5');
    expect(locationText({ document_id: 'd', section_id: 's', heading_path: ['4', '4.2'] })).toBe('4 / 4.2');
    expect(locationText({ document_id: 'd', section_id: 's' })).toBe('');
  });
});

describe('the editor when a new version lands', () => {
  const seededFrom = { seededId: 'v1', seededText: 'First draft.' };

  it('adopts the version silently while nothing has been typed', () => {
    expect(adoptLandedVersion({ ...seededFrom, text: 'First draft.' }, { id: 'v2', text: 'Second draft.' })).toBe('adopt');
  });

  it('keeps typed text and reports a conflict before any save is attempted', () => {
    expect(adoptLandedVersion({ ...seededFrom, text: 'First draft, edited.' }, { id: 'v2', text: 'Second draft.' })).toBe('conflict');
  });

  it('ignores the version it was seeded from and the absence of a version', () => {
    expect(adoptLandedVersion({ ...seededFrom, text: 'typed' }, { id: 'v1', text: 'First draft.' })).toBe('ignore');
    expect(adoptLandedVersion({ ...seededFrom, text: 'typed' }, null)).toBe('ignore');
    // A first answer arriving in an empty editor is adopted; typed text in that editor is kept.
    expect(adoptLandedVersion({ seededId: null, seededText: '', text: '' }, { id: 'v1', text: 'Draft.' })).toBe('adopt');
    expect(adoptLandedVersion({ seededId: null, seededText: '', text: 'My own words.' }, { id: 'v1', text: 'Draft.' })).toBe('conflict');
  });

  describe('reconciling the editor with the question prop', () => {
    // Right after a successful save: the editor seeded itself from v2 while the prop still carries v1.
    const afterSave = { seenId: 'v1', seeded: { id: 'v2', text: 'Saved draft.' }, text: 'Saved draft.' };

    it('does nothing while the prop has not changed, so a save never snaps back to the old version', () => {
      expect(reconcileLandedVersion(afterSave, { id: 'v1', text: 'First draft.' })).toBeNull();
      expect(reconcileLandedVersion({ seenId: null, seeded: { id: null, text: '' }, text: 'typed' }, null)).toBeNull();
    });

    it('only records the reload that brings back the version it already seeded itself from', () => {
      expect(reconcileLandedVersion(afterSave, { id: 'v2', text: 'Saved draft.' })).toEqual({ seenId: 'v2', seeded: null, outcome: 'ignore' });
    });

    it('adopts a foreign version while nothing has been typed since the save', () => {
      expect(reconcileLandedVersion(afterSave, { id: 'v3', text: 'Colleague draft.' })).toEqual({
        seenId: 'v3',
        seeded: { id: 'v3', text: 'Colleague draft.' },
        outcome: 'adopt',
      });
    });

    it('flags a foreign version as a conflict once the person has typed', () => {
      expect(reconcileLandedVersion({ ...afterSave, text: 'Saved draft, edited.' }, { id: 'v3', text: 'Colleague draft.' })).toEqual({
        seenId: 'v3',
        seeded: { id: 'v3', text: 'Colleague draft.' },
        outcome: 'conflict',
      });
    });

    it('adopts the first version arriving in an empty editor and records a version disappearing', () => {
      expect(reconcileLandedVersion({ seenId: null, seeded: { id: null, text: '' }, text: '' }, { id: 'v1', text: 'Draft.' })).toEqual({
        seenId: 'v1',
        seeded: { id: 'v1', text: 'Draft.' },
        outcome: 'adopt',
      });
      expect(reconcileLandedVersion({ seenId: 'v1', seeded: { id: 'v1', text: 'Draft.' }, text: 'Draft.' }, null)).toEqual({ seenId: null, seeded: null, outcome: 'ignore' });
    });
  });
});
