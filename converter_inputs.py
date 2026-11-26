"""Turn non-ZIP comic sources into pages for the CSTR packer."""

from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite, sqrt
import os
from pathlib import Path, PurePosixPath
from shutil import copyfileobj, which
import subprocess
import tarfile
from tempfile import TemporaryDirectory, TemporaryFile

from pack import is_comic_image


ZIP_EXTENSIONS = (".cbz", ".zip")
COMIC_ARCHIVES = (".cbr", ".rar", ".cb7", ".7z", ".cbt", ".tar")


@dataclass(frozen=True)
class StagedPage:
    filename: str
    file_size: int
    path: Path


@dataclass(frozen=True)
class StagedComic:
    filename: Path
    pages: list[StagedPage]

    @staticmethod
    def open(page):
        return page.path.open("rb")


@contextmanager
def open_comic_archive(path, workdir):
    """Read a libarchive stream without extracting user-supplied paths to disk."""
    if os.name == "nt":
        system_tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        executable = str(system_tar) if system_tar.is_file() else None
    else:
        executable = which("bsdtar")
    if executable is None:
        raise RuntimeError("RAR, 7Z and TAR input requires bsdtar (Windows system tar.exe).")

    with TemporaryDirectory(dir=workdir) as staging, TemporaryFile() as errors, path.open("rb") as archive_file:
        pages = []
        # Pass the open handle so Unicode paths do not depend on the helper's code page.
        command = [executable, "-cf", "-", "--format=pax", "@-"]
        with subprocess.Popen(command, stdin=archive_file, stdout=subprocess.PIPE, stderr=errors,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            try:
                try:
                    with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                        for entry in archive:
                            member = PurePosixPath(entry.name.replace("\\", "/"))
                            if not entry.isfile() or not is_comic_image(member):
                                continue
                            local = Path(staging) / str(len(pages))
                            with archive.extractfile(entry) as source, local.open("wb") as output:
                                copyfileobj(source, output, length=1024 * 1024)
                            pages.append(StagedPage(member.as_posix(), entry.size, local))
                except tarfile.TarError as error:
                    # The helper can emit a partial stream when the source archive is damaged.
                    stream_error = error
                else:
                    stream_error = None
                while process.stdout.read(65536):
                    pass
                if process.wait() != 0 or stream_error is not None:
                    errors.seek(0)
                    message = errors.read(4096).decode("utf-8", errors="replace").strip()
                    raise ValueError(message or "Could not read the comic archive.") from stream_error
            finally:
                if process.poll() is None:
                    process.kill()
        yield StagedComic(path, pages)


@dataclass(frozen=True)
class PdfPage:
    filename: str
    index: int


@dataclass(frozen=True)
class PdfComic:
    filename: Path
    document: object

    @property
    def pages(self):
        return [PdfPage(f"{index + 1}.png", index) for index in range(len(self.document))]

    @contextmanager
    def open_image(self, entry):
        page = self.document[entry.index]
        try:
            width, height = page.get_size()
            if not all(isfinite(side) and side > 0 for side in (width, height)):
                raise ValueError(f"Invalid PDF page dimensions on page {entry.index + 1}.")
            # Render at 144 dpi, reducing scale for unusually large pages.
            scale = min(2, 8192 / max(width, height), sqrt(15_000_000) / sqrt(width) / sqrt(height))
            bitmap = page.render(scale=scale, rev_byteorder=True, prefer_bgrx=True)
            try:
                with bitmap.to_pil() as image, image.convert("RGB") as rgb:
                    yield rgb
            finally:
                bitmap.close()
        finally:
            page.close()


@contextmanager
def open_pdf(path):
    from pypdfium2 import PdfDocument

    with PdfDocument(path) as document:
        yield PdfComic(path, document)
