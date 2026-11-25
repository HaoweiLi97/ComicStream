"""Produce a deliberately misordered CBZ and verify its converted pixels."""

from io import BytesIO
import json
from pathlib import Path
from random import Random
from struct import unpack_from
import subprocess
import sys
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
print("PASS: CBZ, image arguments and manifest sort 1, 2, 10. Verified actual page pixels and short final strips.")
