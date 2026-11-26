"""Read CSTR v1 indexes and individual images from a file or HTTP byte ranges."""

from dataclasses import dataclass
from io import BytesIO
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from PIL import Image

from pack import (CHUNK, CODECS, COVER, COVER_MAX_EDGE, COVER_MAX_KIB, FLAG_COVER, FLAG_THUMBNAILS,
                  HEADER, PAGE, READER_CHUNK_BYTES, READER_INDEX_BYTES, READER_METADATA_BYTES,
                  THUMBNAIL, THUMBNAIL_BYTES, fits_reader)


FORMATS = {value: key for key, value in CODECS.items()}
WHOLE_FILE_LIMIT = 64 * 1024 * 1024
CONTENT_RANGE = re.compile(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})", re.IGNORECASE)


class FileBytes:
    def __init__(self, path):
        self.path = path
        self.size = path.stat().st_size

    def read(self, start, count):
        with self.path.open("rb") as stream:
            stream.seek(start)
            data = stream.read(count)
        if len(data) != count:
            raise ValueError("The CSTR file was truncated or changed.")
        return data


class HttpRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, stream, code, message, headers, url):
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("The link must use HTTP or HTTPS.")
        return super().redirect_request(request, stream, code, message, headers, url)


class HttpBytes:
    def __init__(self, url):
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("The link must use HTTP or HTTPS.")
        self.opener = build_opener(HttpRedirects)
        self.url = url
        self.target = None
        self.etag = None
        self.prefix = self.request(0, 65536)

    def request(self, start, count):
        headers = {"Range": f"bytes={start}-{start + count - 1}", "Accept-Encoding": "identity"}
        if self.etag:
            headers["If-Range"] = self.etag
        with self.opener.open(Request(self.url, headers=headers), timeout=12) as response:
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("The server must send the original file bytes.")
            if response.status == 200 and self.target is None:
                data = response.read(WHOLE_FILE_LIMIT + 1)
                if len(data) > WHOLE_FILE_LIMIT:
                    raise ValueError("The server must support byte ranges for files over 64 MiB.")
                self.size = len(data)
                return data
            if response.status != 206:
                raise ValueError("The remote file changed or byte ranges are unavailable. Reopen the link.")
            match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if match is None:
                raise ValueError("Missing or invalid HTTP Content-Range.")
            first, last, total = map(int, match.groups())
            end = min(start + count, total)
            if not (0 <= first == start <= last == end - 1 and total < 2**64):
                raise ValueError("The server returned an unexpected byte range.")
            etag = response.headers.get("ETag", "")
            if self.target is None:
                self.target = response.url
                self.size = total
                self.etag = etag if re.fullmatch(r'"[^\"]*"', etag) else None
            elif response.url != self.target or total != self.size or (self.etag and etag != self.etag):
                raise ValueError("The remote file changed. Reopen the link.")
            data = response.read(end - start + 1)
            if len(data) != end - start:
                raise ValueError("The server returned an incomplete byte range.")
            return data

    def read(self, start, count):
        end = start + count
        prefix = self.prefix[start:end]
        return prefix if len(prefix) == count else prefix + self.request(start + len(prefix), count - len(prefix))


@dataclass(frozen=True, slots=True)
class ImageRecord:
    width: int
    height: int
    start: int
    length: int
    format: str


@dataclass(frozen=True, slots=True)
class ComicPage:
    width: int
    height: int
    chunks: tuple[ImageRecord, ...]


