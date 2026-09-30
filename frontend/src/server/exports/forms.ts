// Fills a buyer's original Word or Excel form at confirmed locations. Only the
// mapped cells change; every other part of the file is carried over unchanged.
// Existing text or formulas in a target are never overwritten.
import { unzipSync, zipSync, type Zippable } from 'fflate';
import { XMLBuilder } from 'fast-xml-parser';
import { attr, child, decodeUtf8, findFirst, kids, parseXml, readZipParts, tagOf, type XNode } from './ooxml/ooxml';
import { runText } from './ooxml/docx';
import { DEFAULT_LIMITS, type ExtractionLimits } from './ooxml/types';
import { columnIndex, readWorkbookSheets, splitRef } from './ooxml/xlsx';

const builder = new XMLBuilder({
  preserveOrder: true,
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  suppressEmptyNode: true,
  processEntities: true,
  format: false,
});

export type DocxTarget = { kind: 'docx_table_cell'; table: number; row: number; cell: number };
export type XlsxTarget = { kind: 'xlsx_cell'; sheet: string; ref: string };
export type Fill<T> = { target: T; text: string; label: string };
export type FillResult = { bytes: Uint8Array; applied: string[]; skipped: Array<{ label: string; reason: string }> };

// Buyer placeholders that may be replaced, e.g. "[Supplier to complete]".
const PLACEHOLDER = /^\s*(\[[^\]]{0,60}\]|\(?(supplier|bidder|tenderer) to (complete|respond)\)?|n\/?a|-+|\.{3,}|_+)?\s*$/i;

function readAll(buf: Uint8Array, limits: ExtractionLimits): Map<string, Uint8Array> {
  return readZipParts(buf, () => true, limits);
}

function rezip(parts: Map<string, Uint8Array>): Uint8Array {
  const out: Zippable = {};
  for (const [name, data] of parts) out[name] = [data, { level: name.endsWith('.xml') || name.endsWith('.rels') ? 6 : 0 }];
  return zipSync(out);
}

function textRun(text: string, rPr?: XNode): XNode {
  const lines = text.split('\n');
  const content: XNode[] = [];
  if (rPr) content.push(structuredClone(rPr));
  lines.forEach((line, i) => {
    if (i > 0) content.push({ 'w:br': [] });
    content.push({ 'w:t': [{ '#text': line }], ':@': { '@_xml:space': 'preserve' } });
  });
  return { 'w:r': content };
}

function tablesInBody(body: XNode): XNode[] {
  // Same traversal as the extractor, so table numbers match what users confirmed.
  const tables: XNode[] = [];
  const walk = (nodes: XNode[]) => {
    for (const n of nodes) {
      const tag = tagOf(n);
      if (tag === 'w:tbl') tables.push(n);
      else if (tag === 'w:sdt') {
        const content = child(n, 'w:sdtContent');
        if (content) walk(kids(content));
      } else if (tag === 'w:customXml' || tag === 'w:smartTag') walk(kids(n));
    }
  };
  walk(kids(body));
  return tables;
}

export function fillDocx(buf: Uint8Array, fills: Array<Fill<DocxTarget>>, limits: ExtractionLimits = DEFAULT_LIMITS): FillResult {
  const parts = readAll(buf, limits);
  const docXml = parts.get('word/document.xml');
  if (!docXml) throw new Error('The Word form has no main document part.');
  const root = parseXml(decodeUtf8(docXml));
  const body = findFirst(root, 'w:body');
  if (!body) throw new Error('The Word form has no body.');
  const tables = tablesInBody(body);
  const applied: string[] = [];
  const skipped: FillResult['skipped'] = [];

  for (const f of fills) {
    const tbl = tables[f.target.table - 1];
    const rows = tbl ? kids(tbl).filter((n) => tagOf(n) === 'w:tr') : [];
    const tr = rows[f.target.row - 1];
    const cells = tr
      ? kids(tr).flatMap((n) => (tagOf(n) === 'w:tc' ? [n] : tagOf(n) === 'w:sdt' ? (findFirst(kids(n), 'w:tc') ? [findFirst(kids(n), 'w:tc')!] : []) : []))
      : [];
    const tc = cells[f.target.cell - 1];
    if (!tc) {
      skipped.push({ label: f.label, reason: 'The mapped table cell does not exist in this form.' });
      continue;
    }
    const children = kids(tc);
    const existing = runText(children.filter((k) => tagOf(k) !== 'w:tcPr'));
    if (!PLACEHOLDER.test(existing)) {
      skipped.push({ label: f.label, reason: `The target cell already contains text (“${existing.slice(0, 60)}”), so it was left unchanged.` });
      continue;
    }
    const firstP = children.find((k) => tagOf(k) === 'w:p');
    const pPr = firstP ? child(firstP, 'w:pPr') : undefined;
    const firstRun = firstP ? kids(firstP).find((k) => tagOf(k) === 'w:r') : undefined;
    const rPr = firstRun ? child(firstRun, 'w:rPr') : undefined;
    const tcPr = children.filter((k) => tagOf(k) === 'w:tcPr');
    const paragraphs = f.text
      .replace(/\r\n?/g, '\n')
      .split(/\n{2,}/)
      .map((para) => ({ 'w:p': [...(pPr ? [structuredClone(pPr)] : []), textRun(para, rPr)] }) as XNode);
    // A cell must end with a paragraph; an empty answer keeps one empty paragraph.
    const content = paragraphs.length ? paragraphs : [{ 'w:p': pPr ? [structuredClone(pPr)] : [] } as XNode];
    (tc as Record<string, unknown>)['w:tc'] = [...tcPr, ...content];
    applied.push(f.label);
  }

  parts.set('word/document.xml', new TextEncoder().encode(builder.build(root)));
  return { bytes: rezip(parts), applied, skipped };
}

