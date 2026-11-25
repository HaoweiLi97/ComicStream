"""Pack naturally sorted manga pages into a CSTR v1 file."""

import argparse
from contextlib import nullcontext
from io import BytesIO
import json
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
U32_MAX = (1 << 32) - 1
WEBP_MAX_DIMENSION = 16383
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


def pack(output, inputs, *, strip_height=0, quality=85, lossless=False, metadata=None,
         archive=None):
    if not inputs or len(inputs) > U32_MAX // (PAGE.size + CHUNK.size):
        raise ValueError("A representable, nonempty list of pages is required")
    if not 0 <= strip_height <= WEBP_MAX_DIMENSION or not 0 <= quality <= 100:
        raise ValueError("Strip height must be 0–16383 and quality must be 0–100")
    inputs = sorted(inputs, key=natural_key if archive is None else lambda entry: natural_key(entry.filename))
    original_paths = inputs if archive is None else [Path(archive.filename)]
    resolved_output = output.resolve()
    if any(resolved_output == path.resolve() for path in original_paths):
        raise ValueError("The output path is also an input page")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Metadata must be a JSON object")
    meta = b"" if metadata is None else json.dumps(
        metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(meta) > U32_MAX:
        raise ValueError("Metadata exceeds the CSTR v1 size limit")

    temporary_output = None
    try:
        with TemporaryFile(dir=output.parent) as payload:
            page_table = bytearray()
            chunk_table = bytearray()
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
                        step = min(strip_height or height, WEBP_MAX_DIMENSION, READER_DECODED_BYTES // (4 * width))
                        first_chunk = chunk_count
                        y = 0
                        while y < height:
                            bottom = min(y + step, height)
                            if PAGE.size * len(inputs) + CHUNK.size * (chunk_count + 1) > U32_MAX:
                                raise ValueError("Index exceeds the CSTR v1 size limit")
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
            if HEADER.size + len(page_table) + len(chunk_table) + len(meta) + offset > (1 << 64) - 1:
                raise ValueError("File exceeds the CSTR v1 size limit")
            with NamedTemporaryFile(dir=output.parent, delete=False) as destination:
                temporary_output = Path(destination.name)
                destination.write(HEADER.pack(b"CSTR", 1, 0, len(inputs), chunk_count, HEADER.size,
                                              len(page_table) + len(chunk_table), len(meta)))
                destination.write(page_table)
                destination.write(chunk_table)
                destination.write(meta)
                payload.seek(0)
                copyfileobj(payload, destination, length=1024 * 1024)
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
                         lossless=args.lossless, metadata=metadata, archive=archive)
    except (OSError, ValueError, BadZipFile, RuntimeError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        cli.exit(1, f"cstr: {error}\n")
    print(f"{args.output} ({len(inputs)} pages, {count} chunks)")


if __name__ == "__main__":
    main()
