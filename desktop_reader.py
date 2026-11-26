"""Native Windows reference reader for CSTR files."""

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from itertools import accumulate
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import unquote, urlsplit

from PIL import ImageTk

from converter_i18n import LANGUAGES, load_language, save_language
from read_cstr import Comic, FileBytes, HttpBytes


class Reader:
    def __init__(self, root):
        self.root = root
        self.language = tk.StringVar(value=load_language())
        self.labels = []
        self.jobs = ThreadPoolExecutor(max_workers=2)
        self.results = SimpleQueue()
        self.book = None
        self.page = 0
        self.open_id = self.layout_id = 0
        self.photos = {}
        self.pending = set()
        self.loading = 0
        self.render_after = self.resize_after = None
        self.preview = None
        root.title("CSTR Reader")
        root.geometry("960x740")
        root.minsize(630, 450)

        bar = ttk.Frame(root, padding=(12, 8))
        bar.pack(fill="x")
        self.button(bar, "打开文件", "Open file", self.open_file)
        self.button(bar, "打开链接", "Open URL", self.open_url)
        self.cover = self.button(bar, "封面", "Cover", self.show_cover)
        self.previews = self.button(bar, "缩略图", "Thumbnails", self.show_previews)
        language = ttk.Combobox(bar, values=LANGUAGES, state="readonly", width=10, textvariable=self.language)
        language.pack(side="right")
        language.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Separator(root).pack(fill="x")

        content = ttk.Frame(root)
        content.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(content, background="#252628", borderwidth=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.scroll_to)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind("<Configure>", self.resized)
        self.canvas.bind("<MouseWheel>", self.mousewheel)
        self.canvas.bind("<Button-1>", lambda _: self.canvas.focus_set())

        bottom = ttk.Frame(root, padding=(12, 8))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.next = self.button(bottom, "下一页", "Next", lambda: self.go(self.page + 1), side="right")
        self.total = ttk.Label(bottom, text="/ 0")
        self.total.pack(side="right", padx=6)
        self.number = ttk.Entry(bottom, width=6, justify="center")
        self.number.pack(side="right")
        self.number.bind("<Return>", self.enter_page)
        self.previous = self.button(bottom, "上一页", "Previous", lambda: self.go(self.page - 1), side="right")
        for key, change in (("<Left>", -1), ("<Right>", 1)):
            root.bind(key, lambda event, step=change: self.arrow(event, step))
        root.bind("<Control-o>", lambda _: self.open_file())
        root.protocol("WM_DELETE_WINDOW", self.close)
        if os.name == "nt":
            from win_drop import accept_file_drops

            try:
                self.drop_handler = accept_file_drops(root, self.canvas, self.open_dropped)
            except OSError:
                self.drop_handler = None
        self.refresh_controls()
        root.after(30, self.poll)

    def tr(self, chinese, english):
        return english if self.language.get() == LANGUAGES[1] else chinese

    def button(self, parent, chinese, english, command, side="left"):
        button = ttk.Button(parent, text=self.tr(chinese, english), command=command)
        button.pack(side=side, padx=(0, 6))
        if parent is self.root or parent.winfo_toplevel() == self.root:
            self.labels.append((button, chinese, english))
        return button

    def change_language(self, _event):
        save_language(self.language.get())
        for button, chinese, english in self.labels:
            button.configure(text=self.tr(chinese, english))
        if self.preview is not None:
            self.preview.destroy()
            self.preview = None
        self.refresh_controls()
        if self.book is None:
            self.relayout()

    def submit(self, run, done):
        future = self.jobs.submit(run)
        future.add_done_callback(lambda result: self.results.put((done, result)))

    def poll(self):
        try:
            while True:
                done, result = self.results.get_nowait()
                error = result.exception()
                done(None if error else result.result(), error)
        except Empty:
            pass
        self.root.after(30, self.poll)

    def open_file(self):
        selected = filedialog.askopenfilename(parent=self.root, filetypes=[("CSTR", "*.cstr")])
        if selected:
            self.open(selected)

    def open_url(self):
        url = simpledialog.askstring("CSTR", self.tr("输入 HTTP(S) 漫画链接", "Enter an HTTP(S) comic URL"), parent=self.root)
        if url:
            self.open(url.strip())

    def open_dropped(self, paths):
        path = next((path for path in paths if Path(path).suffix.lower() == ".cstr"), None)
        if path:
            self.open(path)

    def open(self, value):
        remote = urlsplit(value).scheme.lower() in ("http", "https")
        path = Path(value) if not remote else None
        name = path.name if path is not None else unquote(urlsplit(value).path.rsplit("/", 1)[-1])
        self.open_id += 1
        request = self.open_id
        self.status.configure(text=self.tr("正在打开…", "Opening…"))

        def done(book, error):
            if request != self.open_id:
                return
            if error:
                self.error(error)
                self.refresh_controls()
                return
            if self.preview is not None:
                self.preview.destroy()
                self.preview = None
            self.book = book
            title = book.metadata.get("title")
            self.name = title if isinstance(title, str) and title else name
            self.root.title(f"CSTR Reader — {self.name}" if self.name else "CSTR Reader")
            self.go(0, force=True)

        self.submit(lambda: Comic(HttpBytes(value) if remote else FileBytes(path)), done)

    def refresh_controls(self):
        count = len(self.book.pages) if self.book else 0
        self.number.configure(state="normal")
        self.number.delete(0, "end")
        if count:
            self.number.insert(0, str(self.page + 1))
        self.total.configure(text=f"/ {count}")
        self.number.configure(state="normal" if count else "disabled")
        for widget, enabled in ((self.previous, self.page > 0), (self.next, self.page + 1 < count),
                                (self.cover, self.book and self.book.cover),
                                (self.previews, self.book and self.book.thumbnails)):
            widget.configure(state="normal" if enabled else "disabled")
        self.status.configure(text=self.name if count else self.tr("打开或拖入 CSTR 文件", "Open or drop a CSTR file"))

    def enter_page(self, _event):
        try:
            self.go(int(self.number.get()) - 1)
        except ValueError:
            self.refresh_controls()
        self.canvas.focus_set()

    def arrow(self, event, step):
        if event.widget in (self.canvas, self.root) and self.book:
            if self.book.metadata.get("readingDirection") == "rtl":
                step = -step
            self.go(self.page + step)

    def go(self, page, force=False):
        if self.book is None:
            return
        page = max(0, min(page, len(self.book.pages) - 1))
        if page != self.page or force:
            self.page = page
            self.relayout(reset=True)
        self.refresh_controls()

    def resized(self, _event):
        if self.resize_after is not None:
            self.root.after_cancel(self.resize_after)
        self.resize_after = self.root.after(80, self.relayout)

    def relayout(self, reset=False):
        if self.resize_after is not None:
            self.root.after_cancel(self.resize_after)
            self.resize_after = None
        self.layout_id += 1
        old_position = self.canvas.yview()[0]
        self.canvas.delete("all")
        self.photos.clear()
        self.pending.clear()
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if self.book is None:
            self.canvas.create_text(width // 2, height // 2, fill="#ededee",
                                    text=self.tr("打开或拖入 CSTR 文件", "Open or drop a CSTR file"))
            return
        page = self.book.pages[self.page]
        self.scaled_width = min(page.width, max(1, width - 32), 2048)
        self.chunk_edges = [0, *accumulate(chunk.height for chunk in page.chunks)]
        self.chunk_edges = [round(edge * self.scaled_width / page.width) for edge in self.chunk_edges]
        # A narrow display can round adjacent subpixel strips to the same row.
        for i in range(1, len(self.chunk_edges)):
            self.chunk_edges[i] = max(self.chunk_edges[i], self.chunk_edges[i - 1] + 1)
        self.left = (width - self.scaled_width) // 2
        self.top = max(12, (height - self.chunk_edges[-1]) // 2)
        self.canvas.configure(scrollregion=(0, 0, width, max(height, self.top + self.chunk_edges[-1] + 12)))
        self.canvas.yview_moveto(0 if reset else old_position)
        self.schedule_render()

    def scroll_to(self, *args):
        self.canvas.yview(*args)
        self.schedule_render()

    def mousewheel(self, event):
        self.scroll_to("scroll", -int(event.delta / 120) * 3, "units")

    def schedule_render(self):
        if self.render_after is None:
            self.render_after = self.root.after(40, self.render)

    def render(self):
        self.render_after = None
        if self.book is None:
            return
        view_start = max(0, self.canvas.canvasy(0) - self.top)
        view_end = self.canvas.canvasy(self.canvas.winfo_height()) - self.top
        first = max(0, bisect_right(self.chunk_edges, view_start) - 1)
        last = min(bisect_right(self.chunk_edges, view_end), len(self.chunk_edges) - 1)
        visible = set(range(first, last))
        for i in self.photos.keys() - visible:
            tag, _ = self.photos.pop(i)
            self.canvas.delete(tag)
        layout = self.layout_id
        book = self.book
        chunks = book.pages[self.page].chunks
        for i in sorted(visible - self.photos.keys() - self.pending)[:max(0, 4 - self.loading)]:
            self.pending.add(i)
            self.loading += 1
            size = self.scaled_width, self.chunk_edges[i + 1] - self.chunk_edges[i]
            record = chunks[i]

            def done(image, error, i=i):
                self.loading -= 1
                self.schedule_render()
                if layout != self.layout_id:
                    if image:
                        image.close()
                    return
                self.pending.discard(i)
                x, y = self.left, self.top + self.chunk_edges[i]
                if error:
                    tag = self.canvas.create_text(x + 6, y + 6, anchor="nw", fill="#ffb4ab",
                                                  text=self.tr("无法显示图像块", "Could not display image chunk"))
                    self.photos[i] = (tag, None)
                    self.status.configure(text=str(error))
                else:
                    photo = ImageTk.PhotoImage(image, master=self.root)
                    self.photos[i] = (self.canvas.create_image(x, y, image=photo, anchor="nw"), photo)
                    image.close()

            self.submit(lambda record=record, size=size: book.image(record, size), done)

    def error(self, error):
        messagebox.showerror(self.tr("无法打开", "Could not open"), str(error), parent=self.root)

    def show_cover(self):
        book = self.book
        record = book.cover
        scale = min(1, 640 / record.width, 750 / record.height)

        def done(image, error):
            if book is not self.book:
                return
            if error:
                self.error(error)
                return
            window = tk.Toplevel(self.root)
            window.title(self.tr("封面", "Cover"))
            window.photo = ImageTk.PhotoImage(image, master=window)
            ttk.Label(window, image=window.photo).pack(padx=12, pady=12)
            image.close()

        self.submit(lambda: book.image(record, (max(1, round(record.width * scale)), max(1, round(record.height * scale)))), done)

    def show_previews(self):
        if self.preview is not None and self.preview.winfo_exists():
            self.preview.lift()
            return
        window = self.preview = tk.Toplevel(self.root)
        window.title(self.tr("逐页缩略图", "Page thumbnails"))
        pages = ttk.Frame(window, padding=10)
        pages.pack()
        controls = ttk.Frame(window, padding=(10, 0, 10, 10))
        controls.pack(fill="x")
        book = self.book
        records = book.thumbnails
        active = (self.page // 8) * 8

        def choose(index):
            self.go(index)
            window.destroy()
            self.preview = None

        def load(first):
            nonlocal active
            active = first
            for widget in pages.winfo_children():
                widget.destroy()
            previous.configure(state="normal" if first > 0 else "disabled")
            following.configure(state="normal" if first + 8 < len(records) else "disabled")
            for cell, index in enumerate(range(first, min(first + 8, len(records)))):
                btn = ttk.Button(pages, text=str(index + 1), compound="top", command=lambda index=index: choose(index))
                btn.grid(row=cell // 4, column=cell % 4, padx=5, pady=5, ipadx=6)
                record = records[index]
                scale = min(1, 120 / record.width, 150 / record.height)
                size = max(1, round(record.width * scale)), max(1, round(record.height * scale))

                def done(image, error, btn=btn, batch=first):
                    if image and not error and window.winfo_exists() and active == batch and btn.winfo_exists():
                        btn.photo = ImageTk.PhotoImage(image, master=window)
                        btn.configure(image=btn.photo)
                    if image:
                        image.close()

                self.submit(lambda record=record, size=size: book.image(record, size), done)

        previous = self.button(controls, "前一组", "Previous set", lambda: load(active - 8))
        following = self.button(controls, "后一组", "Next set", lambda: load(active + 8), side="right")
        load(active)

    def close(self):
        self.jobs.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    reader = Reader(root)
    if len(sys.argv) > 1:
        reader.open(sys.argv[1])
    root.mainloop()
