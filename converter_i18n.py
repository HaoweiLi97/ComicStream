"""Desktop converter strings and the local language preference."""

import os
from pathlib import Path

from portable_runtime import portable_data_directory


LANGUAGES = ("简体中文", "English")
ENGLISH = {
    "CSTR 转换器": "CSTR Converter",
    "添加任务": "Add tasks",
    "界面语言": "Language",
    "漫画文件或图片清单（可多选）…": "Comic files or image manifests…",
    "图片文件夹…": "Image folder…",
    "多张图片合为一项…": "Combine images into one task…",
    "移除所选": "Remove selected",
    "重新排队": "Requeue",
    "可将多个文件或文件夹拖入列表": "Drop files or folders into the list",
    "已忽略 {} 个不支持的项目": "Unsupported items skipped: {}",
    "输出位置": "Output location",
    "统一输出文件夹": "Shared output folder",
    "原文件位置": "Beside each source",
    "浏览…": "Browse…",
    "输入来源": "Source",
    "输出文件": "Output file",
    "状态": "Status",
    "进度": "Progress",
    "所选任务": "Selected task",
    "输入路径": "Source path",
    "元数据": "Metadata",
    "未设置": "None",
    "已设置 {} 个字段": "Fields set: {}",
    "编辑 JSON…": "Edit JSON…",
    "选择一项任务可查看详情。": "Select a task to view its details.",
    "批量转换设置": "Batch settings",
    "图像编码": "Encoding",
    "WebP 质量": "WebP quality",
    "阅读方向": "Reading order",
    "切段高度（px，0 自动）": "Strip height (px, 0 = auto)",
    "逐页缩略图": "Page thumbnails",
    "用首页生成封面": "First-page cover",
    "自动": "Auto",
    "从左向右 (LTR)": "Left to right (LTR)",
    "从右向左 (RTL)": "Right to left (RTL)",
    "从上向下 (TTB)": "Top to bottom (TTB)",
    "优先保留原图": "Preserve originals",
    "有损 WebP": "Lossy WebP",
    "无损 WebP": "Lossless WebP",
    "开始转换": "Convert",
    "打开输出文件夹": "Open output folder",
    "待处理": "Pending",
    "转换中": "Converting",
    "写入中": "Writing",
    "已完成": "Done",
    "失败": "Failed",
    "已跳过": "Skipped",
    "共 {} 项    待处理 {}    转换中 {}    完成 {}    失败 {}    跳过 {}":
        "Total {}    Pending {}    Running {}    Done {}    Failed {}    Skipped {}",
    "选择漫画文件或图片清单": "Select comic files or image manifests",
    "支持的漫画文件": "Supported comic files",
    "漫画压缩包": "Comic archives",
    "图片清单": "Image manifests",
    "电子书": "E-books",
    "选择漫画图片文件夹": "Select an image folder",
    "选择漫画页面": "Select comic pages",
    "漫画图片": "Comic images",
    "选择输出文件夹": "Select output folder",
    "请选择一个已存在的输出文件夹。": "Choose an existing output folder.",
    "JSON 元数据": "JSON metadata",
    '输入一个 JSON 对象，例如 {"title": "漫画名"}。留空可移除元数据。':
        'Enter a JSON object, e.g. {"title": "Comic title"}. Leave blank to clear metadata.',
    "导入 JSON 元数据": "Import JSON metadata",
    "无法读取 UTF-8 JSON 文件。{}": "Could not read the UTF-8 JSON file. {}",
    "元数据必须是 JSON 对象，最外层应使用花括号。": "Metadata must be a JSON object enclosed in braces.",
    "JSON 格式错误。请检查第 {} 行、第 {} 列。": "Invalid JSON at line {}, column {}.",
    "导入 JSON…": "Import JSON…",
    "应用": "Apply",
    "取消": "Cancel",
    "切段高度": "Strip height",
    "{}须为 0–{} 之间的整数。": "{} must be a whole number from 0 to {}.",
    "检查转换设置": "Check conversion settings",
    "替换输出文件": "Replace output files?",
    "有 {} 个输出文件已存在。要替换这些文件吗？\n选择“否”将跳过这些任务。":
        '{} output files already exist. Replace them?\nChoose "No" to skip those tasks.',
    "输出文件已存在。可更换输出位置后重新排队。": "An output file already exists. Change the output location and requeue.",
    "输出位置已有同名文件夹。": "A folder already has the output file's name.",
    "输出文件夹不存在。": "The output folder does not exist.",
    "本批次已有任务使用同一输出路径。": "Another task in this batch uses the same output path.",
    "压缩包无法读取。请检查文件是否完整。": "Could not read the archive. Check that the file is intact.",
    "无法读取其中一张图片。{}": "Could not read an image. {}",
    "转换期间出现了同名文件。可更换输出位置后重新排队。": "A file appeared at the output path during conversion. Change the output location and requeue.",
    "转换完成 · {} 页 · {} 个图像块\n{}": "Converted · {} pages · {} chunks\n{}",
    "已处理 {} / {} 页。": "Processed {} / {} pages.",
    "已处理 {} 页，正在写入。": "Processed {} pages. Writing output…",
    "正在读取漫画来源…": "Reading source…",
    "无法打开输出文件夹": "Could not open the output folder",
    "{}（{} 张）": "{} ({} images)",
    "图片": "Images",
}

CHINESE = {
    "Image folder not found.": "找不到图片文件夹。",
    "No pages found. Select a source containing comic pages.": "找不到可转换的图片。请选择包含漫画页面的来源。",
    "Choose an output file different from the source.": "请选择与来源不同的输出文件。",
    "MOBI, AZW and AZW3 input requires calibre (ebook-convert).": "转换 MOBI、AZW 或 AZW3 需要 calibre 的 ebook-convert。",
    "CSTR_EBOOK_CONVERT does not point to a file.": "CSTR_EBOOK_CONVERT 指定的程序文件不存在。",
    "No raster comic pages were found in the EPUB reading order.": "在 EPUB 的阅读顺序中找不到可转换的图像页。",
}


def translate(language, text, *values):
    strings = ENGLISH if language == LANGUAGES[1] else CHINESE
    message = strings.get(text, text)
    return message.format(*values) if values else message


def language_setting():
    portable = portable_data_directory()
    if portable is not None:
        return portable / "language.txt"
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "ComicStream" / "language.txt" if base else None


def load_language():
    path = language_setting()
    if path is not None:
        try:
            saved = path.read_text(encoding="utf-8").strip()
            if saved in LANGUAGES:
                return saved
        except (OSError, UnicodeError):
            pass
    return LANGUAGES[0]


def save_language(language):
    path = language_setting()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(language, encoding="utf-8")
        except OSError:
            pass
