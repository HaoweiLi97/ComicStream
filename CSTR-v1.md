# CSTR v1 binary specification

English · [简体中文](CSTR-v1.zh-CN.md)

CSTR stores the index at the front of the file so a reader can locate individual pages, image chunks, and thumbnails by byte range.

```text
[Header][Page table][Chunk table][Thumbnail table*][JSON metadata*][Image chunks][Thumbnail data*]

* Optional. Header flag bit 0 controls both thumbnail sections.
```

All multi-byte integers are unsigned and little-endian. Records are packed without padding. Page and chunk indices are zero-based. Byte intervals are half-open unless written as an HTTP Range.

## File header

| File offset | Type | Field | Value or constraint |
| ---: | --- | --- | --- |
| 0 | `u8[4]` | `magic` | ASCII `CSTR` (`43 53 54 52`) |
| 4 | `u16` | `version` | `1` |
| 6 | `u16` | `flags` | Bit 0 indicates thumbnails. All other bits must be zero. |
| 8 | `u32` | `page_count` | At least 1 |
| 12 | `u32` | `chunk_count` | At least `page_count` |
| 16 | `u64` | `index_offset` | `32` |
| 24 | `u32` | `index_size` | Combined byte length of the tables |
| 28 | `u32` | `meta_size` | UTF-8 byte length of metadata, or zero |

```text
index_size = 16 × page_count + 20 × chunk_count
           + (16 × page_count if flags & 1, otherwise 0)
meta_start = index_offset + index_size
body_start = meta_start + meta_size
```

## Page table

The page table starts at `index_offset` and contains one 16-byte record per page in reading order.

| Record offset | Type | Field | Meaning |
| ---: | --- | --- | --- |
| 0 | `u32` | `width` | Page width in pixels, positive |
| 4 | `u32` | `height` | Page height in pixels, positive |
| 8 | `u32` | `first_chunk` | Index of the first chunk in the chunk table |
| 12 | `u32` | `chunk_count` | Number of chunks on the page, positive |

The first page starts at chunk zero. Each subsequent page starts immediately after the previous page's chunks. The final page's range ends at the total chunk count in the header.

## Chunk table and image data

The chunk table starts at `index_offset + 16 × page_count` and contains one 20-byte record per chunk. Chunks follow page order and run from top to bottom within each page.

| Record offset | Type | Field | Meaning |
| ---: | --- | --- | --- |
| 0 | `u32` | `height` | Chunk height in pixels, positive |
| 4 | `u8` | `codec` | `1` WebP, `2` AVIF, `3` PNG |
| 5 | `u8[3]` | `reserved` | All zero |
| 8 | `u64` | `offset` | Byte offset relative to `body_start` |
| 16 | `u32` | `size` | Encoded byte length, positive |

Each chunk is a complete single-frame image, as wide as its page and as tall as the record specifies. The sum of chunk heights must equal the page height. A chunk's vertical origin is the sum of the preceding chunk heights on its page. Preserve shared boundaries when scaling adjacent chunks. Apply the source image's orientation before encoding.

WebP is required for readers and is the format written by ComicStream. Readers may support AVIF and PNG. Report an assigned codec a reader cannot decode at the chunk level so other chunks remain readable. Codec values outside the table are invalid.

```text
chunk_start = body_start + chunk.offset
chunk_end   = chunk_start + chunk.size
body_end    = body_start + last_chunk.offset + last_chunk.size
```

The first chunk has offset zero. Each following chunk starts at the previous chunk's end. If thumbnails are absent, `body_end` is the file length.

## Optional thumbnail table and data

When flag bit 0 is set, a 16-byte record for each page follows the chunk table. The thumbnail images start at `body_end` and follow the same order as the page table.

| Record offset | Type | Field | Meaning |
| ---: | --- | --- | --- |
| 0 | `u16` | `width` | Thumbnail width in pixels, 1–512 |
| 2 | `u16` | `height` | Thumbnail height in pixels, 1–512 |
| 4 | `u32` | `size` | Encoded byte length, 1–262,144 |
| 8 | `u64` | `offset` | Byte offset relative to the thumbnail data start |

```text
thumbnail_table = index_offset + 16 × page_count + 20 × chunk_count
thumbnail_start = body_end
image_start     = thumbnail_start + thumbnail.offset
image_end       = image_start + thumbnail.size
file_size       = thumbnail_start + last_thumbnail.offset + last_thumbnail.size
```

