import { fileSource, FileReplaced, FormatError, LIMITS, openHttp, RangeErrorResponse, RangeSessionError, readComic } from "./cstr.js";
import type { ByteSource, Comic } from "./cstr.js";
import { bitmapCost, decodeWebP } from "./webp.js";

const find = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const opening = find("opening");
const viewport = find("viewport");
const pages = find("pages");
const navigation = find("navigation");
const bookTitle = find("book-title");
const change = find<HTMLButtonElement>("change");
const previous = find<HTMLButtonElement>("previous");
const next = find<HTMLButtonElement>("next");
const pageInput = find<HTMLInputElement>("page");
const total = find("total");
const notice = find("notice");
const openStatus = find("open-status");
const fileInput = find<HTMLInputElement>("file");
const address = find<HTMLInputElement>("address");
const mutable = find<HTMLInputElement>("mutable");
const pick = find<HTMLButtonElement>("pick");
const message = (error: unknown) => error instanceof Error ? error.message : "读取失败";
const GROUP_BYTES = 2 * 1024 * 1024;
const CONCURRENCY = 4;
const MAX_BITMAP_WIDTH = 1920;

interface BlockRender {
  id: number;
  width: number;
  height: number;
  cost: number;
}

interface Job {
  blocks: BlockRender[];
  controller: AbortController;
}

class Reader {
  private readonly slots: HTMLDivElement[] = [];
  private readonly jobs = new Set<Job>();
  private readonly pending = new Set<number>();
  private readonly cached = new Map<number, { canvas: HTMLCanvasElement; cost: number }>();
  private readonly failed = new Set<number>();
  private readonly observer = new ResizeObserver(() => this.queue());
  private readonly meta = new AbortController();
  private memory = 0;
  private frame = 0;
  private disposed = false;
  private stopped = false;
  private direction = 1;
  private scroll = 0;
  private page = 0;

  constructor(private readonly comic: Comic, private readonly name: string) {
    const fragment = document.createDocumentFragment();
    for (const [index, page] of comic.pages.entries()) {
      const column = document.createElement("section");
      column.className = "comic-page";
      column.dataset.page = String(index + 1);
      column.setAttribute("aria-label", "第 " + (index + 1) + " 页");
      for (let i = page.firstChunk; i < page.firstChunk + page.chunkCount; i++) {
        const slot = document.createElement("div");
        slot.className = "strip";
        slot.dataset.chunk = String(i);
        slot.style.aspectRatio = page.width + " / " + comic.chunks[i].height;
        column.append(slot);
        this.slots.push(slot);
      }
      fragment.append(column);
    }
    pages.replaceChildren(fragment);
    pageInput.max = String(comic.pages.length);
    total.textContent = "共 " + comic.pages.length + " 页";
    viewport.addEventListener("scroll", this.queue, { passive: true });
    this.observer.observe(viewport);
    this.showPage(0);
    this.jump(0);
    // Give the first visible block a chance to start before requesting optional metadata.
    requestAnimationFrame(() => {
      if (!this.disposed && !this.stopped) void this.readMetadata();
    });
  }

  private async readMetadata() {
    try {
      const info = await this.comic.metadata(this.meta.signal);
      if (!this.disposed && !this.stopped && typeof info?.title === "string" && info.title.trim()) {
        bookTitle.textContent = info.title;
      }
    } catch (error) {
      if (this.disposed || this.stopped) return;
      if (error instanceof RangeSessionError) this.transportError(error);
      else this.report("元数据读取失败。 " + message(error));
    }
  }

  dispose() {
    this.disposed = true;
    this.meta.abort();
    this.observer.disconnect();
    viewport.removeEventListener("scroll", this.queue);
    cancelAnimationFrame(this.frame);
    for (const job of this.jobs) job.controller.abort();
    for (const id of this.cached.keys()) this.evict(id);
    pages.replaceChildren();
  }

  jump(page: number) {
    if (!Number.isInteger(page) || page < 0 || page >= this.comic.pages.length) return;
    this.updateTail();
    const bounds = viewport.getBoundingClientRect();
    const target = this.slots[this.comic.pages[page].firstChunk].getBoundingClientRect();
    viewport.scrollTop += target.top - bounds.top;
    this.showPage(page);
    this.queue();
  }

  step(delta: number) {
    this.jump(this.page + delta);
  }

  private showPage(page: number) {
    this.page = page;
    if (document.activeElement !== pageInput) pageInput.value = String(page + 1);
    previous.disabled = page === 0;
    next.disabled = page === this.comic.pages.length - 1;
  }

  syncPageInput() {
    pageInput.value = String(this.page + 1);
  }

