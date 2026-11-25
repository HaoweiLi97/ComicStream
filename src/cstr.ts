import { validateWebP } from "./webp.js";

const HEADER = 32;
const PAGE = 16;
const CHUNK = 20;
const THUMBNAIL = 16;
const FLAG_THUMBNAILS = 1;
const FLAG_COVER = 2;
const COVER = 16;
export const CODEC = { webp: 1, avif: 2, png: 3, jpeg: 4 } as const;
const U64_MAX = (1n << 64n) - 1n;
export const LIMITS = {
  index: 8 * 1024 * 1024,
  metadata: 256 * 1024,
  chunk: 32 * 1024 * 1024,
  whole: 64 * 1024 * 1024,
  decoded: 128 * 1024 * 1024,
  prefix: 64 * 1024,
  thumbnail: 256 * 1024,
  thumbnailDimension: 512,
};

export class FormatError extends Error {}
export class RangeErrorResponse extends Error {}
export class RangeSessionError extends RangeErrorResponse {}
export class FileReplaced extends RangeSessionError {
  constructor(public readonly source: ByteSource) {
    super("文件版本已更新");
  }
}

export interface ByteSource {
  readonly size: bigint;
  readonly cachedPrefix?: bigint;
  read(start: bigint, end: bigint, signal?: AbortSignal): Promise<Uint8Array<ArrayBuffer>>;
}

function checkRange(size: bigint, start: bigint, end: bigint) {
  if (start < 0n || end < start || end > size || end - start > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new FormatError("字节区间超出文件范围");
  }
}

export function fileSource(blob: Blob): ByteSource {
  if (!Number.isSafeInteger(blob.size)) throw new FormatError("文件超出本地读取范围");
  const size = BigInt(blob.size);
  return {
    size,
    cachedPrefix: size,
    async read(start, end, signal) {
      checkRange(size, start, end);
      signal?.throwIfAborted();
      const bytes = await blob.slice(Number(start), Number(end)).arrayBuffer();
      signal?.throwIfAborted();
      return new Uint8Array(bytes);
    },
  };
}

async function readBody(response: Response, limit: number, exact?: number) {
  const stream = response.body!.getReader();
  try {
    const declared = response.headers.get("Content-Length");
    if (declared !== null && (!/^\d+$/.test(declared) || BigInt(declared) > BigInt(limit)
        || (exact !== undefined && Number(declared) !== exact))) {
      throw new RangeErrorResponse("HTTP 正文长度超出预期");
    }
    const result = exact === undefined ? undefined : new Uint8Array(exact);
    const pieces: Uint8Array<ArrayBuffer>[] = [];
    let received = 0;
    for (;;) {
      const { done, value } = await stream.read();
      if (done) break;
      if (received + value.length > limit) throw new RangeErrorResponse("HTTP 正文超过读取上限");
      if (result) result.set(value, received);
      else pieces.push(value);
      received += value.length;
    }
    if (exact !== undefined && received !== exact) throw new RangeErrorResponse("HTTP 正文长度不匹配");
    return result ?? new Blob(pieces);
  } finally {
    await stream.cancel().catch(() => {});
    stream.releaseLock();
  }
}

type HttpPart = { bytes: Uint8Array<ArrayBuffer>; total: bigint; etag: string | null; url: string };
const isStrongETag = (tag: string | null): tag is string => tag !== null && /^"[^"]*"$/.test(tag);

