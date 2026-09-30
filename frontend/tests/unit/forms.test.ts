import { strFromU8, strToU8, unzipSync, zipSync } from 'fflate';
import { describe, expect, it } from 'vitest';
import { findAll, kids, parseXml } from '@/server/exports/ooxml/ooxml';
import { runText } from '@/server/exports/ooxml/docx';
import { fillDocx, fillXlsx } from '@/server/exports/forms';

// Minimal synthetic Office structures: independent of the retired tender pipeline.
const wordCell = (text: string) => `<w:tc><w:p><w:r><w:t>${text}</w:t></w:r></w:p></w:tc>`;
const wordForm = zipSync({
  'word/document.xml': strToU8(
    `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr>${['ID', 'Question', 'Limit', 'Answer'].map(wordCell).join('')}</w:tr><w:tr>${['Q1', 'Where is data hosted?', '100', ''].map(wordCell).join('')}</w:tr></w:tbl></w:body></w:document>`,
  ),
  'word/styles.xml': strToU8('<styles/>'),
});
const excelForm = zipSync({
  'xl/workbook.xml': strToU8(
    '<workbook><sheets><sheet name="DPIA" sheetId="1" r:id="r1"/><sheet name="Other" sheetId="2" r:id="r2"/></sheets></workbook>',
  ),
  'xl/_rels/workbook.xml.rels': strToU8(
    '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>',
  ),
  'xl/worksheets/sheet1.xml': strToU8(
    '<worksheet><sheetData><row r="2"><c r="B2" t="inlineStr"><is><t>Retention?</t></is></c></row><row r="3"><c r="B3" t="inlineStr"><is><t>Existing question</t></is></c></row></sheetData></worksheet>',
  ),
  'xl/worksheets/sheet2.xml': strToU8('<worksheet><sheetData/></worksheet>'),
});
const cellText = (bytes: Uint8Array, index: number) =>
  runText(kids(findAll(parseXml(strFromU8(unzipSync(bytes)['word/document.xml'])), 'w:tc')[index]));

function changedParts(before: Uint8Array, after: Uint8Array): string[] {
  const a = unzipSync(before);
  const b = unzipSync(after);
  const names = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...names].filter((n) => !a[n] || !b[n] || Buffer.compare(Buffer.from(a[n]), Buffer.from(b[n])) !== 0).sort();
}

describe('Word form filler', () => {
  const form = wordForm;

  it('fills only the confirmed empty cell and keeps every other part', async () => {
    const answer = 'Customer data is hosted in the United Kingdom.\n\nDisaster recovery is in Manchester <UK> & London.';
    const r = fillDocx(form, [{ target: { kind: 'docx_table_cell', table: 1, row: 2, cell: 4 }, text: answer, label: 'Q1' }]);
    expect(r.applied).toEqual(['Q1']);
    expect(r.skipped).toEqual([]);
    expect(changedParts(form, r.bytes)).toEqual(['word/document.xml']);

    expect(cellText(r.bytes, 7)).toContain('Customer data is hosted in the United Kingdom.');
    expect(cellText(r.bytes, 7)).toContain('Manchester <UK> & London.');
    expect(cellText(r.bytes, 5)).toBe(cellText(form, 5));
  });

  it('never overwrites existing text and reports missing cells', () => {
    const r = fillDocx(form, [
      { target: { kind: 'docx_table_cell', table: 1, row: 2, cell: 2 }, text: 'overwrite attempt', label: 'question cell' },
      { target: { kind: 'docx_table_cell', table: 1, row: 40, cell: 4 }, text: 'x', label: 'missing row' },
    ]);
    expect(r.applied).toEqual([]);
    expect(r.skipped.map((s) => s.label)).toEqual(['question cell', 'missing row']);
    expect(r.skipped[0].reason).toMatch(/already contains text/);
  });
});

describe('Excel form filler', () => {
  const form = excelForm;

  it('writes an inline string into an empty cell and leaves other sheets alone', async () => {
    const r = fillXlsx(form, [
      { target: { kind: 'xlsx_cell', sheet: 'DPIA', ref: 'D2' }, text: 'Recordings are deleted after 24 hours.', label: 'D1' },
      { target: { kind: 'xlsx_cell', sheet: 'DPIA', ref: 'b3' }, text: 'overwrite', label: 'question cell' },
      { target: { kind: 'xlsx_cell', sheet: 'Missing', ref: 'D2' }, text: 'x', label: 'missing sheet' },
    ]);
    expect(r.applied).toEqual(['D1']);
    expect(r.skipped.map((s) => s.label)).toEqual(['question cell', 'missing sheet']);
    expect(changedParts(form, r.bytes)).toEqual(['xl/worksheets/sheet1.xml']);
    expect(strFromU8(unzipSync(r.bytes)['xl/worksheets/sheet1.xml'])).toContain('Recordings are deleted after 24 hours.');
  });

  it('refuses to replace a formula', () => {
    const parts = unzipSync(form);
    const sheet = strFromU8(parts['xl/worksheets/sheet1.xml']).replace('<row r="3">', '<row r="3"><c r="D3"><f>SUM(1,1)</f><v>2</v></c>');
    // Keep cells in column order: move the formula cell after C3.
    const reordered = sheet.replace(/(<row r="3">)(<c r="D3">.*?<\/c>)(.*?)(<\/row>)/, '$1$3$2$4');
    const withFormula = zipSync({ ...parts, 'xl/worksheets/sheet1.xml': strToU8(reordered) });
    const r = fillXlsx(withFormula, [{ target: { kind: 'xlsx_cell', sheet: 'DPIA', ref: 'D3' }, text: 'answer', label: 'D2' }]);
    expect(r.applied).toEqual([]);
    expect(r.skipped[0].reason).toMatch(/formula/);
  });
});

it('rejects decompression bombs and XML doctypes before filling', () => {
  expect(() =>
    fillDocx(wordForm, [], { maxDecompressedBytes: 10, maxZipEntries: 5000, maxPassages: 1, maxPassageChars: 1, timeoutMs: 100 }),
  ).toThrow(/expands/);
  expect(() => parseXml('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')).toThrow();
});