  private queue = () => {
    if (this.frame || this.disposed || this.stopped) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.refresh();
    });
  };

  private firstAt(y: number) {
    let start = 0;
    let end = this.slots.length;
    while (start < end) {
      const middle = Math.floor((start + end) / 2);
      if (this.slots[middle].getBoundingClientRect().bottom <= y) start = middle + 1;
      else end = middle;
    }
    return Math.min(start, this.slots.length - 1);
  }

  private updateTail() {
    // Give a short final page just enough room to align at the top.
    const lastPage = pages.lastElementChild!.getBoundingClientRect();
    const padding = Math.max(20, Math.ceil(viewport.clientHeight - lastPage.height)) + "px";
    if (pages.style.paddingBottom !== padding) pages.style.paddingBottom = padding;
  }

  private refresh() {
    if (viewport.scrollTop !== this.scroll) this.direction = viewport.scrollTop > this.scroll ? 1 : -1;
    this.scroll = viewport.scrollTop;
    this.updateTail();
    const bounds = viewport.getBoundingClientRect();
    const first = this.firstAt(bounds.top + 1);
    const last = this.firstAt(bounds.bottom - 1);
    const visible = new Set<number>();
    for (let i = first; i <= last; i++) {
      const rect = this.slots[i].getBoundingClientRect();
      if (rect.top < bounds.bottom && rect.bottom > bounds.top) visible.add(i);
    }
    this.showPage(this.comic.chunks[first].page);
    const boundary = this.direction === 1 ? last : first;
    const ahead = [boundary + this.direction, boundary + 2 * this.direction]
      .filter(id => id >= 0 && id < this.slots.length);
    const wanted = new Set([...visible, ...ahead]);
    for (const job of this.jobs) {
      if (!job.blocks.some(block => wanted.has(block.id))) job.controller.abort();
    }
    for (const id of wanted) {
      const entry = this.cached.get(id);
      if (entry) {
        this.cached.delete(id);
        this.cached.set(id, entry);
      }
    }
    this.schedule([...visible], visible);
    if (![...visible].some(id => this.pending.has(id))) this.schedule(ahead, visible);
  }

  private schedule(ids: number[], visible: Set<number>) {
    const groups: BlockRender[][] = [];
    for (const id of [...ids].sort((a, b) => a - b)) {
      if (this.cached.has(id) || this.pending.has(id) || this.failed.has(id)) continue;
      const chunk = this.comic.chunks[id];
      const page = this.comic.pages[chunk.page];
      try {
        if (chunk.codec !== 1) throw new FormatError("此阅读器暂不支持该图像编码");
        if (chunk.size > LIMITS.chunk) throw new FormatError("压缩图像超过读取上限");
        bitmapCost(page.width, chunk.height);
      } catch (error) {
        this.fail(id, message(error), false);
        continue;
      }
      const group = groups.at(-1);
      const first = group && this.comic.chunks[group[0].id];
      const chunkEnd = chunk.start + BigInt(chunk.size);
      const prefix = this.comic.source.cachedPrefix ?? 0n;
      const merge = group && group.at(-1)?.id === id - 1
        && first && chunkEnd - first.start <= BigInt(GROUP_BYTES)
        && !(first.start + BigInt(first.size) <= prefix && chunkEnd > prefix);
      if (!merge && this.jobs.size + groups.length >= CONCURRENCY) break;
      let width = Math.min(page.width, MAX_BITMAP_WIDTH);
      let height = Math.max(1, Math.round(chunk.height * width / page.width));
      let cost = width * height * 4;
      for (const cached of this.cached.keys()) {
        if (this.memory + cost <= LIMITS.decoded) break;
        if (!visible.has(cached) && !this.pending.has(cached)) this.evict(cached);
      }
      if (this.memory + cost > LIMITS.decoded) {
        if (!visible.has(id)) continue;
        const pixels = Math.floor((LIMITS.decoded - this.memory) / 4);
        if (pixels < Math.min(width * height, 4096)) {
          const slot = this.slots[id];
          if (!slot.firstChild) {
            const status = document.createElement("span");
            status.className = "strip-status";
            status.textContent = "图像暂缓显示，滚动后会自动重试";
            slot.append(status);
          }
          continue;
        }
        const scale = Math.sqrt(pixels / (width * height));
        width = Math.max(1, Math.min(pixels, Math.floor(width * scale)));
        height = Math.max(1, Math.min(Math.floor(pixels / width), Math.floor(height * scale)));
        cost = width * height * 4;
      }
      this.memory += cost;
      this.pending.add(id);
      const block = { id, width, height, cost };
      if (merge) group.push(block);
      else groups.push([block]);
    }
    for (const blocks of groups) {
      const job = { blocks, controller: new AbortController() };
      this.jobs.add(job);
      void this.load(job);
    }
  }

  private async load(job: Job) {
    const { signal } = job.controller;
    try {
      const first = this.comic.chunks[job.blocks[0].id];
      const last = this.comic.chunks[job.blocks[job.blocks.length - 1].id];
      const bytes = await this.comic.source.read(first.start, last.start + BigInt(last.size), signal);
      for (const { id, width, height, cost } of job.blocks) {
        signal.throwIfAborted();
        const chunk = this.comic.chunks[id];
        try {
          const offset = Number(chunk.start - first.start);
          const bitmap = await decodeWebP(
            bytes.subarray(offset, offset + chunk.size), this.comic.pages[chunk.page].width, chunk.height, width, height,
          );
          try {
            signal.throwIfAborted();
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            canvas.setAttribute("role", "img");
            canvas.setAttribute("aria-label", "第 " + (chunk.page + 1) + " 页漫画图像");
            const renderer = canvas.getContext("bitmaprenderer");
            if (renderer) renderer.transferFromImageBitmap(bitmap);
            else {
              const context = canvas.getContext("2d");
              if (!context) throw new Error("无法初始化图像画布");
              context.drawImage(bitmap, 0, 0);
            }
            this.cached.set(id, { canvas, cost });
            this.slots[id].replaceChildren(canvas);
          } finally {
            bitmap.close();
          }
        } catch (error) {
          if (signal.aborted) throw error;
          this.fail(id, message(error), false);
        }
      }
    } catch (error) {
      if (!signal.aborted && !this.disposed) {
        if (error instanceof RangeErrorResponse) this.transportError(error);
        else for (const { id } of job.blocks) this.fail(id, message(error), true);
      }
    } finally {
      for (const { id, cost } of job.blocks) {
        this.pending.delete(id);
        if (!this.disposed && !this.cached.has(id)) this.memory -= cost;
      }
      this.jobs.delete(job);
      this.queue();
    }
  }

  private evict(id: number) {
    const entry = this.cached.get(id)!;
    entry.canvas.width = 0;
    entry.canvas.height = 0;
    entry.canvas.remove();
    this.cached.delete(id);
    this.memory -= entry.cost;
  }

  private fail(id: number, explanation: string, retry: boolean) {
    this.failed.add(id);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = retry ? explanation + " · 重试" : explanation;
    button.disabled = !retry;
    if (retry) button.onclick = () => {
      button.remove();
      this.failed.delete(id);
      this.queue();
    };
    this.slots[id].replaceChildren(button);
  }

  private report(text: string) {
    notice.textContent = text;
    notice.hidden = false;
  }

  private transportError(error: RangeErrorResponse) {
    if (this.disposed || this.stopped) return;
    this.stopped = true;
    this.meta.abort();
    for (const job of this.jobs) job.controller.abort();
    if (error instanceof FileReplaced) {
      void open(async () => error.source, this.name, "地址返回了更新后的完整漫画包");
    } else {
      this.report(error.message);
    }
  }
}

