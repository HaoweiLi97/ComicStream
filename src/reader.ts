import { fileSource, FileReplaced, FormatError, LIMITS, openHttp, RangeErrorResponse, RangeSessionError, readComic, readingDirection } from "./cstr.js";
import type { ByteSource, Comic, ReadingDirection } from "./cstr.js";
import { bitmapCost, decodeWebP } from "./webp.js";
import { decodeImage } from "./images.js";

const find = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
let english = document.documentElement.lang === "en";
const ui = (chinese: string, translated: string) => english ? translated : chinese;
const translations = new WeakMap<HTMLElement, () => void>();

function localize(element: HTMLElement, render: () => string, attribute?: "aria-label") {
  const apply = () => {
    if (attribute) element.setAttribute(attribute, render());
    else element.textContent = render();
  };
  element.dataset.localized = "";
  translations.set(element, apply);
  apply();
}

for (const element of document.querySelectorAll<HTMLElement>("[data-en]")) {
  const chinese = element.textContent!;
  localize(element, () => ui(chinese, element.dataset.en!));
}
for (const element of document.querySelectorAll<HTMLElement>("[data-en-label]")) {
  const chinese = element.getAttribute("aria-label")!;
  localize(element, () => ui(chinese, element.dataset.enLabel!), "aria-label");
}
const language = find<HTMLSelectElement>("reader-language");
language.value = english ? "en" : "zh";
language.onchange = () => {
  english = language.value === "en";
  document.documentElement.lang = english ? "en" : "zh-CN";
  for (const element of document.querySelectorAll<HTMLElement>("[data-localized]")) translations.get(element)?.();
};
const opening = find("opening");
const viewport = find("viewport");
const pages = find("pages");
const navigation = find("navigation");
const pageControls = find("page-controls");
const previews = find("previews");
const previewList = find("preview-list");
const previewPrevious = find<HTMLButtonElement>("preview-previous");
const previewNext = find<HTMLButtonElement>("preview-next");
const previewRange = find("preview-range");
const previewToggle = find<HTMLButtonElement>("preview-toggle");
const bookTitle = find("book-title");
const coverToggle = find<HTMLButtonElement>("cover-toggle");
const coverDialog = find<HTMLDialogElement>("cover-dialog");
const coverStatus = find("cover-status");
const coverImage = document.createElement("img");
coverImage.id = "cover-image";
coverImage.alt = "";
coverImage.hidden = true;
coverDialog.append(coverImage);
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
const dropArea = find("drop-area");
function message(error: unknown) {
  if (!(error instanceof Error)) return ui("读取失败", "Could not read the comic.");
  if (!english || !/\p{Script=Han}/u.test(error.message)) return error.message;
  if (error.message === "文件不是 CSTR 漫画包") return "This file is not a CSTR comic.";
  if (error.message === "不支持此 CSTR 版本或标志位") return "This CSTR version or feature is unsupported.";
  if (error instanceof RangeErrorResponse) return "Could not read the comic URL. Check the address and its Range and CORS configuration.";
  if (error instanceof FormatError) return "The file contains invalid or unsupported comic data.";
  return "Could not read the comic.";
}
const pageLabel = (page: number) => ui(`第 ${page} 页`, `Page ${page}`);
let coverRequest: AbortController | undefined;
let coverUrl: string | undefined;

function clearCover() {
  coverRequest?.abort();
  coverRequest = undefined;
  coverImage.hidden = true;
  coverImage.removeAttribute("src");
  if (coverUrl) URL.revokeObjectURL(coverUrl);
  coverUrl = undefined;
}

async function showCover(comic: Comic) {
  clearCover();
  const request = new AbortController();
  coverRequest = request;
  localize(coverStatus, () => ui("正在读取封面…", "Loading cover…"));
  coverDialog.showModal();
  try {
    const cover = await comic.readCover(request.signal);
    if (!cover || request.signal.aborted) return;
    coverUrl = URL.createObjectURL(new Blob([cover.bytes], { type: "image/webp" }));
    coverImage.src = coverUrl;
    await coverImage.decode();
    if (request.signal.aborted) return;
    coverImage.hidden = false;
    localize(coverStatus, () => "");
  } catch (error) {
    if (!request.signal.aborted) {
      clearCover();
      localize(coverStatus, () => ui("无法读取封面。", "The cover could not be opened."));
    }
  }
}

