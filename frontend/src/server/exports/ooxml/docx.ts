import { isText, kids, tagOf, textValue, type XNode } from './ooxml';
export function runText(nodes: XNode[]): string {
  let out = '';
  for (const n of nodes) {
    if (isText(n)) continue;
    const tag = tagOf(n);
    if (tag === 'w:t') out += kids(n).filter(isText).map(textValue).join('');
    else if (tag === 'w:tab') out += '\t';
    else if (tag === 'w:br' || tag === 'w:cr') out += '\n';
    else if (tag === 'w:del' || tag === 'w:delText' || tag === 'w:instrText' || tag === 'w:rPr' || tag === 'w:pPr') continue;
    else out += runText(kids(n));
  }
  return out;
}