class Comic:
    def __init__(self, source):
        self.source = source
        self.pages = []
        self.cover = None
        self.thumbnails = []
        self.metadata = {}
        if source.size < HEADER.size:
            raise ValueError("The CSTR header is incomplete.")
        magic, version, flags, pages, chunks, start, size, metadata_size = HEADER.unpack(source.read(0, HEADER.size))
        if magic != b"CSTR" or version != 1 or flags & ~(FLAG_COVER | FLAG_THUMBNAILS):
            raise ValueError("This is not a supported CSTR v1 file.")
        has_cover, has_thumbnails = flags & FLAG_COVER, flags & FLAG_THUMBNAILS
        thumbnail_table = pages * PAGE.size + chunks * CHUNK.size
        metadata_at = start + size
        metadata_end = metadata_at + metadata_size
        fixed_end = HEADER.size + COVER.size
        if (not 0 < pages <= chunks or size > READER_INDEX_BYTES
                or size != thumbnail_table + (pages * THUMBNAIL.size if has_thumbnails else 0)
                or metadata_size > READER_METADATA_BYTES or metadata_end > source.size
                or (not fixed_end <= start <= fixed_end + COVER_MAX_KIB * 1024 if has_cover else start != HEADER.size)):
            raise ValueError("The CSTR index is invalid.")

        position = metadata_end
        end = source.size
        if has_cover:
            offset, length, width, height = COVER.unpack(source.read(HEADER.size, COVER.size))
            if not (0 < width <= COVER_MAX_EDGE and 0 < height <= COVER_MAX_EDGE
                    and 0 < length <= COVER_MAX_KIB * 1024):
                raise ValueError("The CSTR cover descriptor is invalid.")
            if start > fixed_end and offset == fixed_end and offset + length == start:
                pass  # Older experimental files stored the cover before the index.
            elif start == fixed_end and offset == position:
                position += length
            elif start == fixed_end and offset > position and offset + length == end:
                end = offset
            else:
                raise ValueError("The CSTR cover location is invalid.")
            self.cover = ImageRecord(width, height, offset, length, "WEBP")

        index = source.read(start, size)
        body = position
        next_chunk = 0
        for page in range(pages):
            width, height, first, count = PAGE.unpack_from(index, page * PAGE.size)
            if not (width and height and count and first == next_chunk and first + count <= chunks):
                raise ValueError("The CSTR page table is invalid.")
            records = []
            y = 0
            for i in range(first, first + count):
                at = pages * PAGE.size + i * CHUNK.size
                h, codec, relative, length = CHUNK.unpack_from(index, at)
                if (codec not in FORMATS or index[at + 5:at + 8] != bytes(3) or not fits_reader(width, h)
                        or relative != position - body or not 0 < length <= READER_CHUNK_BYTES
                        or position + length > end or y + h > height):
                    raise ValueError("The CSTR chunk table is invalid.")
                records.append(ImageRecord(width, h, position, length, FORMATS[codec]))
                y += h
                position += length
            if y != height:
                raise ValueError("The CSTR chunks do not match the page height.")
            self.pages.append(ComicPage(width, height, tuple(records)))
            next_chunk += count
        if next_chunk != chunks or (not has_thumbnails and position != end):
            raise ValueError("The CSTR body length does not match the index.")

        if has_thumbnails:
            base = position
            for i in range(pages):
                width, height, length, relative = THUMBNAIL.unpack_from(index, thumbnail_table + i * THUMBNAIL.size)
                if (not 0 < width <= 512 or not 0 < height <= 512 or not 0 < length <= THUMBNAIL_BYTES
                        or relative != position - base or position + length > end):
                    break
                self.thumbnails.append(ImageRecord(width, height, position, length, "WEBP"))
                position += length
            if len(self.thumbnails) != pages or position != end:
                self.thumbnails.clear()  # Optional thumbnails may be damaged while the body remains intact.

        if metadata_size:
            try:
                value = json.loads(source.read(metadata_at, metadata_size).decode("utf-8"))
                if isinstance(value, dict):
                    self.metadata = value
            except (UnicodeError, json.JSONDecodeError):
                pass

    def image(self, record, size):
        with Image.open(BytesIO(self.source.read(record.start, record.length)), formats=[record.format]) as image:
            if image.size != (record.width, record.height) or getattr(image, "n_frames", 1) != 1:
                raise ValueError("The image does not match its CSTR record.")
            # Bound decoded input with the validated record before resizing for display.
            with image.convert("RGBA") as rgba:
                return rgba.resize(size, Image.Resampling.LANCZOS)
