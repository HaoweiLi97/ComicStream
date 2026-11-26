"""Read comic image pages in EPUB spine order and import Kindle books via calibre."""

from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
import os
from pathlib import Path, PurePosixPath
import posixpath
from shutil import which
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile, ZipInfo

from pack import IMAGE_EXTENSIONS
from portable_runtime import application_directory, portable_data_directory


KINDLE_EXTENSIONS = (".mobi", ".azw", ".azw3")
MAX_MARKUP_BYTES = 8 * 1024 * 1024
IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif", "image/gif", "image/bmp", "image/tiff"}


def resource_path(base, reference):
    url = urlsplit(reference)
    if url.scheme or url.netloc or not url.path:
        return None
    path = unquote(url.path)
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)).lstrip("/")
    return None if resolved in (".", "..") or resolved.startswith("../") else resolved


def read_markup(book, name):
    entry = book.getinfo(name)
    if entry.file_size > MAX_MARKUP_BYTES:
        raise ValueError("An EPUB document exceeds the markup size limit.")
    return book.read(entry)


class ImageReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        tag = tag.rsplit(":", 1)[-1]
        if tag == "img":
            reference = attributes.get("src")
        elif tag == "image":  # Fixed-layout EPUBs often put the page in an SVG wrapper.
            reference = attributes.get("href") or attributes.get("xlink:href")
        else:
            return
        if reference:
            self.references.append(reference)


@dataclass(frozen=True)
class EpubPage:
    filename: str
    entry: ZipInfo

    @property
    def file_size(self):
        return self.entry.file_size


@dataclass(frozen=True)
class EpubComic:
    filename: Path
    book: ZipFile
    pages: list[EpubPage]
    metadata: dict

    def open(self, page):
        return self.book.open(page.entry)


def read_epub(book, filename):
    container = ElementTree.fromstring(read_markup(book, "META-INF/container.xml"))
    rootfile = container.find(".//{*}rootfile")
    package_name = resource_path("", rootfile.get("full-path", "")) if rootfile is not None else None
    if package_name is None:
        raise ValueError("The EPUB does not declare a package document.")
    package = ElementTree.fromstring(read_markup(book, package_name))
    manifest = {}
    media = {}
    for item in package.findall("./{*}manifest/{*}item"):
        path = resource_path(package_name, item.get("href", ""))
        if path is not None:
            manifest[item.get("id")] = path
            media[path] = item.get("media-type", "")

    spine = package.find("./{*}spine")
    if spine is None:
        raise ValueError("The EPUB does not declare a reading order.")
    entries = {entry.filename: entry for entry in book.infolist() if not entry.is_dir()}

    def is_image(path):
        return path is not None and (PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS
                                     or media.get(path) in IMAGE_MEDIA_TYPES)

    pages = []
    for item in spine.findall("./{*}itemref"):
        if item.get("linear") == "no":
            continue
        document = manifest.get(item.get("idref"))
        if document is None:
            continue
        if is_image(document):
            images = [document]
        elif (media[document] in ("application/xhtml+xml", "text/html", "image/svg+xml")
              or PurePosixPath(document).suffix.lower() in (".xhtml", ".html", ".htm", ".svg")):
            parser = ImageReferences()
            raw = read_markup(book, document)
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            parser.feed(raw.decode(encoding))
            parser.close()
            images = [resource_path(document, ref) for ref in parser.references]
        else:
            continue
        for image in images:
            if is_image(image):
                if image not in entries:
                    raise ValueError(f"EPUB page image is missing: {image}")
                pages.append(EpubPage(f"page-{len(pages) + 1}", entries[image]))

    if not pages:
        raise ValueError("No raster comic pages were found in the EPUB reading order.")
    metadata = {}
    title = package.findtext("./{*}metadata/{*}title", "").strip()
    if title:
        metadata["title"] = title
    direction = spine.get("page-progression-direction")
    if direction in ("ltr", "rtl"):
        metadata["readingDirection"] = direction
    return EpubComic(filename, book, pages, metadata)


@contextmanager
def open_epub(path):
    with ZipFile(path) as book:
        yield read_epub(book, path)


def calibre_executable():
    configured = os.environ.get("CSTR_EBOOK_CONVERT")
    if configured:
        executable = Path(configured).expanduser()
        if executable.is_file():
            return str(executable.absolute())
        raise ValueError("CSTR_EBOOK_CONVERT does not point to a file.")
    if os.name == "nt":
        local = application_directory() / "calibre" / "ebook-convert.exe"
        if local.is_file():
            return str(local)
    found = which("ebook-convert")
    if found:
        return found
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base:
                candidate = Path(base) / "Calibre2" / "ebook-convert.exe"
                if candidate.is_file():
                    return str(candidate)
    raise ValueError("MOBI, AZW and AZW3 input requires calibre (ebook-convert).")


@contextmanager
def open_kindle(path, workdir):
    executable = calibre_executable()
    environment = os.environ.copy()
    portable = portable_data_directory()
    if portable is not None:
        config = portable / "calibre"
        config.mkdir(parents=True, exist_ok=True)
        environment["CALIBRE_CONFIG_DIRECTORY"] = str(config)
    with TemporaryDirectory(dir=workdir) as staging, TemporaryFile() as log:
        output = Path(staging) / "book.epub"
        result = subprocess.run([executable, str(path.absolute()), str(output), "--output-profile", "tablet",
                                 "--no-default-epub-cover"],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=environment)
        if result.returncode or not output.is_file():
            log.seek(max(0, log.tell() - 4096))
            detail = log.read().decode("utf-8", errors="replace").strip()
            raise ValueError(detail or "calibre could not open the Kindle book.")
        with ZipFile(output) as book:
            yield read_epub(book, path)
