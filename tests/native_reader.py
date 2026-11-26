"""Exercise native CSTR image, index, and HTTP Range reads."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack import HEADER, pack
from read_cstr import Comic, FileBytes, HttpBytes


def check(source):
    comic = Comic(source)
    assert comic.metadata == {"title": "阅读样包", "readingDirection": "rtl"}
    page = comic.pages[0]
    assert (page.width, page.height, len(page.chunks)) == (160, 750, 3)
    with comic.image(page.chunks[1], (40, 60)) as picture:
        assert picture.size == (40, 60) and picture.getpixel((0, 0))[:3] == (0, 0, 255)
    assert len(comic.thumbnails) == 1 and comic.cover is not None
    for record in (comic.thumbnails[0], comic.cover):
        with comic.image(record, (40, 60)) as picture:
            assert picture.size == (40, 60)


def verify(directory):
    page = directory / "page.png"
    with Image.new("RGB", (160, 750), "blue") as image:
        image.save(page)
    book = directory / "漫画.cstr"
    pack(book, [page], strip_height=250, lossless=True, cover=True, thumbnails=True,
         metadata={"title": "阅读样包", "readingDirection": "rtl"})
    check(FileBytes(book))
    data = book.read_bytes()
    book.write_bytes(data[:-1])
    truncated = Comic(FileBytes(book))
    assert len(truncated.pages) == 1 and not truncated.thumbnails
    book.write_bytes(data[:HEADER.size - 1])
    try:
        Comic(FileBytes(book))
    except ValueError:
        pass
    else:
        raise AssertionError("Accepted a truncated header")

    # A larger source exercises subsequent ranges and an ETag change.
    with Image.effect_noise((520, 640), 90).convert("RGB") as image:
        image.save(page)
    pack(book, [page], metadata={"title": "Remote"})
    payload = book.read_bytes()
    requests = []
    tag = ['"original"']

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            first, last = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
            last = min(last, len(payload) - 1)
            chunk = payload[first:last + 1]
            requests.append((first, last, self.headers.get("If-Range")))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {first}-{last}/{len(payload)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("ETag", tag[0])
            self.end_headers()
            self.wfile.write(chunk)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            comic = Comic(HttpBytes(f"http://127.0.0.1:{server.server_port}/comic.cstr"))
            assert comic.metadata == {"title": "Remote"} and requests == [(0, 65535, None)]
            record = comic.pages[0].chunks[0]
            with comic.image(record, (104, 128)) as picture:
                assert picture.size == (104, 128)
            assert requests[1] == (65536, len(payload) - 1, '"original"')
            tag[0] = '"replacement"'
            try:
                comic.image(record, (104, 128))
            except ValueError as error:
                assert "changed" in str(error)
            else:
                raise AssertionError("Combined ranges across different ETags")
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    with TemporaryDirectory() as staging:
        verify(Path(staging))
    print("PASS: native CSTR pages, cover, thumbnails, absent thumbnail tail, and HTTP Range sessions")