function cellValue(c: XNode, shared: string[]): { text: string; formula: boolean } {
  const type = attr(c, 't');
  const formula = Boolean(child(c, 'f'));
  const v = child(c, 'v');
  const vText = v ? kids(v).map((k) => String(k['#text'] ?? '')).join('') : '';
  if (type === 's') return { text: shared[Number(vText)] ?? '', formula };
  if (type === 'inlineStr') {
    const is = child(c, 'is');
    return { text: is ? runTextXlsx(is) : '', formula };
  }
  return { text: vText, formula };
}

function runTextXlsx(n: XNode): string {
  let out = '';
  for (const k of kids(n)) {
    if ('#text' in k) out += String(k['#text']);
    else if (tagOf(k) !== 'rPh') out += runTextXlsx(k);
  }
  return out;
}

export function fillXlsx(buf: Uint8Array, fills: Array<Fill<XlsxTarget>>, limits: ExtractionLimits = DEFAULT_LIMITS): FillResult {
  const parts = readAll(buf, limits);
  const sheets = readWorkbookSheets(parts);
  const shared: string[] = [];
  const sst = parts.get('xl/sharedStrings.xml');
  if (sst) {
    const sstRoot = parseXml(decodeUtf8(sst));
    const node = findFirst(sstRoot, 'sst');
    for (const si of node ? kids(node) : []) if (tagOf(si) === 'si') shared.push(runTextXlsx(si));
  }
  const applied: string[] = [];
  const skipped: FillResult['skipped'] = [];
  const parsed = new Map<string, XNode[]>();

  for (const f of fills) {
    const sheet = sheets.find((s) => s.name === f.target.sheet);
    if (!sheet || !parts.has(sheet.path)) {
      skipped.push({ label: f.label, reason: `Sheet “${f.target.sheet}” was not found in this workbook.` });
      continue;
    }
    if (!parsed.has(sheet.path)) parsed.set(sheet.path, parseXml(decodeUtf8(parts.get(sheet.path)!)));
    const root = parsed.get(sheet.path)!;
    const sheetData = findFirst(root, 'sheetData');
    if (!sheetData) {
      skipped.push({ label: f.label, reason: 'The sheet has no cell data section.' });
      continue;
    }
    let pos: { col: number; row: number };
    try {
      pos = splitRef(f.target.ref);
    } catch {
      skipped.push({ label: f.label, reason: `“${f.target.ref}” is not a valid cell reference.` });
      continue;
    }
    const rows = kids(sheetData);
    let row = rows.find((r) => tagOf(r) === 'row' && Number(attr(r, 'r')) === pos.row);
    if (!row) {
      row = { row: [], ':@': { '@_r': String(pos.row) } };
      const insertAt = rows.findIndex((r) => tagOf(r) === 'row' && Number(attr(r, 'r')) > pos.row);
      if (insertAt < 0) rows.push(row);
      else rows.splice(insertAt, 0, row);
    }
    const cells = kids(row);
    const ref = f.target.ref.toUpperCase();
    let cell = cells.find((c) => tagOf(c) === 'c' && (attr(c, 'r') ?? '').toUpperCase() === ref);
    if (cell) {
      const current = cellValue(cell, shared);
      if (current.formula) {
        skipped.push({ label: f.label, reason: `Cell ${ref} contains a formula, so it was left unchanged.` });
        continue;
      }
      if (!PLACEHOLDER.test(current.text)) {
        skipped.push({ label: f.label, reason: `Cell ${ref} already contains “${current.text.slice(0, 60)}”, so it was left unchanged.` });
        continue;
      }
    } else {
      cell = { c: [], ':@': { '@_r': ref } };
      const insertAt = cells.findIndex((c) => tagOf(c) === 'c' && columnIndex(attr(c, 'r') ?? 'A') > pos.col);
      if (insertAt < 0) cells.push(cell);
      else cells.splice(insertAt, 0, cell);
    }
    const text = f.text.length > 32767 ? f.text.slice(0, 32767) : f.text;
    if (text.length < f.text.length) skipped.push({ label: f.label, reason: 'The answer was longer than an Excel cell allows and was cut to 32,767 characters.' });
    const attrs = { ...((cell[':@'] as Record<string, string>) ?? {}), '@_r': ref, '@_t': 'inlineStr' };
    cell[':@'] = attrs;
    (cell as Record<string, unknown>).c = [{ is: [{ t: [{ '#text': text }], ':@': { '@_xml:space': 'preserve' } }] }];
    applied.push(f.label);
  }

  for (const [path, root] of parsed) parts.set(path, new TextEncoder().encode(builder.build(root)));
  return { bytes: rezip(parts), applied, skipped };
}

/** Reads every entry of an Office file, for tests and previews. */
export function unzipAll(buf: Uint8Array): Record<string, Uint8Array> {
  return unzipSync(buf);
}
