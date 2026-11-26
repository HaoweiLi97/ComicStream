"""Write the Windows release ZIP from built executables and calibre."""

import argparse
from importlib.metadata import distribution
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = "ComicStream-Portable"
EXECUTABLES = ("CSTR-Converter.exe", "CSTR-Reader.exe", "CSTR.exe")
SCREENSHOTS = ("cstr-converter.en.jpg", "cstr-converter.zh-CN.jpg")
CALIBRE_KEEP_ROOT = {"ebook-convert.exe", "calibre-parallel.exe", "LICENSE"}
# Keep Qt's image plugins, fonts, and locale data for Kindle-to-EPUB conversion.
CALIBRE_UNUSED_TREES = (
    "app/resources/langtool/", "app/resources/content-server/", "app/resources/editor-help/",
    "app/resources/quick_start/", "app/share/espeak-ng-data/", "app/translations/qtwebengine_locales/",
    "app/plugins/texttospeech/", "app/resources/qtwebengine_", "app/resources/viewer.",
    "app/resources/editor.", "app/resources/stylelint", "app/bin/Qt6WebEngine", "app/bin/pyqt6.QtWebEngine",
)
CALIBRE_UNUSED_FILES = {
    "app/bin/QtWebEngineProcess.exe", "app/resources/icudtl.dat", "app/resources/v8_context_snapshot.bin",
    "app/bin/DirectML.dll", "app/bin/onnxruntime.dll", "app/bin/seq2seq.pyd", "app/bin/espeak-ng.dll",
}
QUICK_START = """ComicStream Portable · Windows x64

Extract the complete directory to a writable local folder on Windows 10
(version 1809 or later) or Windows 11. Keep the calibre folder beside the EXEs.
将整个目录解压到可写的本地文件夹。保留 EXE 旁边的 calibre 文件夹。

CSTR-Converter.exe   Batch converter / 批量转换器
CSTR-Reader.exe      CSTR reference reader / CSTR 阅读器
CSTR.exe             Command-line converter / 命令行转换器

See docs for screenshots of the converter in both languages.
中英文转换器截图位于 docs 文件夹。

From PowerShell in this folder / 在此文件夹中打开 PowerShell：
  .\\CSTR.exe --help
  .\\CSTR.exe -i "book.mobi" -i "book.cbz" -d "output"
  .\\CSTR.exe -i "book.epub" --list-pages

Language and runtime data are saved under Data.
语言设置和运行时数据保存在 Data 文件夹下。
"""


def calibre_files(calibre, version):
    for path in sorted(calibre.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(calibre)
        name = relative.as_posix()
        if version == "9.19.0":
            if len(relative.parts) == 1 and name not in CALIBRE_KEEP_ROOT:
                continue
            if name in CALIBRE_UNUSED_FILES or name.startswith(CALIBRE_UNUSED_TREES):
                continue
        yield path, name


def write_licenses(archive, project):
    archive.write(project / "LICENSE", f"{ROOT}/LICENSE")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    archive.write(python_license, f"{ROOT}/licenses/CPython-LICENSE.txt")
    for name in ("Pillow", "pypdfium2"):
        installed = distribution(name)
        for file in installed.files or ():
            if (len(file.parts) > 1 and file.parts[0].endswith(".dist-info")
                    and (file.parts[1] == "licenses" or file.name.lower().startswith("license"))):
                relative = Path(*file.parts[1:]).as_posix()
                archive.write(file.locate(), f"{ROOT}/licenses/{name}/{relative}")


def write_package(executables, calibre, calibre_version, output):
    notices = f"""ComicStream is released under the MIT License (LICENSE).

Third-party runtimes

CPython (including Tcl/Tk), Pillow, and pypdfium2/PDFium notices are in
the licenses directory.

calibre {calibre_version} is distributed under GNU GPL version 3. The
original license is in calibre/LICENSE. Corresponding calibre source:
https://download.calibre-ebook.com/{calibre_version}/calibre-{calibre_version}.tar.xz

The calibre 9.19.0 bundle is limited to the ebook-convert runtime used
for Kindle-to-EPUB conversion. Original binaries are unmodified.
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, prefix=output.stem, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for name in EXECUTABLES:
                archive.write(executables / name, f"{ROOT}/{name}")
            project = Path(__file__).resolve().parent
            images = project / "docs" / "images"
            for name in SCREENSHOTS:
                archive.write(images / name, f"{ROOT}/docs/{name}")
            for path, name in calibre_files(calibre, calibre_version):
                archive.write(path, f"{ROOT}/calibre/{name}")
            write_licenses(archive, project)
            archive.writestr(f"{ROOT}/PORTABLE", b"")
            archive.writestr(f"{ROOT}/Readme.txt", QUICK_START.encode("utf-8-sig"))
            archive.writestr(f"{ROOT}/THIRD-PARTY.txt", notices)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{output.resolve()} ({output.stat().st_size / 2**20:.1f} MiB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("executables", "calibre"):
        parser.add_argument(name, type=Path)
    parser.add_argument("calibre_version")
    parser.add_argument("output", type=Path)
    write_package(**vars(parser.parse_args()))
