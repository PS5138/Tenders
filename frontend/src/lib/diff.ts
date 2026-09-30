export type DiffPart = { kind: 'same' | 'added' | 'removed'; text: string };

/** Word-level diff (longest common subsequence) for comparing answer versions. */
export function diffWords(a: string, b: string): DiffPart[] {
  const x = a.split(/(\s+)/).filter((t) => t !== '');
  const y = b.split(/(\s+)/).filter((t) => t !== '');
  if (x.length * y.length > 4_000_000) {
    return [
      { kind: 'removed', text: a },
      { kind: 'added', text: b },
    ];
  }
  const dp: Uint32Array[] = Array.from({ length: x.length + 1 }, () => new Uint32Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffPart[] = [];
  const push = (kind: DiffPart['kind'], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) {
      push('same', x[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) push('removed', x[i++]);
    else push('added', y[j++]);
  }
  while (i < x.length) push('removed', x[i++]);
  while (j < y.length) push('added', y[j++]);
  return out;
}
