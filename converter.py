"""Windows desktop interface for the CSTR packer."""

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from zipfile import BadZipFile

from PIL import UnidentifiedImageError

from comic_source import COMIC_ARCHIVES, EBOOK_EXTENSIONS, FILE_EXTENSIONS, ZIP_EXTENSIONS, Source
from converter_i18n import LANGUAGES, load_language, save_language, translate
from pack import IMAGE_EXTENSIONS, WEBP_MAX_DIMENSION, encode_metadata


COMIC_TYPES = [("支持的漫画文件", tuple(f"*{ext}" for ext in FILE_EXTENSIONS)),
               ("漫画压缩包", tuple(f"*{ext}" for ext in (*ZIP_EXTENSIONS, *COMIC_ARCHIVES))),
               ("电子书", tuple(f"*{ext}" for ext in EBOOK_EXTENSIONS)), ("PDF", "*.pdf"), ("图片清单", "*.json")]
IMAGE_TYPES = [("漫画图片", tuple(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS)))]
DIRECTIONS = {"自动": None, "从左向右 (LTR)": "ltr", "从右向左 (RTL)": "rtl", "从上向下 (TTB)": "ttb"}
ENCODINGS = ("优先保留原图", "有损 WebP", "无损 WebP")
STATUSES = {"pending": "待处理", "running": "转换中", "done": "已完成", "failed": "失败", "skipped": "已跳过"}
TITLE = "CSTR 转换器"
DROP_HINT = "可将多个文件或文件夹拖入列表"


@dataclass
class Task:
    source: Source
    output: Path
    metadata: dict | None = None
    state: str = "pending"
    progress: int | None = None
    detail: tuple = ()