export async function openHttp(
  url: URL, options: { mutable?: boolean; signal?: AbortSignal; fetch?: typeof fetch } = {},
): Promise<ByteSource> {
  if (!["http:", "https:"].includes(url.protocol)) throw new RangeErrorResponse("请输入 HTTP 或 HTTPS 地址");
  const fetchRange = options.fetch ?? fetch;
  async function request(start: bigint, end: bigint, signal?: AbortSignal, previous?: HttpPart):
    Promise<HttpPart | ByteSource> {
    const headers = new Headers({ Range: "bytes=" + start + "-" + (end - 1n) });
    if (options.mutable && previous?.etag) headers.set("If-Range", previous.etag);
    let response: Response;
    try {
      response = await fetchRange(url, { headers, signal, cache: options.mutable ? "no-store" : "default" });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new RangeErrorResponse("无法连接到漫画包，请检查链接和跨域配置");
    }
    try {
      if (response.status === 416) throw new RangeSessionError("文件范围已失效，请重新打开");
      if (response.status !== 200 && response.status !== 206) {
        throw new RangeErrorResponse("服务器返回 HTTP " + response.status);
      }
      if (!response.body) throw new RangeErrorResponse("HTTP " + response.status + " 未返回文件正文");
      const encoding = response.headers.get("Content-Encoding");
      if (encoding && encoding.toLowerCase() !== "identity") {
        throw new RangeErrorResponse("漫画包需要以原始字节传输");
      }
      if (response.status === 200) {
        return fileSource(await readBody(response, LIMITS.whole) as Blob);
      }
      if (previous && response.url !== previous.url) {
        throw new RangeSessionError("文件链接指向了其他资源，请重新打开");
      }
      const match = /^bytes (\d{1,20})-(\d{1,20})\/(\d{1,20})$/i.exec(
        response.headers.get("Content-Range") ?? "",
      );
      if (!match) throw new RangeErrorResponse("缺少有效的 Content-Range，请检查服务器和跨域配置");
      const [from, through, total] = match.slice(1).map(BigInt);
      const expectedEnd = end < total ? end : total;
      if (previous && total !== previous.total) {
        throw new RangeSessionError("文件长度已变化，请重新打开");
      }
      if (total > U64_MAX || start >= expectedEnd || from !== start || through + 1n !== expectedEnd) {
        throw new RangeErrorResponse("服务器返回的字节区间不匹配");
      }
      const etag = response.headers.get("ETag");
      if (previous && ((options.mutable && etag !== previous.etag)
          || (isStrongETag(previous.etag) && isStrongETag(etag) && etag !== previous.etag))) {
        throw new RangeSessionError("文件版本已变化，请重新打开");
      }
      if (!previous && options.mutable && expectedEnd < total && !isStrongETag(etag)) {
        throw new RangeErrorResponse("可变文件地址需要服务器提供强 ETag");
      }
      const length = Number(expectedEnd - start);
      const bytes = await readBody(response, length, length) as Uint8Array<ArrayBuffer>;
      return { bytes, total, etag, url: response.url };
    } catch (error) {
      await response.body?.cancel().catch(() => {});
      if (previous && response.status === 200 && !signal?.aborted) {
        throw new RangeSessionError("无法读取替换后的完整漫画包，请重新打开", { cause: error });
      }
      if (error instanceof RangeErrorResponse || signal?.aborted) throw error;
      throw new RangeErrorResponse("HTTP 正文传输中断，请重新打开");
    }
  }

  const initial = await request(0n, BigInt(LIMITS.prefix), options.signal);
  if ("read" in initial) return initial;
  const prefixEnd = BigInt(initial.bytes.length);
  return {
    size: initial.total,
    cachedPrefix: prefixEnd,
    async read(start, end, signal) {
      checkRange(initial.total, start, end);
      signal?.throwIfAborted();
      if (start === end) return new Uint8Array();
      if (end <= prefixEnd) return initial.bytes.slice(Number(start), Number(end));
      const missingStart = start > prefixEnd ? start : prefixEnd;
      const result = await request(missingStart, end, signal, initial);
      if ("read" in result) throw new FileReplaced(result);
      if (start === missingStart) return result.bytes;
      const first = initial.bytes.subarray(Number(start));
      const joined = new Uint8Array(first.length + result.bytes.length);
      joined.set(first);
      joined.set(result.bytes, first.length);
      return joined;
    },
  };
}

export interface Page {
  width: number;
  height: number;
  firstChunk: number;
  chunkCount: number;
}

export interface Chunk {
  page: number;
  y: number;
  height: number;
  codec: number;
  start: bigint;
  size: number;
}

export interface Thumbnail {
  width: number;
  height: number;
  start: bigint;
  size: number;
}

export type ReadingDirection = "ltr" | "rtl" | "ttb";

export function readingDirection(value: unknown): ReadingDirection | undefined {
  return value === "ltr" || value === "rtl" || value === "ttb" ? value : undefined;
}

const viewOf = (bytes: Uint8Array) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

function readThumbnailIndex(index: DataView, table: number, count: number, start: bigint, fileSize: bigint, hasCover: boolean) {
  const thumbnails: Thumbnail[] = [];
  let position = start;
  for (let page = 0; page < count; page++) {
    const entry = table + page * THUMBNAIL;
    const width = index.getUint16(entry, true);
    const height = index.getUint16(entry + 2, true);
    const size = index.getUint32(entry + 4, true);
    if (!width || !height || width > LIMITS.thumbnailDimension || height > LIMITS.thumbnailDimension
        || !size || size > LIMITS.thumbnail || index.getBigUint64(entry + 8, true) !== position - start) {
      throw new FormatError("缩略图尺寸或偏移无效");
    }
    thumbnails.push({ width, height, start: position, size });
    position += BigInt(size);
    if (position > fileSize || position > U64_MAX) throw new FormatError("缩略图超出文件范围");
  }
  if (!hasCover && position !== fileSize) throw new FormatError("缩略图区长度与文件总长不匹配");
  return thumbnails;
}

