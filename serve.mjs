import { open } from "node:fs/promises";
import { createServer } from "node:http";
import { pipeline } from "node:stream/promises";

const files = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/reader.css", ["reader.css", "text/css; charset=utf-8"]],
  ...["reader", "cstr", "webp"].map(name => [
    "/dist/" + name + ".js", ["dist/" + name + ".js", "text/javascript; charset=utf-8"],
  ]),
]);

async function serve(request, response) {
  if (!["GET", "HEAD"].includes(request.method)) {
    response.writeHead(405, { Allow: "GET, HEAD" }).end();
    return;
  }
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const comic = /^\/library\/([^/\\:\x00-\x1f]+\.cstr)$/i.exec(pathname);
  const route = files.get(pathname) ?? (comic && ["library/" + comic[1], "application/octet-stream"]);
  if (!route) {
    response.writeHead(404).end();
    return;
  }
  const file = await open(new URL(route[0].split("/").map(encodeURIComponent).join("/"), import.meta.url), "r");
  try {
    const stat = await file.stat();
    const { size } = stat;
    if (!stat.isFile()) {
      response.writeHead(404).end();
      return;
    }
    const headers = { "Content-Type": route[1], "Cache-Control": "no-store" };
    let start = 0;
    let end = size - 1;
    let status = 200;
    if (comic) {
      headers["Accept-Ranges"] = "bytes";
      if (request.headers.range && !request.headers["if-range"]) {
        const range = /^bytes=(\d{1,20})-(\d{0,20})$/.exec(request.headers.range);
        if (range) {
          start = Number(range[1]);
          end = range[2] ? Math.min(end, Number(range[2])) : end;
          if (start > end) {
            response.writeHead(416, { ...headers, "Content-Range": "bytes */" + size }).end();
            return;
          }
          status = 206;
          headers["Content-Range"] = "bytes " + start + "-" + end + "/" + size;
        }
      }
    }
    headers["Content-Length"] = end - start + 1;
    response.writeHead(status, headers);
    if (request.method === "HEAD" || size === 0) response.end();
    else await pipeline(file.createReadStream({ start, end, autoClose: false }), response);
  } finally {
    await file.close();
  }
}

const host = "127.0.0.1";
const port = Number(process.env.CSTR_PORT ?? 4173);
createServer((request, response) => {
  serve(request, response).catch(error => {
    if (response.destroyed) return;
    if (!response.headersSent) {
      response.writeHead(error.code === "ENOENT" ? 404 : error instanceof URIError ? 400 : 500).end();
    } else {
      response.destroy();
    }
  });
}).listen(port, host, () => console.log("ComicStream · http://" + host + ":" + port));
