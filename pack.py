"""Pack naturally sorted manga pages into a CSTR file."""

import argparse
from contextlib import nullcontext
from fractions import Fraction
from io import BytesIO
import json
from math import ceil
from pathlib import Path, PurePosixPath
import re
from shutil import copyfileobj
from struct import Struct
from tempfile import NamedTemporaryFile, TemporaryFile
import warnings
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps

HEADER = Struct("<4sHHIIQII")
PAGE = Struct("<IIII")
CHUNK = Struct("<IB3xQI")
THUMBNAIL = Struct("<HHIQ")
FLAG_THUMBNAILS = 1
READING_DIRECTIONS = ("ltr", "rtl", "ttb")
THUMBNAIL_BYTES = 256 * 1024
U32_MAX = (1 << 32) - 1
WEBP_MAX_DIMENSION = 16383
READER_INDEX_BYTES = 8 * 1024 * 1024
READER_METADATA_BYTES = 256 * 1024
READER_CHUNK_BYTES = 32 * 1024 * 1024
READER_DECODED_BYTES = 128 * 1024 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp", ".tif", ".tiff"}


def natural_key(path):
    path = PurePosixPath(str(path).replace("\\", "/"))
    components = tuple(
        tuple((1, len(token.lstrip("0")), token.lstrip("0")) if token.isascii() and token.isdigit()
              else (0, token)
              for token in re.split(r"([0-9]+)", component.casefold()))
        for component in path.parts
    )
    return components, path.as_posix().casefold(), path.as_posix()


def archive_pages(archive):
    candidates = []
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename.replace("\\", "/"))
        if (not entry.is_dir() and path.suffix.lower() in IMAGE_EXTENSIONS
                and all(part != "__MACOSX" and not part.startswith(".") for part in path.parts)):
            candidates.append((entry, path))
    candidates.sort(key=lambda candidate: natural_key(candidate[1]))
    return [entry for entry, _ in candidates]


def write_thumbnail(image, destination, table):
    width, height = image.size
    # Keep normal pages whole. Crop extreme aspect ratios before resizing.
    width, height = min(width, height * 2), min(height, width * 2)
    scale = min(1, max(Fraction(160, width), Fraction(224, height)))
    size = (ceil(width * scale), ceil(height * scale))
    left = (image.width - width) // 2
    with image.resize(size, Image.Resampling.LANCZOS, box=(left, 0, left + width, height)) as thumb:
        with BytesIO() as buffer:
            thumb.save(buffer, "WEBP", quality=72)
            with buffer.getbuffer() as data:
                if not 0 < len(data) <= THUMBNAIL_BYTES:
                    raise ValueError("Page thumbnail exceeds its size limit")
                table.extend(THUMBNAIL.pack(*size, len(data), destination.tell()))
                destination.write(data)


