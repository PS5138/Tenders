import { unzipSync } from 'fflate';
import { XMLParser } from 'fast-xml-parser';
import { ExtractionError, type ExtractionLimits } from './types';

/** A node in fast-xml-parser's preserveOrder output: { tag: children[], ':@': attributes } or { '#text': value }. */
export type XNode = Record<string, unknown>;

const parser = new XMLParser({
  preserveOrder: true,
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  trimValues: false,
  parseTagValue: false,
  parseAttributeValue: false,
  processEntities: true,
  htmlEntities: false,
  // External entities are never resolved; DOCTYPEs in OOXML parts are not expected.
});

export function parseXml(xml: string): XNode[] {
  if (/<!DOCTYPE/i.test(xml.slice(0, 2000))) {
    throw new ExtractionError('UNSAFE_XML', 'The document contains an XML DOCTYPE declaration, which is not allowed.');
  }
  return parser.parse(xml) as XNode[];
}

export function tagOf(n: XNode): string | undefined {
  for (const k of Object.keys(n)) if (k !== ':@') return k;
  return undefined;
}

export function kids(n: XNode): XNode[] {
  const t = tagOf(n);
  const v = t ? n[t] : undefined;
  return Array.isArray(v) ? (v as XNode[]) : [];
}

export function attr(n: XNode, name: string): string | undefined {
  const a = n[':@'] as Record<string, string> | undefined;
  return a?.['@_' + name];
}

export function isText(n: XNode): boolean {
  return '#text' in n;
}

export function textValue(n: XNode): string {
  return String(n['#text'] ?? '');
}

export function child(n: XNode, tag: string): XNode | undefined {
  return kids(n).find((c) => tagOf(c) === tag);
}

export function childrenNamed(n: XNode, tag: string): XNode[] {
  return kids(n).filter((c) => tagOf(c) === tag);
}

/** Depth-first search for descendants with a tag, not descending into matches. */
export function findAll(nodes: XNode[], tag: string, out: XNode[] = []): XNode[] {
  for (const n of nodes) {
    if (tagOf(n) === tag) out.push(n);
    else if (!isText(n)) findAll(kids(n), tag, out);
  }
  return out;
}

export function findFirst(nodes: XNode[], tag: string): XNode | undefined {
  for (const n of nodes) {
    if (tagOf(n) === tag) return n;
    if (!isText(n)) {
      const f = findFirst(kids(n), tag);
      if (f) return f;
    }
  }
  return undefined;
}

export type ZipEntryInfo = { name: string; originalSize: number; size: number };

/** Lists entries from the central directory without inflating anything. */
export function listZip(buf: Uint8Array): ZipEntryInfo[] {
  const entries: ZipEntryInfo[] = [];
  unzipSync(buf, {
    filter: (f) => {
      entries.push({ name: f.name, originalSize: f.originalSize, size: f.size });
      return false;
    },
  });
  return entries;
}

/**
 * Inflates only the named parts, enforcing entry-count and decompressed-size
 * limits to guard against archive bombs.
 */
export function readZipParts(buf: Uint8Array, wanted: (name: string) => boolean, limits: ExtractionLimits): Map<string, Uint8Array> {
  let entries: ZipEntryInfo[];
  try {
    entries = listZip(buf);
  } catch {
    throw new ExtractionError('CORRUPT_ARCHIVE', 'The file could not be opened. It may be damaged or password-protected.');
  }
  if (entries.length > limits.maxZipEntries) {
    throw new ExtractionError('ARCHIVE_TOO_LARGE', `The file contains ${entries.length} parts, above the limit of ${limits.maxZipEntries}.`);
  }
  const total = entries.reduce((s, e) => s + e.originalSize, 0);
  if (total > limits.maxDecompressedBytes) {
    throw new ExtractionError('ARCHIVE_TOO_LARGE', 'The file expands to more than the allowed size when opened.');
  }
  let files: Record<string, Uint8Array>;
  try {
    files = unzipSync(buf, { filter: (f) => wanted(f.name) });
  } catch {
    throw new ExtractionError('CORRUPT_ARCHIVE', 'The file could not be opened. It may be damaged or password-protected.');
  }
  let actual = 0;
  const out = new Map<string, Uint8Array>();
  for (const [name, data] of Object.entries(files)) {
    actual += data.byteLength;
    if (actual > limits.maxDecompressedBytes) throw new ExtractionError('ARCHIVE_TOO_LARGE', 'The file expands to more than the allowed size when opened.');
    out.set(name, data);
  }
  return out;
}

export function decodeUtf8(data: Uint8Array): string {
  return new TextDecoder('utf-8').decode(data);
}