async function readHeader(source: ByteSource, signal?: AbortSignal) {
  if (source.size < BigInt(HEADER)) throw new FormatError("漫画包文件头不完整");
  const bytes = await source.read(0n, BigInt(HEADER), signal);
  const header = viewOf(bytes);
  if (header.getUint32(0) !== 0x43535452) throw new FormatError("文件不是 CSTR 漫画包");
  const version = header.getUint16(4, true);
  const flags = header.getUint16(6, true);
  if (version !== 1 || (flags & ~(FLAG_THUMBNAILS | FLAG_COVER)) !== 0) {
    throw new FormatError("不支持此 CSTR 版本或标志位");
  }
  const indexOffset = header.getBigUint64(16, true);
  const coverStart = BigInt(HEADER + COVER);
  // Older experimental packs can have their cover before the index.
  const validOffset = flags & FLAG_COVER
    ? indexOffset >= coverStart && indexOffset <= coverStart + 2097152n
    : indexOffset === BigInt(HEADER);
  if (!validOffset || indexOffset > source.size) {
    throw new FormatError("Invalid CSTR index offset");
  }
  return { header, flags, indexOffset };
}

async function coverRecord(source: ByteSource, signal?: AbortSignal) {
  const record = viewOf(await source.read(32n, 48n, signal));
  const start = record.getBigUint64(0, true);
  const size = record.getUint32(8, true);
  const width = record.getUint16(12, true), height = record.getUint16(14, true);
  return { start, size, width, height };
}

/** Fetch only the fixed header and optional cover. Page tables and body are not needed. */
export async function readCover(source: ByteSource, signal?: AbortSignal) {
  const { flags, header, indexOffset } = await readHeader(source, signal);
  if (!(flags & FLAG_COVER)) return undefined;
  const metaEnd = indexOffset + BigInt(header.getUint32(24, true)) + BigInt(header.getUint32(28, true));
  return readCoverData(source, indexOffset, metaEnd, undefined, signal);
}

async function readCoverData(source: ByteSource, indexOffset: bigint, metaEnd: bigint, expected: bigint | undefined, signal?: AbortSignal) {
  const { start, size, width, height } = await coverRecord(source, signal);
  const end = start + BigInt(size);
  const validLocation = indexOffset > 48n
    ? start === 48n && end === indexOffset
    : (start === metaEnd && end < source.size)
      || (start > metaEnd && end === source.size && (expected === undefined || start === expected));
  if (!width || !height || width > 2560 || height > 2560 || !size || size > 2 * 1024 * 1024
      || !validLocation) throw new FormatError("Invalid CSTR cover");
  const bytes = await source.read(start, end, signal);
  validateWebP(bytes, width, height);
  return { width, height, bytes };
}