def pack(output, inputs, *, strip_height=0, quality=85, lossless=False, metadata=None,
         archive=None, thumbnails=False, reading_direction=None):
    page_record_size = PAGE.size + (THUMBNAIL.size if thumbnails else 0)
    if not inputs:
        raise ValueError("A nonempty list of pages is required")
    max_chunks = (READER_INDEX_BYTES - page_record_size * len(inputs)) // CHUNK.size
    if len(inputs) > max_chunks:
        raise ValueError(f"Index exceeds ComicStream's reading limit ({READER_INDEX_BYTES} bytes)")
    if not 0 <= strip_height <= WEBP_MAX_DIMENSION or not 0 <= quality <= 100:
        raise ValueError("Strip height must be 0–16383 and quality must be 0–100")
    inputs = sorted(inputs, key=natural_key if archive is None else lambda entry: natural_key(entry.filename))
    original_paths = inputs if archive is None else [Path(archive.filename)]
    resolved_output = output.resolve()
    if any(resolved_output == path.resolve() for path in original_paths):
        raise ValueError("The output path is also an input page")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Metadata must be a JSON object")
    if reading_direction is not None:
        metadata = {**(metadata or {}), "readingDirection": reading_direction}
    if metadata is not None and "readingDirection" in metadata:
        if metadata["readingDirection"] not in READING_DIRECTIONS:
            raise ValueError("Reading direction must be ltr, rtl or ttb")
    meta = b"" if metadata is None else json.dumps(
        metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(meta) > READER_METADATA_BYTES:
        raise ValueError(f"Metadata exceeds ComicStream's {READER_METADATA_BYTES // 1024} KiB reading limit")

    temporary_output = None
    try:
        with (TemporaryFile(dir=output.parent) as payload,
              (TemporaryFile(dir=output.parent) if thumbnails else nullcontext()) as previews):
            page_table = bytearray()
            chunk_table = bytearray()
            thumbnail_table = bytearray()
            chunk_count = 0
            offset = 0
            for path in inputs:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    source = nullcontext(path) if archive is None else archive.open(path)
                    label = path if archive is None else path.filename
                    with source as page_source, Image.open(page_source) as image:
                        if getattr(image, "n_frames", 1) != 1:
                            raise ValueError(f"Expected a single image frame: {label}")
                        # Transpose in place before cropping so strip order matches display order.
                        ImageOps.exif_transpose(image, in_place=True)
                        width, height = image.size
                        if width > WEBP_MAX_DIMENSION or height > U32_MAX:
                            raise ValueError(f"Image dimensions are too large: {label}")
                        if previews is not None:
                            write_thumbnail(image, previews, thumbnail_table)
                        step = min(strip_height or height, WEBP_MAX_DIMENSION, READER_DECODED_BYTES // (4 * width))
                        first_chunk = chunk_count
                        y = 0
                        while y < height:
                            bottom = min(y + step, height)
                            if chunk_count >= max_chunks:
                                raise ValueError(f"Index exceeds ComicStream's reading limit ({READER_INDEX_BYTES} bytes)")
                            with image.crop((0, y, width, bottom)) as strip, BytesIO() as buffer:
                                strip.save(buffer, "WEBP", quality=quality, lossless=lossless, method=4)
                                with buffer.getbuffer() as data:
                                    if len(data) > READER_CHUNK_BYTES and bottom - y > 1:
                                        step = (bottom - y) // 2
                                        continue
                                    if not 0 < len(data) <= READER_CHUNK_BYTES or offset + len(data) > (1 << 64) - 1:
                                        raise ValueError("Image data exceeds the CSTR v1 size limit")
                                    chunk_table.extend(CHUNK.pack(bottom - y, 1, offset, len(data)))
                                    payload.write(data)
                                    offset += len(data)
                            chunk_count += 1
                            y = bottom
                        page_table.extend(PAGE.pack(width, height, first_chunk, chunk_count - first_chunk))
            index_size = len(page_table) + len(chunk_table) + len(thumbnail_table)
            preview_size = previews.tell() if previews is not None else 0
            if HEADER.size + index_size + len(meta) + offset + preview_size > (1 << 64) - 1:
                raise ValueError("File exceeds the CSTR size limit")
            with NamedTemporaryFile(dir=output.parent, delete=False) as destination:
                temporary_output = Path(destination.name)
                destination.write(HEADER.pack(b"CSTR", 1, FLAG_THUMBNAILS if thumbnails else 0,
                                              len(inputs), chunk_count, HEADER.size, index_size, len(meta)))
                destination.write(page_table)
                destination.write(chunk_table)
                destination.write(thumbnail_table)
                destination.write(meta)
                payload.seek(0)
                copyfileobj(payload, destination, length=1024 * 1024)
                if previews is not None:
                    previews.seek(0)
                    copyfileobj(previews, destination, length=1024 * 1024)
        temporary_output.replace(output)
    finally:
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)
    return chunk_count


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("-o", "--output", type=Path)
    cli.add_argument("--strip-height", type=int, default=0,
                     help="Maximum strip height in pixels (0 cuts only when required by reader or WebP limits)")
    cli.add_argument("--quality", type=int, default=85)
    cli.add_argument("--lossless", action="store_true")
    cli.add_argument("--metadata", type=Path, help="Path to a UTF-8 JSON object")
    cli.add_argument("--thumbnails", action="store_true", help="Embed optional page thumbnails in CSTR v1")
    cli.add_argument("--reading-direction", choices=READING_DIRECTIONS,
                     help="Set the readingDirection metadata field")
    cli.add_argument("--manifest", type=Path,
                     help="UTF-8 JSON array of image paths relative to the manifest")
    cli.add_argument("--cbz", type=Path, help="Read image pages directly from a CBZ archive")
    cli.add_argument("--list-pages", action="store_true", help="Print the final page order without writing a pack")
    cli.add_argument("images", type=Path, nargs="*", help="Input images, sorted by natural path order")
    args = cli.parse_args()
    try:
        if sum((bool(args.images), bool(args.manifest), bool(args.cbz))) != 1:
            raise ValueError("Choose one input: positional images, --manifest or --cbz")
        if not args.list_pages and args.output is None:
            raise ValueError("--output is required to write a pack")
        inputs = args.images
        if args.manifest:
            entries = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(entries, list) or any(not isinstance(path, str) or not path for path in entries):
                raise ValueError("Manifest must be an ordered JSON array of nonempty image paths")
            inputs = [args.manifest.parent / path for path in entries]
        metadata = None
        if args.metadata:
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("Metadata must be a JSON object")
        with (ZipFile(args.cbz) if args.cbz else nullcontext()) as archive:
            if archive is not None:
                inputs = archive_pages(archive)
            if not inputs:
                raise ValueError("No image pages found")
            if args.list_pages:
                pages = sorted(inputs, key=natural_key) if archive is None else inputs
                for index, page in enumerate(pages, 1):
                    print(f"{index}\t{page if archive is None else page.filename}")
                return
            count = pack(args.output, inputs, strip_height=args.strip_height, quality=args.quality,
                         lossless=args.lossless, metadata=metadata, archive=archive,
                         thumbnails=args.thumbnails, reading_direction=args.reading_direction)
    except (OSError, ValueError, BadZipFile, RuntimeError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        cli.exit(1, f"cstr: {error}\n")
    print(f"{args.output} ({len(inputs)} pages, {count} chunks)")


if __name__ == "__main__":
    main()
