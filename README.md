# ComicStream

English · [简体中文](README.zh-CN.md)

CSTR (`.cstr`) is a comic file format optimized for streaming. Its binary index lets a reader load a page, a visible segment of a long webtoon, or a complete page thumbnail on demand over HTTP Range. ComicStream includes a Python packer and a browser reader.

## CSTR file layout

```text
[Header][Page table][Chunk table][Thumbnail table*][JSON metadata*][Image chunks][Thumbnail data*]

* Optional. Header flag bit 0 enables both thumbnail sections.
```

| Section | Contents | How readers use it |
| --- | --- | --- |
| Header (32 bytes) | `CSTR` magic, version, flags, page and chunk counts, index offset and size, metadata size | Locate the index, metadata, and image data |
| Page table (16 bytes per page) | Page width and height, first chunk index, chunk count | Locate a page's chunks and reserve space on screen |
| Chunk table (20 bytes per chunk) | Chunk height, codec, offset relative to the body, byte length | Locate and fetch a full-width image segment |
| Thumbnail table (16 bytes per page, optional) | Thumbnail width and height, byte length, offset relative to thumbnail data | Fetch a complete thumbnail or a contiguous group |
| JSON metadata (optional) | UTF-8 object with fields such as `title` and `readingDirection` | Display a title and configure page navigation |
| Image chunks | Contiguous single-frame images in reading order | Read one chunk for a typical page or several for a long page |
| Thumbnail data (optional) | Contiguous single-frame WebP images in page order | Load small images for page previews |

The current format is **CSTR v1**. Multi-byte integers are unsigned and little-endian. Page and chunk indices are zero-based. Files with thumbnails use `version = 1` and `flags = 0x0001`. Files with `flags = 0` have no thumbnail sections. [Binary specification and byte sample](CSTR-v1.md)

## Locate an image by byte range

1. Read the header and index. The first body chunk begins at `index_offset + index_size + meta_size`.
2. Look up the page's first chunk and count. A chunk's vertical position is the sum of the heights of the earlier chunks on that page. Its byte range starts at `body_start + chunk.offset`.
3. If present, thumbnail data begins immediately after the final body chunk. The target image begins at `thumbnail_start + thumbnail.offset`. Read `thumbnail.size` bytes to get one complete WebP.

File offsets use half-open ranges. In the reference sample, the first chunk occupies `[142, 178)`, which maps to `Range: bytes=142-177`. Readers can combine adjacent chunks or thumbnails into one request and split the response using the index. [HTTP Range behavior](CSTR-v1.md#http-range-reads) · [Byte sample](CSTR-v1.md#reference-file)

The optional `readingDirection` field accepts `ltr` (left to right), `rtl` (right to left), or `ttb` (top to bottom). The index always follows reading order. The browser reader uses the field to configure navigation, including left and right arrow keys and thumbnail order in RTL mode.

## Pack a CBZ or image files

Requires Python 3.10 or later. AVIF input also requires a Pillow build with AVIF support. Run these commands from the project directory.

~~~powershell
py -m pip install -r requirements.txt
py pack.py --cbz book.cbz --list-pages
py pack.py --cbz book.cbz -o book.cstr
py pack.py --cbz book.cbz --thumbnails --reading-direction rtl -o book.cstr
py pack.py --cbz book.cbz --webp -o book-webp.cstr
py pack.py --cbz webtoon.cbz --strip-height 1600 -o webtoon.cstr
py pack.py -o book.cstr 10.png 2.png 1.png
~~~

The packer sorts paths naturally, so images named `1.png`, `2.png`, and `10.png` appear in that order. To select images with `--manifest pages.json`, supply a JSON array of paths relative to the manifest. Use `--list-pages` with any input type to check the resulting order.

The packer preserves the original bytes of whole-page JPEG, PNG, WebP, and AVIF images when they fit the reader's limits and need no orientation or layout normalization. It encodes pages that need normalization, strips, or a format conversion as WebP. Single-frame GIF, BMP, and TIFF can be imported through conversion. Animated and other multi-frame inputs are rejected.

The reader allows up to 128 MiB of decoded RGBA and 32 MiB of encoded data per chunk. Use `--strip-height` to set a maximum strip height. Use `--webp` for readers that only support WebP, or use `--quality` or `--lossless` to force WebP at the selected quality or in lossless mode.

`--thumbnails` embeds one thumbnail per page. Thumbnails retain their aspect ratio and target a minimum width of 160 px and height of 224 px without upscaling. The packer uses the top of a very tall page or the center of a very wide page. `--metadata` accepts a UTF-8 JSON object, and `--reading-direction` takes precedence over its `readingDirection` field.

ComicStream's packer and reader both limit the index to 8 MiB and JSON metadata to 256 KiB.

## Open a comic in the browser

Requires Node.js 24 or later.

~~~powershell
npm ci
npm run build
npm start
~~~

Open [the local reader](http://127.0.0.1:4173/), then select a local `.cstr` file or enter an HTTP(S) URL. The reader supports page-number jumps, previous and next controls, and arrow keys when the reading area has focus. The page preview control appears for files with a valid thumbnail index. If that index is corrupt or the thumbnail section is truncated, the reader can still show the body when its index and byte ranges are intact.

The development server supports Range requests for files in `library/`. For example, open `library/book.cstr` at `/library/book.cstr`. Remote servers should support single-range requests and serve the original bytes. For cross-origin requests, allow the reader's origin and expose `Content-Range` and `ETag`. For a URL whose file may change, use the reader's mutable-link option and configure the server to provide a strong ETag and allow `If-Range`.

## Verify

~~~powershell
npm test
py tests/create_fixture.py
~~~

To run the browser tests, leave `npm start` running and install Python Playwright and Chromium. Run `py tests/browser.py` to check local files, HTTP Range reads, out-of-order responses, previews, and page navigation. Set `CSTR_CHROME` to use an installed browser or `CSTR_PORT` to change the local port.
