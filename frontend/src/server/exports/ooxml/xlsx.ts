import { attr, decodeUtf8, findAll, parseXml } from './ooxml';
import { ExtractionError } from './types';
export function columnIndex(ref: string): number {
  const letters = /^([A-Z]+)/.exec(ref.toUpperCase())?.[1] ?? 'A';
  return letters.split('').reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0);
}

export function columnLetters(index: number): string {
  let s = '';
  let n = index;
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

export function splitRef(ref: string): { col: number; row: number } {
  const m = /^([A-Z]+)(\d+)$/i.exec(ref);
  if (!m) throw new Error(`Invalid cell reference ${ref}`);
  return { col: columnIndex(m[1]), row: Number(m[2]) };
}

function resolveTarget(target: string): string {
  const t = target.replace(/^\/+/, '');
  return t.startsWith('xl/') ? t : `xl/${t}`;
}

export type SheetRef = { name: string; path: string; hidden: boolean };

export function readWorkbookSheets(parts: Map<string, Uint8Array>): SheetRef[] {
  const wb = parts.get('xl/workbook.xml');
  const rels = parts.get('xl/_rels/workbook.xml.rels');
  if (!wb || !rels) throw new ExtractionError('CORRUPT_XLSX', 'The workbook is missing its sheet list.');
  const relMap = new Map<string, string>();
  for (const r of findAll(parseXml(decodeUtf8(rels)), 'Relationship')) {
    const id = attr(r, 'Id');
    const target = attr(r, 'Target');
    if (id && target && attr(r, 'TargetMode') !== 'External') relMap.set(id, resolveTarget(target));
  }
  return findAll(parseXml(decodeUtf8(wb)), 'sheet').flatMap((s) => {
    const name = attr(s, 'name') ?? 'Sheet';
    const rid = attr(s, 'r:id');
    const path = rid ? relMap.get(rid) : undefined;
    return path ? [{ name, path, hidden: (attr(s, 'state') ?? 'visible') !== 'visible' }] : [];
  });
}

