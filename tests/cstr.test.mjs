import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { FileReplaced, fileSource, FormatError, LIMITS, openHttp, RangeErrorResponse, RangeSessionError, readComic } from "../dist/cstr.js";

const spec = readFileSync(new URL("../CSTR-v1.md", import.meta.url), "utf8");
const sample = Buffer.from(spec.match(/^\x60{3}hex\r?\n([\da-f\r\n]+)^\x60{3}/m)[1].replace(/\s/g, ""), "hex");
const url = new URL("https://example.test/comic.cstr");

function fixture({ blocks = 1, bytes = LIMITS.prefix * 2, metadata = Buffer.alloc(0) } = {}) {
  const indexSize = 16 + 20 * blocks;
  const data = Buffer.alloc(32 + indexSize + metadata.length + blocks * bytes);
  data.write("CSTR");
  data.writeUInt16LE(1, 4);
  data.writeUInt32LE(1, 8);
  data.writeUInt32LE(blocks, 12);
  data.writeBigUInt64LE(32n, 16);
  data.writeUInt32LE(indexSize, 24);
  data.writeUInt32LE(metadata.length, 28);
  data.writeUInt32LE(2, 32);
  data.writeUInt32LE(blocks, 36);
  data.writeUInt32LE(blocks, 44);
  for (let i = 0; i < blocks; i++) {
    const at = 48 + 20 * i;
    data.writeUInt32LE(1, at);
    data.writeUInt8(1, at + 4);
    data.writeBigUInt64LE(BigInt(i * bytes), at + 8);
    data.writeUInt32LE(bytes, at + 16);
  }
  metadata.copy(data, 32 + indexSize);
  return data;
}

function rangeResponse(data, headers, changes = {}) {
  const [, start, last] = /^bytes=(\d+)-(\d+)$/.exec(headers.get("Range"));
  const end = Math.min(Number(last), data.length - 1);
  return new Response(data.subarray(Number(start), end + 1), {
    status: 206, headers: { "Content-Range": "bytes " + start + "-" + end + "/" + data.length, ...changes },
  });
}

test("v1 reference sample preserves page order and exact chunk ranges", async () => {
  const comic = await readComic(fileSource(new Blob([sample])));
  assert.deepEqual(comic.pages.map(page => [page.width, page.height]), [[2, 4], [2, 2]]);
  assert.deepEqual(comic.chunks.map(chunk => [chunk.page, chunk.y, chunk.start]), [
    [0, 0, 142n], [0, 2, 178n], [1, 0, 214n],
  ]);
  assert.deepEqual(await comic.metadata(), { title: "样包" });
});

test("rejects truncated files, extra bytes, bad offsets and incorrect block heights", async () => {
  const wrongOffset = Buffer.from(sample);
  wrongOffset.writeBigUInt64LE(1n, 72);
  const wrongHeight = Buffer.from(sample);
  wrongHeight.writeUInt32LE(1, 64);
  const badVersion = Buffer.from(sample);
  badVersion.writeUInt16LE(2, 4);
  for (const bytes of [sample.subarray(0, -1), Buffer.concat([sample, Buffer.alloc(1)]),
    wrongOffset, wrongHeight, badVersion]) {
    await assert.rejects(readComic(fileSource(new Blob([bytes]))), FormatError);
  }
});

test("rejects malformed metadata independently of a valid index", async () => {
  const data = fixture({ bytes: 1, metadata: Buffer.from([0xef, 0xbb, 0xbf, 0x7b, 0x7d]) });
  const comic = await readComic(fileSource(new Blob([data])));
  await assert.rejects(comic.metadata(), FormatError);
  assert.equal(comic.chunks.length, 1);
});

test("a small comic is opened with a single Range request", async () => {
  const requests = [];
  const source = await openHttp(url, { fetch: async (_url, { headers }) => {
    requests.push(headers.get("Range"));
    return rangeResponse(sample, headers);
  } });
  const comic = await readComic(source);
  const first = comic.chunks[0];
  assert.equal((await source.read(first.start, first.start + BigInt(first.size))).length, 36);
  assert.deepEqual(requests, ["bytes=0-65535"]);
});

test("reads only the missing part of a chunk and of an index that crosses 64 KiB", async () => {
  for (const data of [fixture(), fixture({ blocks: 3300, bytes: 1 })]) {
    const requests = [];
    const source = await openHttp(url, { fetch: async (_url, { headers }) => {
      requests.push(headers.get("Range"));
      return rangeResponse(data, headers);
    } });
    const comic = await readComic(source);
    const first = comic.chunks[0];
    await source.read(first.start, first.start + BigInt(first.size));
    assert.equal(requests[0], "bytes=0-65535");
    assert.match(requests[1], /^bytes=65536-/);
    assert.equal(requests.length, comic.chunks.length === 1 ? 2 : 3);
  }
});

test("rejects wrong Content-Range, short bodies and 416", async () => {
  const data = fixture();
  const requests = [
    async (_url, { headers }) => rangeResponse(data, headers, { "Content-Range": "bytes 1-65535/200000" }),
    async () => new Response(new Uint8Array(1), {
      status: 206, headers: { "Content-Range": "bytes 0-65535/200000" },
    }),
    async () => new Response("", { status: 416 }),
  ];
  for (const fetch of requests) await assert.rejects(openHttp(url, { fetch }), RangeErrorResponse);
});

test("handles an initial 200 and never mixes a newer full response into an older file", async () => {
  const full = await openHttp(url, { fetch: async () => new Response(sample) });
  assert.equal((await readComic(full)).pages.length, 2);

  const data = fixture();
  let requested = 0;
  const source = await openHttp(url, { mutable: true, fetch: async (_url, { headers }) => {
    if (!requested++) return rangeResponse(data, headers, { ETag: '"edition-one"' });
    assert.equal(headers.get("If-Range"), '"edition-one"');
    return new Response(sample);
  } });
  const first = (await readComic(source)).chunks[0];
  await assert.rejects(source.read(first.start, first.start + BigInt(first.size)), asyncReplacement);
  function asyncReplacement(error) {
    assert.ok(error instanceof FileReplaced);
    assert.equal(error.source.size, BigInt(sample.length));
    return true;
  }
});

test("a strong ETag cannot match ranges across redirect targets", async () => {
  const data = fixture();
  let target = "https://example.test/edition-one.cstr";
  const source = await openHttp(url, { mutable: true, fetch: async (_url, { headers }) => {
    const response = rangeResponse(data, headers, { ETag: '"revision-one"' });
    Object.defineProperty(response, "url", { value: target });
    return response;
  } });
  const start = BigInt(LIMITS.prefix - 1);
  assert.equal((await source.read(start, start + 2n)).length, 2);
  target = "https://example.test/edition-two.cstr";
  await assert.rejects(source.read(start, start + 2n), RangeSessionError);
});

test("an empty 416 invalidates the range session", async () => {
  const data = fixture();
  let first = true;
  const source = await openHttp(url, { fetch: async (_url, { headers }) => {
    if (!first) return new Response(null, { status: 416 });
    first = false;
    return rangeResponse(data, headers);
  } });
  const start = BigInt(LIMITS.prefix);
  await assert.rejects(source.read(start, start + 1n), RangeSessionError);
});
