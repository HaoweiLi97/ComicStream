"""Exercise the desktop batch with actual files and a temporary language preference."""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import tkinter as tk
from unittest.mock import patch
from zipfile import ZipFile
from zlib import crc32

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converter import Converter, Source
from pack import FLAG_COVER, FLAG_THUMBNAILS, HEADER, PAGE


def rar_block(kind, flags, payload):
    """A RAR 4 header with the low 16 bits of its CRC32."""
    body = struct.pack("<BHH", kind, flags, len(payload) + 7) + payload
    return struct.pack("<H", crc32(body) & 0xFFFF) + body


def write_rar(path, files):
    with path.open("wb") as target:
        target.write(b"Rar!\x1a\x07\0" + rar_block(0x73, 0, b"\0" * 6))
        for file in files:
            data, name = file.read_bytes(), file.name.encode("ascii")
            header = struct.pack("<IIBIIBBHI", len(data), len(data), 2, crc32(data), 0, 20, 0x30, len(name), 0x20)
            target.write(rar_block(0x74, 0x8000, header + name))
            target.write(data)
        target.write(rar_block(0x7B, 0, b""))


def write_epub(path, first, second):
    with ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", '''<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''')
        book.writestr("OPS/book.opf", '''<package version="3.0" unique-identifier="id" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">test-epub</dc:identifier><dc:title>EPUB sample</dc:title><dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="first" href="pages/2.xhtml" media-type="application/xhtml+xml"/>
    <item id="second" href="pages/10.xhtml" media-type="application/xhtml+xml"/>
    <item id="image1" href="images/2.png" media-type="image/png"/>
    <item id="image2" href="images/10.png" media-type="image/png"/>
  </manifest>
  <spine page-progression-direction="rtl"><itemref idref="second"/><itemref idref="first"/></spine>
</package>''')
        book.writestr("OPS/pages/2.xhtml", '''<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Page 2</title></head><body><img src="../images/%32.png" alt=""/></body></html>''')
        book.writestr("OPS/pages/10.xhtml", '''<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Page 1</title></head><body><svg xmlns="http://www.w3.org/2000/svg"
 xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 140 170">
 <image width="140" height="170" xlink:href="../images/10.png"/></svg></body></html>''')
        book.write(first, "OPS/images/2.png")
        book.write(second, "OPS/images/10.png")


def verify(base):
    first, second = base / "2.png", base / "10.png"
    with Image.new("RGB", (110, 160), "red") as page, Image.new("RGB", (140, 170), "blue") as other:
        page.save(first)
        other.save(second)
        page.save(base / "漫画.pdf", save_all=True, append_images=[other], resolution=72)
    write_rar(base / "漫画.cbr", [second, first])
    epub = base / "漫画.epub"
    write_epub(epub, first, second)
    seven_zip = base / "temporary.7z"
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    subprocess.run([str(executable), "-caf", str(seven_zip), "--format=7zip", "-C", str(base),
                    second.name, first.name], check=True)
    seven_zip.rename(base / "漫画.7z")
    (base / "无效.pdf").write_bytes(b"broken pdf")
    with tarfile.open(base / "漫画.cbt", "w") as archive:
        archive.add(second, arcname="nested/10.png")
        archive.add(first, arcname="nested/2.png")
        archive.add(first, arcname="__MACOSX/0.png")

    root = tk.Tk()
    ui = Converter(root)
    root.update()
    assert root.title() == "CSTR 转换器"
    ui.add_tasks(Source.from_file(base / filename) for filename in (
        "漫画.cbr", "漫画.7z", "无效.pdf", "漫画.cbt", "漫画.pdf", epub.name,
    ))
    ui.direction.current(2)
    ui.encoding.current(2)
    ui.language_picker.set("English")
    ui.change_language()
    assert root.title() == "CSTR Converter"
    assert ui.encoding.get() == "Lossless WebP" and ui.direction.get() == "Right to left (RTL)"
    assert ui.convert.cget("text") == "Convert" and ui.summary.get().startswith("Total 6")

    ui.convert.invoke()
    deadline = monotonic() + 20
    while ui.running:
        assert monotonic() < deadline, ui.summary.get()
        root.update()
        sleep(.01)
    assert [task.state for task in ui.tasks.values()] == ["done", "done", "failed", "done", "done", "done"], ui.tasks
    for task in ui.tasks.values():
        if task.state == "failed":
            continue
        data = task.output.read_bytes()
        magic, version, flags, count, _, index, index_size, meta_size = HEADER.unpack_from(data)
        assert (magic, version, flags, count) == (b"CSTR", 1, FLAG_COVER | FLAG_THUMBNAILS, 2)
        size = {"pdf": (220, 320), "epub": (140, 170)}.get(task.source.kind, (110, 160))
        assert PAGE.unpack_from(data, index)[:2] == size
        if task.source.kind == "epub":
            start = index + index_size
            assert json.loads(data[start:start + meta_size]) == {"title": "EPUB sample", "readingDirection": "rtl"}

    ui.table.selection_set(next(iter(ui.tasks)))
    root.update()
    assert ui.detail_text.get().startswith("Converted")
    ui.language_picker.set("简体中文")
    ui.change_language()
    assert ui.detail_text.get().startswith("转换完成")
    ui.language_picker.set("English")
    ui.change_language()
    ui.close()
    root = tk.Tk()
    reopened = Converter(root)
    assert root.title() == "CSTR Converter"
    reopened.close()


if __name__ == "__main__":
    with TemporaryDirectory() as directory, patch.dict(os.environ, LOCALAPPDATA=str(Path(directory) / "settings")):
        verify(Path(directory))
    print("PASS: desktop archive, PDF, EPUB batch, Unicode paths, failure isolation, and English preference.")