Each thumbnail is a complete single-frame WebP. The dimensions in the record must match the image. The first thumbnail has offset zero. Each following thumbnail starts at the previous thumbnail's end. The final thumbnail ends at the end of the file. The format allows neither padding between images nor trailing bytes.

## JSON metadata

If `meta_size` is positive, the section at `meta_start` contains a UTF-8 JSON object without a byte order mark. Readers may display `title` and may use `readingDirection` to configure navigation. Defined direction values are `ltr` (left to right), `rtl` (right to left), and `ttb` (top to bottom). ComicStream defaults to `ltr` when the field is absent or unrecognized. Ignore unknown keys. The binary tables define page order, dimensions, codecs, and offsets.

## Validation and recovery

1. Read the file length and header. Validate the magic, version, flags, counts, and index bounds before reading the tables. Use integers that can represent unsigned 64-bit offsets, such as `BigInt` in JavaScript.
2. Validate page and chunk ranges, chunk heights, image offsets, and the expected file length. Validate the thumbnail table and tail when the thumbnail flag is set. Check metadata independently so a bad JSON object can be reported without blocking body images.
3. Before decoding, check the image header dimensions and enforce the decoder's memory budget. Verify the decoded dimensions against the index. Report corrupt image data at the affected chunk or thumbnail.

ComicStream limits the index to 8 MiB and JSON metadata to 256 KiB. Its TypeScript `readComic` function validates the thumbnail table and tail by default. The browser enables `tolerateInvalidThumbnails` so a bad thumbnail table or tail can disable previews while the validated body remains available. The function returns `thumbnailError` and an empty thumbnail list in that case.

`readThumbnail(page, signal)` returns the complete WebP bytes for one zero-based page. `readThumbnails(first, end, signal)` uses a half-open page interval and returns one byte array per image. Omitting the interval reads the entire thumbnail section, subject to a 32 MiB batch limit.

## HTTP Range reads

ComicStream first requests `Range: bytes=0-65535` and reuses the returned prefix. It requests any missing index bytes, then fetches body chunks or thumbnails as needed.

- Convert `[start, end)` to an inclusive HTTP Range with `Range: bytes=start-(end-1)`. Validate the response's `Content-Range` bounds, total length, and body length. Adjacent images can share one request.
- For mutable URLs, require a strong ETag before issuing later requests and send it in `If-Range`. Confirm that subsequent partial responses still refer to the same resource.
- Treat a `200` as a candidate complete file within the download budget. Discard previous partial bytes before parsing it. A `416` invalidates the current byte-range session.
- Serve the original bytes without a content encoding transformation. For cross-origin access, expose `Content-Range` and `ETag` and allow `If-Range` for mutable URLs. [HTTP Range semantics](https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests)

## Reference file

This 250-byte file contains two pages measuring `2 × 4` and `2 × 2` pixels. It has three `2 × 2` WebP chunks, in red, green, and blue. The first two chunks form page 0. The index is 92 bytes, the JSON metadata is 18 bytes, and the body starts at byte 142. Join the lines and decode the hexadecimal text to recover the complete file.

```hex
4353545201000000020000000300000020000000000000005c00000012000000
02000000040000000000000002000000
02000000020000000200000001000000
0200000001000000000000000000000024000000
0200000001000000240000000000000024000000
0200000001000000480000000000000024000000
7b227469746c65223a22e6a0b7e58c85227d
524946461c000000574542505650384c0f0000002f014000000710f58ffe0722a2ff0100
524946461c000000574542505650384c0f0000002f014000000750c0ccfe0722a2ff0100
524946461c000000574542505650384c0f0000002f014000000710d1fffe0722a2ff0100
```

| Chunk | Page | Page-relative y range | File byte range | HTTP request header |
| ---: | ---: | --- | --- | --- |
| 0 | 0 | `[0, 2)` | `[142, 178)` | `Range: bytes=142-177` |
| 1 | 0 | `[2, 4)` | `[178, 214)` | `Range: bytes=178-213` |
| 2 | 1 | `[0, 2)` | `[214, 250)` | `Range: bytes=214-249` |

Request `Range: bytes=142-213` to read chunks 0 and 1 together. The response has `Content-Range: bytes 142-213/250` and a 72-byte body that splits into two 36-byte chunks. [Run the format tests](README.md#verify)