let active: Reader | undefined;
let openingRequest: AbortController | undefined;

function chooseSource() {
  openingRequest?.abort();
  active?.dispose();
  active = undefined;
  bookTitle.hidden = change.hidden = viewport.hidden = navigation.hidden = notice.hidden = true;
  opening.hidden = false;
  openStatus.textContent = "";
  pick.focus();
}

async function open(source: (signal: AbortSignal) => Promise<ByteSource>, name: string, update?: string) {
  openingRequest?.abort();
  const request = new AbortController();
  openingRequest = request;
  active?.dispose();
  active = undefined;
  bookTitle.hidden = viewport.hidden = navigation.hidden = notice.hidden = true;
  opening.hidden = false;
  change.hidden = false;
  openStatus.textContent = "正在读取漫画索引…";
  try {
    const comic = await readComic(await source(request.signal), request.signal);
    request.signal.throwIfAborted();
    opening.hidden = true;
    viewport.hidden = navigation.hidden = bookTitle.hidden = false;
    bookTitle.textContent = name;
    viewport.scrollTop = 0;
    active = new Reader(comic, name);
    if (update) {
      notice.textContent = update;
      notice.hidden = false;
    }
    viewport.focus({ preventScroll: true });
  } catch (error) {
    if (!request.signal.aborted) {
      if (error instanceof FileReplaced) {
        void open(async () => error.source, name, "地址返回了更新后的完整漫画包");
      } else {
        change.hidden = true;
        openStatus.textContent = message(error);
      }
    }
  }
}

pick.onclick = () => fileInput.click();
fileInput.onchange = () => {
  const file = fileInput.files?.[0];
  if (file) void open(async () => fileSource(file), file.name);
  fileInput.value = "";
};
find<HTMLFormElement>("remote").onsubmit = event => {
  event.preventDefault();
  let url: URL;
  try {
    url = new URL(address.value.trim(), location.href);
  } catch {
    openStatus.textContent = "请输入有效的漫画包链接";
    return;
  }
  const name = url.pathname.split("/").pop() || url.hostname;
  void open(signal => openHttp(url, { signal, mutable: mutable.checked }), name);
};
change.onclick = chooseSource;
previous.onclick = () => active?.step(-1);
next.onclick = () => active?.step(1);
pageInput.onchange = () => active?.jump(pageInput.valueAsNumber - 1);
pageInput.onblur = () => active?.syncPageInput();
pageInput.onkeydown = event => {
  if (event.key === "Enter") {
    active?.jump(pageInput.valueAsNumber - 1);
    viewport.focus({ preventScroll: true });
  }
};
viewport.onkeydown = event => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    active?.step(event.key === "ArrowLeft" ? -1 : 1);
  }
};