class Converter:
    def __init__(self, root):
        self.root = root
        self.language = load_language()
        self.translations = []
        self.ignored = 0
        self.localize(root.title, TITLE)
        ttk.Style(root).configure("Error.TLabel", foreground="#a4262c")
        self.tasks: dict[str, Task] = {}
        self.events = SimpleQueue()
        self.running = False
        self.selected_id = None
        self.file_drop_handler = None
        self.source_text = tk.StringVar()
        self.output_mode = tk.StringVar(value="folder")
        self.output_folder = tk.StringVar()
        self.metadata_text = tk.StringVar()
        self.detail_text = tk.StringVar()
        self.summary = tk.StringVar()
        self.quality = tk.StringVar(value="85")
        self.strip_height = tk.StringVar(value="0")
        self.thumbnails = tk.BooleanVar(value=True)
        self.cover = tk.BooleanVar(value=True)

        panel = ttk.Frame(root, padding=12)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        self.build_queue(panel)
        self.build_details(panel)
        self.build_options(panel)

        actions = ttk.Frame(panel)
        actions.grid(row=5, sticky="ew", pady=(12, 0))
        self.convert = self.text_widget(ttk.Button(actions, command=self.start, default="active", width=12), "开始转换")
        self.convert.pack(side="right")
        self.reveal = self.text_widget(ttk.Button(actions, command=self.open_output), "打开输出文件夹")
        self.reveal.pack(side="right", padx=(0, 8))

        ttk.Separator(root).pack(fill="x")
        status = ttk.Frame(root, padding=(12, 6))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.summary).pack(side="left")
        self.progress = ttk.Progressbar(status, length=132)
        self.progress.pack(side="right")

        self.output_mode.trace_add("write", self.destinations_changed)
        self.output_folder.trace_add("write", self.destinations_changed)
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.selection_changed()
        self.update_summary()
        root.update_idletasks()
        if os.name == "nt":
            from win_drop import accept_file_drops

            try:
                self.file_drop_handler = accept_file_drops(root, self.table, self.add_dropped_paths)
            except OSError:
                pass  # The file and folder pickers are available without Explorer drops.
            else:
                self.drop_hint.pack(side="right")
            root.update_idletasks()
        root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())
        self.add.focus_set()

    def tr(self, text, *values):
        return translate(self.language, text, *values)

    def localize(self, setter, text):
        self.translations.append((setter, text))
        setter(self.tr(text))

    def text_widget(self, widget, text):
        self.localize(lambda value: widget.configure(text=value), text)
        return widget

    def change_language(self, _event=None):
        self.language = self.language_picker.get()
        save_language(self.language)
        for setter, text in self.translations:
            setter(self.tr(text))
        for widget, values in ((self.direction, DIRECTIONS), (self.encoding, ENCODINGS)):
            index = widget.current()
            widget.configure(values=tuple(map(self.tr, values)))
            widget.current(index)
        self.update_drop_hint()
        for key in self.tasks:
            self.update_row(key)
        self.selection_changed()
        self.update_summary()
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), self.root.winfo_reqheight())

    def build_queue(self, panel):
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=0, sticky="ew", pady=(0, 8))
        self.add = self.text_widget(ttk.Menubutton(toolbar), "添加任务")
        menu = tk.Menu(self.add, tearoff=False)
        for index, (label, command) in enumerate((
            ("漫画文件或图片清单（可多选）…", self.choose_archives),
            ("图片文件夹…", self.choose_folder), ("多张图片合为一项…", self.choose_images),
        )):
            menu.add_command(command=command)
            self.localize(lambda value, item=index: menu.entryconfigure(item, label=value), label)
        self.add.configure(menu=menu)
        self.remove = self.text_widget(ttk.Button(toolbar, command=self.remove_selected), "移除所选")
        self.retry = self.text_widget(ttk.Button(toolbar, command=self.requeue_selected), "重新排队")
        for button in (self.add, self.remove, self.retry):
            button.pack(side="left", padx=(0, 8))
        self.language_picker = ttk.Combobox(toolbar, values=LANGUAGES, state="readonly", width=9)
        self.language_picker.set(self.language)
        self.language_picker.bind("<<ComboboxSelected>>", self.change_language)
        self.language_picker.pack(side="right", padx=(8, 0))
        self.text_widget(ttk.Label(toolbar), "界面语言").pack(side="right")
        self.drop_hint = ttk.Label(toolbar, text=self.tr(DROP_HINT))

        destination = self.text_widget(ttk.Labelframe(panel, padding=(10, 8)), "输出位置")
        destination.grid(row=1, sticky="ew", pady=(0, 8))
        destination.columnconfigure(2, weight=1)
        shared = self.text_widget(ttk.Radiobutton(destination, variable=self.output_mode, value="folder"), "统一输出文件夹")
        original = self.text_widget(ttk.Radiobutton(destination, variable=self.output_mode, value="source"), "原文件位置")
        shared.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 6))
        original.grid(row=0, column=1, sticky="w", pady=(0, 6))
        self.destination_modes = (shared, original)
        self.output_entry = ttk.Entry(destination, textvariable=self.output_folder)
        self.output_entry.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.browse = self.text_widget(ttk.Button(destination, command=self.choose_output_folder, width=12), "浏览…")
        self.browse.grid(row=1, column=3, padx=(8, 0))

        queue = ttk.Frame(panel)
        queue.grid(row=2, sticky="nsew")
        queue.columnconfigure(0, weight=1)
        queue.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(queue, columns=("input", "output", "state", "progress"), show="headings", height=7)
        for column, title, width, stretch in (
            ("input", "输入来源", 206, True), ("output", "输出文件", 202, True),
            ("state", "状态", 76, False), ("progress", "进度", 60, False),
        ):
            self.table.heading(column, anchor="w" if stretch else "center")
            self.localize(lambda value, name=column: self.table.heading(name, text=value), title)
            self.table.column(column, width=width, minwidth=width, stretch=stretch, anchor="w" if stretch else "center")
        scrollbar = ttk.Scrollbar(queue, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def build_details(self, panel):
        details = self.text_widget(ttk.Labelframe(panel, padding=(10, 8)), "所选任务")
        details.grid(row=3, sticky="ew", pady=(10, 0))
        details.columnconfigure(1, weight=1)
        self.source_entry = ttk.Entry(details, textvariable=self.source_text, state="readonly")
        self.metadata_button = self.text_widget(ttk.Button(details, command=self.edit_metadata, width=12), "编辑 JSON…")
        for row, label, widget in (
            (0, "输入路径", self.source_entry), (1, "元数据", ttk.Label(details, textvariable=self.metadata_text)),
        ):
            self.text_widget(ttk.Label(details), label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=(0, 4))
            widget.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        self.metadata_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=(0, 4))
        self.detail_label = ttk.Label(details, textvariable=self.detail_text, wraplength=500)
        self.detail_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        details.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(1, event.width - 24)))

    def build_options(self, panel):
        options = self.text_widget(ttk.Labelframe(panel, padding=(10, 8)), "批量转换设置")
        options.grid(row=4, sticky="ew", pady=(10, 0))
        options.columnconfigure(1, weight=1, uniform="values")
        options.columnconfigure(3, weight=1, uniform="values")
        self.direction = ttk.Combobox(options, values=tuple(map(self.tr, DIRECTIONS)), state="readonly", width=18)
        self.encoding = ttk.Combobox(options, values=tuple(map(self.tr, ENCODINGS)), state="readonly", width=18)
        self.direction.current(0)
        self.encoding.current(0)
        strip = ttk.Spinbox(options, textvariable=self.strip_height, from_=0, to=WEBP_MAX_DIMENSION, increment=100, width=8)
        self.quality_input = ttk.Spinbox(options, textvariable=self.quality, from_=0, to=100, width=8)
        for row, column, label, widget in (
            (0, 0, "图像编码", self.encoding), (0, 2, "WebP 质量", self.quality_input),
            (1, 0, "阅读方向", self.direction), (1, 2, "切段高度（px，0 自动）", strip),
        ):
            self.text_widget(ttk.Label(options), label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=(0, 6))
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 20) if column == 0 else 0, pady=(0, 6))
        thumbnails = self.text_widget(ttk.Checkbutton(options, variable=self.thumbnails), "逐页缩略图")
        cover = self.text_widget(ttk.Checkbutton(options, variable=self.cover), "用首页生成封面")
        thumbnails.grid(row=2, column=0, columnspan=2, sticky="w")
        cover.grid(row=2, column=2, columnspan=2, sticky="w")
        self.encoding.bind("<<ComboboxSelected>>", lambda _: self.sync_controls())
        self.options = (self.direction, self.encoding, strip, thumbnails, cover)

    def sync_controls(self):
        idle = not self.running
        selected = self.table.selection()
        task = self.tasks.get(self.selected_id)
        editable = idle and task is not None and task.state != "done"
        retryable = idle and any(self.tasks[key].state in ("failed", "skipped") for key in selected)
        shared = self.output_mode.get() == "folder"
        has_target = bool(self.output_folder.get().strip()) if shared else True
        for widget, enabled in (
            *((widget, idle) for widget in (*self.options, *self.destination_modes)),
            (self.add, idle), (self.remove, idle and selected), (self.retry, retryable),
            (self.output_entry, idle and shared), (self.browse, idle and shared), (self.metadata_button, editable),
            (self.reveal, has_target if shared else task is not None),
            (self.convert, idle and has_target and any(task.state == "pending" for task in self.tasks.values())),
        ):
            widget.state(["!disabled" if enabled else "disabled"])
        self.quality_input.state(["!disabled" if idle and self.encoding.current() == 1 else "disabled"])

    def choose_archives(self):
        paths = filedialog.askopenfilenames(parent=self.root, title=self.tr("选择漫画文件或图片清单"),
                                            filetypes=[(self.tr(name), pattern) for name, pattern in COMIC_TYPES])
        self.add_tasks(Source.from_file(path) for path in map(Path, paths) if path.suffix.lower() in FILE_EXTENSIONS)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.root, title=self.tr("选择漫画图片文件夹"), mustexist=True)
        if path:
            self.add_tasks([Source("folder", (Path(path),))])

    def choose_images(self):
        paths = filedialog.askopenfilenames(parent=self.root, title=self.tr("选择漫画页面"),
                                            filetypes=[(self.tr(name), pattern) for name, pattern in IMAGE_TYPES])
        if paths:
            self.add_tasks([Source("images", tuple(map(Path, paths)))])

    def add_dropped_paths(self, paths):
        if self.running or self.root.grab_current() is not None:
            return
        sources = []
        images = []
        ignored = 0
        for path in map(Path, paths):
            suffix = path.suffix.lower()
            if path.is_dir():
                sources.append(Source("folder", (path,)))
            elif suffix in FILE_EXTENSIONS:
                sources.append(Source.from_file(path))
            elif suffix in IMAGE_EXTENSIONS:
                images.append(path)
            else:
                ignored += 1
        if images:
            sources.append(Source("images", tuple(images)))
        self.add_tasks(sources)
        self.ignored = ignored
        self.update_drop_hint()

    def update_drop_hint(self):
        self.drop_hint.configure(text=self.tr("已忽略 {} 个不支持的项目", self.ignored) if self.ignored else self.tr(DROP_HINT))

    def add_tasks(self, sources):
        first_key = None
        for source in sources:
            key = self.table.insert("", "end")
            self.tasks[key] = Task(source, source.suggested_output())
            if first_key is None:
                first_key = key
        if first_key is not None:
            self.ignored = 0
            self.update_drop_hint()
            if not self.output_folder.get().strip():
                self.output_folder.set(str(self.tasks[first_key].output.parent.absolute()))
                self.output_entry.xview_moveto(1)
            else:
                self.destinations_changed()
            self.table.selection_set(first_key)
            self.table.focus(first_key)
            self.table.see(first_key)
            self.progress.configure(value=0)
            self.selection_changed()
            self.update_summary()

    def destinations_changed(self, *_):
        if self.running:
            return
        shared = self.output_mode.get() == "folder"
        folder = Path(self.output_folder.get().strip()).absolute()
        reserved = {task.output for task in self.tasks.values() if task.state == "done"}
        next_suffix = {}
        for key, task in self.tasks.items():
            if task.state == "done":
                continue
            suggested = task.source.suggested_output().absolute()
            if shared:
                suggested = folder / suggested.name
            output = suggested
            suffix = next_suffix.get(suggested, 2)
            while output in reserved:
                output = suggested.with_name(f"{suggested.stem} ({suffix}).cstr")
                suffix += 1
            next_suffix[suggested] = suffix
            reserved.add(output)
            task.output = output
            self.update_row(key)
        self.sync_controls()

    def remove_selected(self):
        if self.running:
            return
        selected = self.table.selection()
        for key in selected:
            del self.tasks[key]
        self.table.delete(*selected)
        self.progress.configure(value=0)
        self.destinations_changed()
        self.selection_changed()
        self.update_summary()

    def requeue_selected(self):
        if self.running:
            return
        for key in self.table.selection():
            task = self.tasks[key]
            if task.state in ("failed", "skipped"):
                task.state, task.progress, task.detail = "pending", None, ()
                self.update_row(key)
        self.progress.configure(value=0)
        self.update_summary()
        self.sync_controls()

    def selection_changed(self, _event=None):
        selected = self.table.selection()
        self.selected_id = selected[0] if len(selected) == 1 else None
        task = self.tasks.get(self.selected_id)
        self.source_text.set(str(task.source.paths[0]) if task else "")
        self.source_entry.xview_moveto(1)
        self.update_metadata_label(task)
        self.update_detail(task)
        self.sync_controls()

    def update_metadata_label(self, task):
        self.metadata_text.set(
            "" if task is None else self.tr("未设置") if task.metadata is None else self.tr("已设置 {} 个字段", len(task.metadata))
        )

    def update_detail(self, task):
        self.detail_label.configure(style="Error.TLabel" if task and task.state == "failed" else "TLabel")
        if task:
            self.detail_text.set(self.tr(*(task.detail or (STATUSES[task.state],))))
        else:
            self.detail_text.set(self.tr("选择一项任务可查看详情。") if self.tasks else "")

    def update_row(self, key):
        task = self.tasks[key]
        state = "写入中" if task.state == "running" and task.progress == 100 else STATUSES[task.state]
        progress = "" if task.progress is None else f"{task.progress}%"
        self.table.item(key, values=(task.source.label(self.tr), task.output.name, self.tr(state), progress))
        if key == self.selected_id:
            self.update_detail(task)

    def update_summary(self):
        counts = Counter(task.state for task in self.tasks.values())
        self.summary.set(self.tr("共 {} 项    待处理 {}    转换中 {}    完成 {}    失败 {}    跳过 {}", len(self.tasks),
                                 *(counts[state] for state in STATUSES)))

    def choose_output_folder(self):
        initial = self.output_folder.get().strip()
        path = filedialog.askdirectory(parent=self.root, title=self.tr("选择输出文件夹"),
                                        mustexist=True, initialdir=initial or None)
        if path:
            self.output_folder.set(path)
            self.output_entry.xview_moveto(1)

    def get_output_folder(self):
        name = self.output_folder.get().strip()
        if not name or not Path(name).is_dir():
            raise ValueError("请选择一个已存在的输出文件夹。")
        return Path(name).absolute()

    def edit_metadata(self):
        task = self.tasks.get(self.selected_id)
        if task is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(self.tr("JSON 元数据"))
        dialog.transient(self.root)
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text=self.tr('输入一个 JSON 对象，例如 {"title": "漫画名"}。留空可移除元数据。'),
                  wraplength=480).grid(row=0, columnspan=2, sticky="w", pady=(0, 8))
        editor = tk.Text(panel, width=62, height=12, wrap="word", font="TkFixedFont",
                         undo=True, padx=6, pady=6, borderwidth=1, relief="sunken")
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=editor.yview)
        editor.configure(yscrollcommand=scrollbar.set)
        editor.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        if task.metadata is not None:
            editor.insert("1.0", json.dumps(task.metadata, ensure_ascii=False, indent=2))
        error_text = tk.StringVar()
        ttk.Label(panel, textvariable=error_text, style="Error.TLabel", wraplength=480).grid(
            row=2, columnspan=2, sticky="ew", pady=(6, 2))

        def import_json():
            path = filedialog.askopenfilename(parent=dialog, title=self.tr("导入 JSON 元数据"), filetypes=[("JSON", "*.json")])
            if path:
                try:
                    content = Path(path).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as error:
                    error_text.set(self.tr("无法读取 UTF-8 JSON 文件。{}", error))
                    return
                editor.delete("1.0", "end")
                editor.insert("1.0", content)
                editor.edit_reset()
                error_text.set("")
                editor.focus_set()

        def apply_json():
            content = editor.get("1.0", "end-1c")
            has_json = bool(content.strip())
            try:
                value = json.loads(content) if has_json else None
                if has_json and not isinstance(value, dict):
                    raise ValueError("元数据必须是 JSON 对象，最外层应使用花括号。")
                encode_metadata(value, tuple(DIRECTIONS.values())[self.direction.current()])
            except json.JSONDecodeError as error:
                editor.mark_set("insert", f"{error.lineno}.{error.colno - 1}")
                editor.see("insert")
                editor.focus_set()
                error_text.set(self.tr("JSON 格式错误。请检查第 {} 行、第 {} 列。", error.lineno, error.colno))
                return
            except (ValueError, RecursionError) as error:
                error_text.set(self.tr(str(error)))
                return
            task.metadata = value
            self.update_metadata_label(task)
            dialog.destroy()

        actions = ttk.Frame(panel)
        actions.grid(row=3, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text=self.tr("导入 JSON…"), command=import_json).pack(side="left")
        ttk.Button(actions, text=self.tr("应用"), command=apply_json, default="active").pack(side="right")
        ttk.Button(actions, text=self.tr("取消"), command=dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.minsize(460, 320)
        dialog.wait_visibility()
        dialog.grab_set()
        editor.focus_set()

    def number(self, variable, label, maximum):
        try:
            value = int(variable.get())
            if 0 <= value <= maximum:
                return value
        except ValueError:
            pass
        raise ValueError(self.tr("{}须为 0–{} 之间的整数。", self.tr(label), maximum))

    def start(self):
        if self.running:
            return
        encoding = self.encoding.current()
        try:
            if self.output_mode.get() == "folder":
                self.get_output_folder()
            options = dict(
                strip_height=self.number(self.strip_height, "切段高度", WEBP_MAX_DIMENSION),
                quality=self.number(self.quality, "WebP 质量", 100) if encoding == 1 else None,
                lossless=encoding == 2, thumbnails=self.thumbnails.get(), cover=self.cover.get(),
                reading_direction=tuple(DIRECTIONS.values())[self.direction.current()],
            )
        except (OSError, ValueError) as error:
            messagebox.showerror(self.tr("检查转换设置"), self.tr(str(error)), parent=self.root)
            return

        planned = self.plan_batch()
        existing = sum(overwrite for _, _, overwrite in planned)
        replace = not existing or messagebox.askyesno(
            self.tr("替换输出文件"), self.tr("有 {} 个输出文件已存在。要替换这些文件吗？\n选择“否”将跳过这些任务。", existing),
            parent=self.root,
        )
        batch = []
        for key, output, overwrite in planned:
            task = self.tasks[key]
            if overwrite and not replace:
                task.state, task.detail = "skipped", ("输出文件已存在。可更换输出位置后重新排队。",)
                self.update_row(key)
                continue
            batch.append((key, task.source, output, dict(options, metadata=task.metadata, overwrite=overwrite)))
            self.update_row(key)

        self.running = bool(batch)
        self.progress.configure(maximum=len(batch) or 1, value=0)
        self.selection_changed()
        self.update_summary()
        if batch:
            Thread(target=self.run_batch, args=(batch,)).start()
            self.root.after(60, self.poll)

    def plan_batch(self):
        planned = []
        reserved = set()
        for key, task in self.tasks.items():
            if task.state != "pending":
                continue
            output = task.output
            try:
                if output.is_dir():
                    raise ValueError("输出位置已有同名文件夹。")
                if not output.parent.is_dir():
                    raise ValueError("输出文件夹不存在。")
                resolved = output.resolve()
                if resolved in reserved:
                    raise ValueError("本批次已有任务使用同一输出路径。")
                overwrite = output.exists()
                reserved.add(resolved)
                planned.append((key, output, overwrite))
            except (OSError, ValueError, RuntimeError) as error:
                task.state, task.detail = "failed", (str(error),)
                self.update_row(key)
        return planned

    def run_batch(self, batch):
        for key, source, output, options in batch:
            self.events.put((key, "running", None))
            previous = -1

            def report(done, total):
                nonlocal previous
                percent = done * 100 // total
                if percent != previous:
                    self.events.put((key, "progress", (done, total, percent)))
                    previous = percent

            try:
                pages, chunks = source.convert(output, options, report)
            except BadZipFile:
                result = ("failed", ("压缩包无法读取。请检查文件是否完整。",))
            except UnidentifiedImageError as error:
                result = ("failed", ("无法读取其中一张图片。{}", str(error)))
            except FileExistsError:
                result = ("failed", ("转换期间出现了同名文件。可更换输出位置后重新排队。",))
            except Exception as error:
                result = ("failed", (str(error) or type(error).__name__,))
            else:
                result = ("done", ("转换完成 · {} 页 · {} 个图像块\n{}", pages, chunks, output))
            self.events.put((key, *result))
        self.events.put((None, "finished", None))

    def poll(self):
        # Limit work per tick so batches of very small files stay responsive.
        changed = False
        for _ in range(256):
            try:
                key, event, data = self.events.get_nowait()
            except Empty:
                break
            if event == "finished":
                self.running = False
                break
            task = self.tasks[key]
            if event == "progress":
                done, total, task.progress = data
                task.detail = ("已处理 {} / {} 页。", done, total) if done < total else ("已处理 {} 页，正在写入。", total)
            else:
                task.state = event
                changed = True
                task.detail = ("正在读取漫画来源…",) if event == "running" else data
                if event in ("done", "failed"):
                    task.progress = 100 if event == "done" else None
                    self.progress.configure(value=self.progress["value"] + 1)
            self.update_row(key)
        if changed or not self.running:
            self.update_summary()
            self.sync_controls()
        if self.running:
            self.root.after(60, self.poll)

    def open_output(self):
        try:
            if self.output_mode.get() == "folder":
                folder = self.get_output_folder()
            else:
                task = self.tasks.get(self.selected_id)
                if task is None:
                    return
                folder = task.output.parent
            os.startfile(folder)
        except (OSError, ValueError) as error:
            messagebox.showerror(self.tr("无法打开输出文件夹"), self.tr(str(error)), parent=self.root)

    def close(self):
        if self.running:
            self.root.bell()
            return
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    Converter(window)
    window.mainloop()
