import { describe, expect, it } from 'vitest';
import { stage, taskFor, type Assigned } from '@/lib/tasks';

const none: Assigned = { sme: [], approver: [], reviewer: [] };
const both: Assigned = { sme: ['Alex'], approver: ['Valerie'], reviewer: [] };
const answer = (needs_attention = 0) => ({ support_summary: { needs_attention } });

describe('whose turn a question is', () => {
  it('follows owner, then SME, then approver', () => {
    expect(stage({ status: 'not_started' }, both).waitingOn).toBe('owner');
    expect(stage({ status: 'ai_draft', current_answer: answer() }, both).waitingOn).toBe('sme');
    expect(stage({ status: 'writer_edited', current_answer: answer(2) }, both).waitingOn).toBe('sme');
    expect(stage({ status: 'sme_verified', current_answer: answer() }, both).waitingOn).toBe('approver');
  });

  it('skips stages nobody is assigned to, and returns to the owner when something needs review', () => {
    expect(stage({ status: 'ai_draft', current_answer: answer() }, none)).toEqual({ waitingOn: 'owner', reason: 'Review and edit the AI draft' });
    expect(stage({ status: 'writer_edited', current_answer: answer(1) }, none).reason).toBe('1 sentence needs a source or confirmation');
    expect(stage({ status: 'writer_edited', current_answer: answer() }, { ...none, approver: ['Valerie'] }).waitingOn).toBe('approver');
    expect(stage({ status: 'sme_verified', current_answer: answer() }, none).waitingOn).toBe('owner');
    expect(stage({ status: 'sme_verified', needs_review: true, current_answer: answer() }, both).waitingOn).toBe('owner');
  });

  it('gives each of a person’s roles its own task and names who it is waiting on', () => {
    const q = { status: 'writer_edited', assignee: 'Sam', current_answer: answer() };
    expect(taskFor(q, { owner: true, roles: [] }, both)).toEqual([{ turn: false, role: 'owner', reason: 'Waiting on SME review by Alex' }]);
    expect(taskFor(q, { owner: false, roles: ['sme'] }, both)).toEqual([{ turn: true, role: 'sme', reason: 'Verify the answer as SME reviewer' }]);
    expect(taskFor(q, { owner: false, roles: ['approver'] }, both)[0]).toMatchObject({ turn: false, reason: 'Waiting on SME review by Alex' });
    expect(taskFor({ status: 'not_started', assignee: 'Sam' }, { owner: false, roles: ['reviewer'] }, both)[0]).toMatchObject({ turn: false });
    expect(taskFor(q, { owner: false, roles: ['reviewer'] }, both)[0]).toMatchObject({ turn: true, reason: 'Review the answer and comment' });
  });
});
