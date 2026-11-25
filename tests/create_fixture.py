"""Produce a deliberately misordered CBZ and verify its converted pixels."""

from io import BytesIO
import json
from pathlib import Path
from random import Random
from runpy import run_path
from struct import unpack_from
import subprocess
import sys
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "library"
CACHE = ROOT / ".cache" / "test"
LIBRARY.mkdir(exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
archive = CACHE / "order-fixture.cbz"
output = LIBRARY / "order-fixture.cstr"
metadata = CACHE / "metadata.json"
metadata.write_text(json.dumps({"title": "排序测试"}, ensure_ascii=False), encoding="utf8")
colors = {1: (240, 40, 40), 2: (40, 220, 60), 10: (40, 90, 240)}
with ZipFile(archive, "w") as bundle:
    bundle.writestr("ComicInfo.xml", "<ComicInfo/>")
    bundle.writestr("__MACOSX/0.png", b"ignored")
    for number in (10, 2, 1):
        pixels = Random(number).randbytes(480 * 1080 * 3)
        with Image.frombytes("RGB", (480, 1080), pixels) as image, BytesIO() as contents:
            image.paste(colors[number], (0, 0, 48, 48))
            image.save(contents, "PNG")
            data = contents.getvalue()
            bundle.writestr(f"chapter 1/{number}.png", data)
            (CACHE / f"{number}.png").write_bytes(data)

cli = [sys.executable, str(ROOT / "pack.py")]
preview = subprocess.check_output(cli + ["--cbz", str(archive), "--list-pages"], text=True)
assert preview.splitlines() == ["1\tchapter 1/1.png", "2\tchapter 1/2.png", "3\tchapter 1/10.png"]
scattered = [str(CACHE / f"{number}.png") for number in (10, 2, 1)]
preview = subprocess.check_output(cli + ["--list-pages", *scattered], text=True)
assert [Path(line.split("\t")[1]).stem for line in preview.splitlines()] == ["1", "2", "10"]
manifest = CACHE / "pages.json"
manifest.write_text(json.dumps(["10.png", "1.png", "2.png"]), encoding="utf8")
preview = subprocess.check_output(cli + ["--manifest", str(manifest), "--list-pages"], text=True)
assert [Path(line.split("\t")[1]).stem for line in preview.splitlines()] == ["1", "2", "10"]
subprocess.run(cli + ["--cbz", str(archive), "--metadata", str(metadata),
                     "--strip-height", "400", "--lossless", "-o", str(output)], check=True)
data = output.read_bytes()
magic, version, flags, page_count, chunk_count, table, index_size, meta_size = unpack_from("<4sHHIIQII", data)
assert (magic, version, flags, page_count, chunk_count, table) == (b"CSTR", 1, 0, 3, 9, 32)
start = table + index_size + meta_size
for page, number in enumerate((1, 2, 10)):
    assert unpack_from("<IIII", data, table + page * 16) == (480, 1080, page * 3, 3)
    for strip, expected_height in enumerate((400, 400, 280)):
        height, codec, offset, size = unpack_from("<IB3xQI", data, table + 48 + (page * 3 + strip) * 20)
        assert (height, codec) == (expected_height, 1)
        with Image.open(BytesIO(data[start + offset:start + offset + size])) as image:
            assert image.size == (480, expected_height)
            if strip == 0:
                assert image.getpixel((0, 0)) == colors[number]

whole = CACHE / "whole-page.cstr"
subprocess.run(cli + ["-o", str(whole), *scattered], check=True)
header = whole.read_bytes()[:32]
assert unpack_from("<III", header, 8) == (3, 3, 32)
assert unpack_from("<I", header, 28) == (0,)

previews = LIBRARY / "preview-fixture.cstr"
small = CACHE / "small.png"
portrait = CACHE / "portrait.png"
with Image.new("RGB", (80, 120), "blue") as image:
    image.save(small)
with Image.new("RGB", (320, 480), "green") as image:
    image.save(portrait)
subprocess.run(cli + ["--thumbnails", "--reading-direction", "rtl", "--metadata", str(metadata),
                     "-o", str(previews), *scattered, str(portrait), str(small), *scattered], check=True)
data = previews.read_bytes()
_, version, flags, page_count, chunk_count, table, index_size, meta_size = unpack_from("<4sHHIIQII", data)
assert (version, flags, page_count, chunk_count) == (1, 1, 8, 8)
chunk_table = table + page_count * 16
thumbnail_table = chunk_table + chunk_count * 20
metadata_start = table + index_size
assert json.loads(data[metadata_start:metadata_start + meta_size]) == {
    "title": "排序测试", "readingDirection": "rtl"
}
last_offset, last_size = unpack_from("<QI", data, thumbnail_table - 12)
preview_start = metadata_start + meta_size + last_offset + last_size
expected_offset = 0
for page in range(page_count):
    width, height, size, offset = unpack_from("<HHIQ", data, thumbnail_table + page * 16)
    expected_size = ((160, 320) if page < 6 else (160, 240) if page == 6 else (80, 120))
    assert (width, height) == expected_size
    assert offset == expected_offset
    with Image.open(BytesIO(data[preview_start + offset:preview_start + offset + size])) as image:
        assert image.format == "WEBP" and image.size == expected_size
        image.load()
    expected_offset += size
assert preview_start + expected_offset == len(data)

pack = run_path(str(ROOT / "pack.py"))["pack"]
boundary_output = CACHE / "reader-boundary.cstr"
metadata_limit = 256 * 1024
metadata_overhead = len(b'{"notes":"","readingDirection":"rtl"}')
notes = "x" * (metadata_limit - metadata_overhead)
with patch.dict(pack.__globals__, {"READER_INDEX_BYTES": 52}):
    pack(boundary_output, [small], thumbnails=True, metadata={"notes": notes}, reading_direction="rtl")
    boundary_data = boundary_output.read_bytes()
    assert unpack_from("<II", boundary_data, 24) == (52, metadata_limit)
    with TestCase().assertRaisesRegex(ValueError, "Metadata.*256 KiB"):
        pack(boundary_output, [small], thumbnails=True, metadata={"notes": notes + "x"}, reading_direction="rtl")
    with TestCase().assertRaisesRegex(ValueError, "Index.*reading"):
        missing = CACHE / "missing-page.png"
        pack(boundary_output, [missing, missing], thumbnails=True)
    with TestCase().assertRaisesRegex(ValueError, "Index.*reading"):
        pack(boundary_output, [small], thumbnails=True, strip_height=60)
assert boundary_output.read_bytes() == boundary_data
print("PASS: page order, strip pixels, metadata, v1 thumbnails, and packer read limits.")