coverDialog.onclose = clearCover;
const GROUP_BYTES = 2 * 1024 * 1024;
const CONCURRENCY = 4;
const MAX_BITMAP_WIDTH = 1920;
const PREVIEW_GROUP = 6;

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
  private readingDirection: ReadingDirection = "ltr";
  private previewRequest?: AbortController;
  private previewStart = 0;

  constructor(private readonly comic: Comic, private readonly name: string) {
    const fragment = document.createDocumentFragment();
    for (const [index, page] of comic.pages.entries()) {
      const column = document.createElement("section");
      column.className = "comic-page";
      column.dataset.page = String(index + 1);
      localize(column, () => pageLabel(index + 1), "aria-label");
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
    const pageCount = comic.pages.length;
    localize(total, () => ui(`共 ${pageCount} 页`, `of ${pageCount}`));
    previewToggle.hidden = comic.thumbnails.length === 0;
    coverToggle.hidden = !comic.hasCover;
    coverToggle.onclick = () => void showCover(comic);
    this.setReadingDirection("ltr");
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
      if (this.disposed || this.stopped) return;
      this.setReadingDirection(readingDirection(info?.readingDirection) ?? "ltr");
      if (typeof info?.title === "string" && info.title.trim()) {
        bookTitle.textContent = info.title;
      }
    } catch (error) {
      if (this.disposed || this.stopped) return;
      if (error instanceof RangeSessionError) this.transportError(error);
      else this.report(() => ui("元数据读取失败。 ", "Could not read metadata. ") + message(error));
    }
  }

  dispose() {
    this.disposed = true;
    if (coverDialog.open) coverDialog.close();
    clearCover();
    coverToggle.hidden = true;
    coverToggle.onclick = null;
    this.meta.abort();
    this.closePreviews();
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

  stepHorizontally(delta: number) {
    this.step(this.readingDirection === "rtl" ? -delta : delta);
  }

  private setReadingDirection(direction: ReadingDirection) {
    this.readingDirection = direction;
    pageControls.dataset.direction = direction;
    pageControls.dir = previewList.dir = direction === "rtl" ? "rtl" : "ltr";
  }

  togglePreviews() {
    if (!previews.hidden) {
      this.closePreviews();
      return;
    }
    if (this.stopped || !this.comic.thumbnails.length) return;
    previews.hidden = false;
    previewToggle.setAttribute("aria-expanded", "true");
    this.showPreviews(this.page - this.page % PREVIEW_GROUP, this.page);
  }

  shiftPreviews(delta: number) {
    this.showPreviews(this.previewStart + delta * PREVIEW_GROUP);
  }

  private closePreviews() {
    this.previewRequest?.abort();
    this.previewRequest = undefined;
    previews.hidden = true;
    previewToggle.setAttribute("aria-expanded", "false");
    previewList.replaceChildren();
  }

  private showPreviews(start: number, focus = start) {
    const count = this.comic.thumbnails.length;
    if (previews.hidden || start < 0 || start >= count) return;
    this.previewRequest?.abort();
    const request = new AbortController();
    this.previewRequest = request;
    this.previewStart = start;
    const end = Math.min(start + PREVIEW_GROUP, count);
    previewRange.textContent = start + 1 + "–" + end + " / " + count;
    const frames: HTMLSpanElement[] = [];
    const buttons: HTMLButtonElement[] = [];
    for (let page = start; page < end; page++) {
      const button = document.createElement("button");
      const frame = document.createElement("span");
      const label = document.createElement("span");
      button.type = "button";
      button.dataset.previewPage = String(page);
      localize(button, () => pageLabel(page + 1), "aria-label");
      localize(label, () => pageLabel(page + 1));
      frame.className = "preview-image";
      localize(frame, () => ui("加载中…", "Loading…"));
      frame.setAttribute("aria-hidden", "true");
      button.append(frame, label);
      button.onclick = () => {
        this.closePreviews();
        this.jump(page);
        viewport.focus({ preventScroll: true });
      };
      frames.push(frame);
      buttons.push(button);
    }
    previewList.replaceChildren(...buttons);
    this.markPreview();
    previewPrevious.disabled = start === 0;
    previewNext.disabled = end === count;
    buttons[focus - start].focus({ preventScroll: true });
    void this.loadPreviews(start, end, frames, request.signal);
  }

  private async loadPreviews(start: number, end: number, frames: HTMLSpanElement[], signal: AbortSignal) {
    try {
      const images = await this.comic.readThumbnails(start, end, signal);
      for (const [index, bytes] of images.entries()) {
        signal.throwIfAborted();
        const frame = frames[index];
        const { width, height } = this.comic.thumbnails[start + index];
        try {
          const bitmap = await decodeWebP(bytes, width, height);
          try {
            signal.throwIfAborted();
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
            translations.delete(frame);
            delete frame.dataset.localized;
            frame.replaceChildren(canvas);
          } finally {
            bitmap.close();
          }
        } catch {
          if (!signal.aborted) localize(frame, () => ui("预览不可用", "Preview unavailable"));
        }
      }
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof RangeSessionError) this.transportError(error);
      else for (const frame of frames) localize(frame, () => ui("预览不可用", "Preview unavailable"));
    }
  }

  private markPreview() {
    previewList.querySelector('[aria-current="page"]')?.removeAttribute("aria-current");
    previewList.querySelector(`[data-preview-page="${this.page}"]`)?.setAttribute("aria-current", "page");
  }

  private showPage(page: number) {
    const changed = this.page !== page;
    this.page = page;
    if (changed && !previews.hidden) this.markPreview();
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
        if (chunk.size > LIMITS.chunk) throw new FormatError(ui("压缩图像超过读取上限", "Compressed image exceeds the read limit"));
        bitmapCost(page.width, chunk.height);
      } catch (error) {
        this.fail(id, error, false);
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
            localize(status, () => ui("图像暂缓显示，滚动后会自动重试", "Image deferred. Scroll to retry."));
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
          const bitmap = await decodeImage(
            bytes.subarray(offset, offset + chunk.size), chunk.codec, this.comic.pages[chunk.page].width,
            chunk.height, width, height,
          );
          try {
            signal.throwIfAborted();
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            canvas.setAttribute("role", "img");
            localize(canvas, () => pageLabel(chunk.page + 1), "aria-label");
            const renderer = canvas.getContext("bitmaprenderer");
            if (renderer) renderer.transferFromImageBitmap(bitmap);
            else {
              const context = canvas.getContext("2d");
              if (!context) throw new Error(ui("无法初始化图像画布", "Could not initialize the canvas"));
              context.drawImage(bitmap, 0, 0);
            }
            this.cached.set(id, { canvas, cost });
            this.slots[id].replaceChildren(canvas);
          } finally {
            bitmap.close();
          }
        } catch (error) {
          if (signal.aborted) throw error;
          this.fail(id, error, false);
        }
      }
    } catch (error) {
      if (!signal.aborted && !this.disposed) {
        if (error instanceof RangeErrorResponse) this.transportError(error);
        else for (const { id } of job.blocks) this.fail(id, error, true);
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

  private fail(id: number, error: unknown, retry: boolean) {
    this.failed.add(id);
    const button = document.createElement("button");
    button.type = "button";
    localize(button, () => message(error) + (retry ? ui(" · 重试", " · Retry") : ""));
    button.disabled = !retry;
    if (retry) button.onclick = () => {
      button.remove();
      this.failed.delete(id);
      this.queue();
    };
    this.slots[id].replaceChildren(button);
  }

  private report(render: () => string) {
    localize(notice, render);
    notice.hidden = false;
  }

  private transportError(error: RangeErrorResponse) {
    if (this.disposed || this.stopped) return;
    this.stopped = true;
    this.meta.abort();
    this.closePreviews();
    previewToggle.hidden = true;
    for (const job of this.jobs) job.controller.abort();
    if (error instanceof FileReplaced) {
      void open(async () => error.source, this.name, replacementMessage);
    } else {
      this.report(() => message(error));
    }
  }
}

let active: Reader | undefined;
let openingRequest: AbortController | undefined;
const replacementMessage = () => ui("地址返回了更新后的完整漫画包", "The URL returned a newer comic.");

function chooseSource() {
  openingRequest?.abort();
  active?.dispose();
  active = undefined;
  bookTitle.hidden = change.hidden = viewport.hidden = navigation.hidden = notice.hidden = true;
  opening.hidden = false;
  localize(openStatus, () => "");
  dropArea.classList.remove("dragging");
  pick.focus();
}

async function open(source: (signal: AbortSignal) => Promise<ByteSource>, name: string, update?: () => string) {
  openingRequest?.abort();
  const request = new AbortController();
  openingRequest = request;
  active?.dispose();
  active = undefined;
  bookTitle.hidden = viewport.hidden = navigation.hidden = notice.hidden = true;
  opening.hidden = false;
  change.hidden = false;
  openStatus.dataset.state = "loading";
  localize(openStatus, () => ui("正在读取漫画索引…", "Reading comic index…"));
  try {
    const comic = await readComic(await source(request.signal), request.signal, { tolerateInvalidThumbnails: true });
    request.signal.throwIfAborted();
    opening.hidden = true;
    viewport.hidden = navigation.hidden = bookTitle.hidden = false;
    bookTitle.textContent = name;
    viewport.scrollTop = 0;
    active = new Reader(comic, name);
    const damagedThumbnails = Boolean(comic.thumbnailError);
    if (update || damagedThumbnails) {
      localize(notice, () => [update?.(), damagedThumbnails && ui("缩略图索引或数据区损坏，已关闭页面预览",
        "The thumbnail section is damaged. Previews are disabled.")].filter(Boolean).join(ui("。", " ")));
      notice.hidden = false;
    }
    viewport.focus({ preventScroll: true });
  } catch (error) {
    if (!request.signal.aborted) {
      if (error instanceof FileReplaced) {
        void open(async () => error.source, name, replacementMessage);
      } else {
        change.hidden = true;
        openStatus.dataset.state = "error";
        localize(openStatus, () => message(error));
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
opening.ondragover = event => {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  dropArea.classList.add("dragging");
};
opening.ondragleave = event => {
  if (!opening.contains(event.relatedTarget as Node | null)) dropArea.classList.remove("dragging");
};
opening.ondrop = event => {
  dropArea.classList.remove("dragging");
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  const files = event.dataTransfer.files;
  const file = files[0];
  if (files.length === 1 && file.name.toLowerCase().endsWith(".cstr")) {
    void open(async () => fileSource(file), file.name);
  } else {
    openStatus.dataset.state = "error";
    localize(openStatus, () => ui("请拖入一个 .cstr 文件。", "Drop one .cstr file to open it."));
  }
};
for (const type of ["dragover", "drop"] as const) {
  window.addEventListener(type, event => {
    if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
  });
}
find<HTMLFormElement>("remote").onsubmit = event => {
  event.preventDefault();
  let url: URL;
  try {
    url = new URL(address.value.trim(), location.href);
  } catch {
    openStatus.dataset.state = "error";
    localize(openStatus, () => ui("请输入有效的漫画包链接", "Enter a valid comic URL"));
    return;
  }
  const name = url.pathname.split("/").pop() || url.hostname;
  void open(signal => openHttp(url, { signal, mutable: mutable.checked }), name);
};
change.onclick = chooseSource;
previous.onclick = () => active?.step(-1);
next.onclick = () => active?.step(1);
previewToggle.onclick = () => active?.togglePreviews();
previewPrevious.onclick = () => active?.shiftPreviews(-1);
previewNext.onclick = () => active?.shiftPreviews(1);
previews.onkeydown = event => {
  if (event.key === "Escape") {
    active?.togglePreviews();
    previewToggle.focus();
  }
};
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
    active?.stepHorizontally(event.key === "ArrowLeft" ? -1 : 1);
  }
};
