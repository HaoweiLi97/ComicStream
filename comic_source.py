"""Shared comic inputs for the desktop and command-line converters."""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from converter_epub import KINDLE_EXTENSIONS, open_epub, open_kindle
from converter_inputs import COMIC_ARCHIVES, ZIP_EXTENSIONS, open_comic_archive, open_pdf
from pack import IMAGE_EXTENSIONS, archive_pages, is_comic_image, manifest_pages, natural_key, pack


EBOOK_EXTENSIONS = (".epub", *KINDLE_EXTENSIONS)
FILE_EXTENSIONS = (*ZIP_EXTENSIONS, *COMIC_ARCHIVES, *EBOOK_EXTENSIONS, ".pdf", ".json")


@dataclass(frozen=True)
class Source:
    kind: str
    paths: tuple[Path, ...]

    @classmethod
    def from_file(cls, path):
        suffix = path.suffix.lower()
        if suffix in ZIP_EXTENSIONS:
            kind = "zip"
        elif suffix in COMIC_ARCHIVES:
            kind = "archive"
        elif suffix in KINDLE_EXTENSIONS:
            kind = "kindle"
        else:
            kind = {".pdf": "pdf", ".epub": "epub", ".json": "manifest"}[suffix]
        return cls(kind, (path,))

    @classmethod
    def from_path(cls, path):
        if path.is_dir():
            return cls("folder", (path,))
        if path.suffix.lower() in FILE_EXTENSIONS:
            return cls.from_file(path)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            return cls("images", (path,))
        if not path.exists():
            raise FileNotFoundError(f"Input not found: {path}")
        raise ValueError(f"Unsupported input format: {path}")

    def label(self, text):
        first = self.paths[0]
        if len(self.paths) > 1:
            return text("{}（{} 张）", first.parent.name or text("图片"), len(self.paths))
        return first.name or str(first)

    def suggested_output(self):
        first = self.paths[0]
        if self.kind == "folder":
            return first.parent / f"{first.name or 'comic'}.cstr"
        if len(self.paths) > 1:
            return first.parent / f"{first.parent.name or 'comic'}.cstr"
        return first.with_suffix(".cstr")

    @contextmanager
    def open_pages(self, workdir=None):
        first = self.paths[0]
        if self.kind == "zip":
            reader = ZipFile(first)
        elif self.kind == "archive":
            reader = open_comic_archive(first, workdir)
        elif self.kind == "pdf":
            reader = open_pdf(first)
        elif self.kind == "epub":
            reader = open_epub(first)
        elif self.kind == "kindle":
            reader = open_kindle(first, workdir)
        else:
            reader = nullcontext()
        with reader as archive:
            if self.kind == "zip":
                pages = archive_pages(archive)
            elif archive is not None:
                pages = archive.pages
            elif self.kind == "manifest":
                pages = manifest_pages(first)
            elif self.kind == "folder":
                if not first.is_dir():
                    raise ValueError("Image folder not found.")
                pages = [path for path in first.rglob("*") if is_comic_image(path.relative_to(first)) and path.is_file()]
            else:
                pages = self.paths
            if not pages:
                raise ValueError("No pages found. Select a source containing comic pages.")
            yield pages, archive

    def page_names(self):
        with self.open_pages() as (pages, archive):
            if archive is None:
                return [str(page) for page in sorted(pages, key=natural_key)]
            ordered = sorted(pages, key=lambda page: natural_key(page.filename))
            return [page.entry.filename if hasattr(page, "entry") else page.filename for page in ordered]

    def convert(self, output, options, progress=None):
        if output.resolve() == self.paths[0].resolve():
            raise ValueError("Choose an output file different from the source.")
        with self.open_pages(output.parent) as (pages, archive):
            if self.kind in ("epub", "kindle") and archive.metadata:
                options = {**options, "metadata": {**archive.metadata, **(options.get("metadata") or {})}}
            chunks = pack(output, pages, archive=archive, progress=progress, **options)
        return len(pages), chunks
