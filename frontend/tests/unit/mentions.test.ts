import { describe, expect, it } from 'vitest';
import { activeMention, mentionedNames, splitMentions } from '@/lib/mentions';

const names = ['Sam', 'Sam Jones', 'Puru', 'Reviewer C'];

describe('mentions', () => {
  it('finds whole-name mentions, preferring the longest name, and ignores lookalikes', () => {
    expect(mentionedNames('Thanks @sam jones and @Puru.', names)).toEqual(['Sam Jones', 'Puru']);
    expect(mentionedNames('@Samuel and email@Puru.test and @Pur', names)).toEqual([]);
    expect(mentionedNames('Please check, @Reviewer C', names)).toEqual(['Reviewer C']);
  });

  it('splits text into tags without losing any characters', () => {
    const parts = splitMentions('Hi @Puru, see (@Sam).', names);
    expect(parts.map((p) => p.text).join('')).toBe('Hi @Puru, see (@Sam).');
    expect(parts.filter((p) => p.mention).map((p) => p.text)).toEqual(['@Puru', '@Sam']);
  });

  it('detects the mention being typed at the caret', () => {
    expect(activeMention('Hello @Pu', 9)).toEqual({ start: 6, query: 'Pu' });
    expect(activeMention('Hello @', 7)).toEqual({ start: 6, query: '' });
    expect(activeMention('mail@example', 12)).toBeNull();
    expect(activeMention('@Puru\nnext', 10)).toBeNull();
  });
});