export async function readComic(
  source: ByteSource, signal?: AbortSignal, { tolerateInvalidThumbnails = false } = {},
) {
  const { header, flags, indexOffset } = await readHeader(source, signal);
  const pageCount = header.getUint32(8, true);
  const chunkCount = header.getUint32(12, true);
  const indexLength = header.getUint32(24, true);
  const metaLength = header.getUint32(28, true);
  const thumbnailTable = pageCount * PAGE + chunkCount * CHUNK;
  const hasThumbnails = (flags & FLAG_THUMBNAILS) !== 0;
  if (!pageCount || chunkCount < pageCount
      || indexLength !== thumbnailTable + (hasThumbnails ? pageCount * THUMBNAIL : 0)) {
    throw new FormatError("索引长度或记录数量无效");
  }
  if (indexLength > LIMITS.index) throw new FormatError("索引超过阅读器的读取上限");
  const metaStart = indexOffset + BigInt(indexLength);
  const metaEnd = metaStart + BigInt(metaLength);
  const record = (flags & FLAG_COVER) && indexOffset === 48n ? await coverRecord(source, signal) : undefined;
  const coverBeforeBody = record?.start === metaEnd;
  if (coverBeforeBody && (!record.size || record.size > 2097152)) throw new FormatError("Invalid CSTR cover size");
  const trailingCover = record !== undefined && !coverBeforeBody;
  if (trailingCover && (record.start <= metaEnd || record.start > source.size)) {
    throw new FormatError("Invalid CSTR cover offset");
  }
  let position = metaEnd + BigInt(coverBeforeBody ? record.size : 0);
  if (position > source.size) throw new FormatError("文件在 Payload 之前截断");
  const index = viewOf(await source.read(indexOffset, metaStart, signal));
  const payloadStart = position;
  const pages: Page[] = [];
  const chunks: Chunk[] = [];
  for (let page = 0; page < pageCount; page++) {
    const entry = page * PAGE;
    const width = index.getUint32(entry, true);
    const height = index.getUint32(entry + 4, true);
    const firstChunk = index.getUint32(entry + 8, true);
    const count = index.getUint32(entry + 12, true);
    if (!width || !height || !count || firstChunk !== chunks.length || firstChunk + count > chunkCount) {
      throw new FormatError("页尺寸或块区间无效");
    }
    pages.push({ width, height, firstChunk, chunkCount: count });
    let y = 0;
    for (let i = firstChunk; i < firstChunk + count; i++) {
      const offset = pageCount * PAGE + i * CHUNK;
      const chunkHeight = index.getUint32(offset, true);
      const codec = index.getUint8(offset + 4);
      const size = index.getUint32(offset + 16, true);
      if (!chunkHeight || !size || codec < CODEC.webp || codec > CODEC.jpeg || index.getUint32(offset + 4, true) !== codec
          || index.getBigUint64(offset + 8, true) !== position - payloadStart) {
        throw new FormatError("块尺寸、编码或偏移无效");
      }
      chunks.push({ page, y, height: chunkHeight, codec, start: position, size });
      y += chunkHeight;
      position += BigInt(size);
      if (y > height || position > source.size || position > U64_MAX) {
        throw new FormatError("块超出页高或文件范围");
      }
    }
    if (trailingCover && position > record.start) throw new FormatError("Body overlaps CSTR cover");
    if (y !== height) throw new FormatError("块高之和与页高不符");
  }
  if (chunks.length !== chunkCount || (!hasThumbnails && !trailingCover && position !== source.size)) {
    throw new FormatError("索引未覆盖全部块，或文件长度不匹配");
  }
  let thumbnails: Thumbnail[] = [];
  let thumbnailError: FormatError | undefined;
  if (hasThumbnails) {
    try {
      thumbnails = readThumbnailIndex(index, thumbnailTable, pageCount, position, source.size, trailingCover);
    } catch (error) {
      if (!tolerateInvalidThumbnails || !(error instanceof FormatError)) throw error;
      thumbnailError = error;
    }
  }
  async function readThumbnails(first = 0, end = thumbnails.length, previewSignal?: AbortSignal) {
    if (!Number.isInteger(first) || !Number.isInteger(end) || first < 0 || end < first
        || end > thumbnails.length) throw new FormatError("缩略图页码超出范围");
    if (first === end) return [];
    const start = thumbnails[first].start;
    const last = thumbnails[end - 1];
    const through = last.start + BigInt(last.size);
    if (through - start > BigInt(LIMITS.chunk)) throw new FormatError("缩略图批次超过读取上限，请分批读取");
    const bytes = await source.read(start, through, previewSignal);
    return thumbnails.slice(first, end).map(thumbnail => {
      const offset = Number(thumbnail.start - start);
      return bytes.subarray(offset, offset + thumbnail.size);
    });
  }

  return {
    pages, chunks, thumbnails, thumbnailError, source, readThumbnails,
    async readCover(coverSignal?: AbortSignal) {
      if (!(flags & FLAG_COVER)) return undefined;
      const last = thumbnails.at(-1);
      const expected = last ? last.start + BigInt(last.size) : position;
      return readCoverData(source, indexOffset, metaEnd, thumbnailError ? undefined : expected, coverSignal);
    },
    async readThumbnail(page: number, previewSignal?: AbortSignal) {
      const [image] = await readThumbnails(page, page + 1, previewSignal);
      return image;
    },
    async metadata(metadataSignal?: AbortSignal): Promise<Record<string, unknown> | undefined> {
      if (metaLength === 0) return undefined;
      if (metaLength > LIMITS.metadata) throw new FormatError("元数据超过读取上限");
      const data = await source.read(metaStart, metaEnd, metadataSignal);
      try {
        if (data[0] === 0xef && data[1] === 0xbb && data[2] === 0xbf) throw new Error();
        const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data));
        if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error();
        return value as Record<string, unknown>;
      } catch {
        throw new FormatError("元数据须为无 BOM 的 UTF-8 JSON 对象");
      }
    },
  };
}

export type Comic = Awaited<ReturnType<typeof readComic>>;
