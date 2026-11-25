# ComicStream

[English](README.md) · 简体中文

CSTR（`.cstr`）是为流式阅读优化的漫画文件格式。文件开头的二进制索引记录每页的尺寸和图像块位置，阅读器可以通过 HTTP Range 按需加载指定页、长条漫的可见分段或某页的完整缩略图。ComicStream 提供 Python 打包器和浏览器阅读器。

## CSTR 文件布局

![CSTR v1 二进制结构图：文件区段顺序、32 字节文件头，以及页表、块表和缩略图表的字段偏移](docs/images/cstr-v1-layout.zh-CN.png)

```text
[文件头][页表][块表][缩略图表*][JSON 元数据*][正文图像块][缩略图数据*]

* 可选。文件头标志位 bit 0 同时启用缩略图表和缩略图数据。
```

| 区段 | 记录内容 | 阅读器如何使用 |
| --- | --- | --- |
| 文件头（32 字节） | `CSTR` 魔数、版本、标志位、页数、块数、索引位置和长度、元数据长度 | 确定索引和后续区段的位置 |
| 页表（每页 16 字节） | 页宽、页高、首块下标、块数 | 找到第 N 页对应的连续块，并预留页面空间 |
| 块表（每块 20 字节） | 块高、编码、相对正文起点的偏移、字节长度 | 按块高定位页内位置，按偏移和长度读取图像 |
| 缩略图表（每页 16 字节，可选） | 图像宽高、字节长度、相对缩略图区起点的偏移 | 直接读取一张完整的小图，或合并读取相邻多张 |
| JSON 元数据（可选） | UTF-8 对象，如 `title` 和 `readingDirection` | 设置标题和翻页方向 |
| 正文图像块 | 按页序紧密排列的独立单帧图像，每块占满所属页的宽度 | 普通页通常读取一块，长条漫可按视口读取其中几块 |
| 缩略图数据（可选） | 按页序紧密排列的完整单帧 WebP | 打开预览时读取所需小图 |

当前格式为 **CSTR v1**。多字节整数按小端序存放，页号和块号从零开始。带缩略图的包使用 `version = 1` 和 `flags = 0x0001`，`flags = 0` 的包省略缩略图表和数据。 [二进制规范与字节样包](CSTR-v1.zh-CN.md)

## 从索引定位图像

1. 读取文件头和索引。正文起点是 `index_offset + index_size + meta_size`。
2. 从页表找到该页的块。页内各块自上而下排列，前面各块的高度之和就是当前块的纵坐标。正文块的文件起点是 `正文起点 + chunk.offset`。
3. 缩略图区从最后一块正文结束的位置开始。某页小图的文件起点是 `缩略图区起点 + thumbnail.offset`。读取该记录的 `size` 字节即可得到完整 WebP。

文件内的半开区间 `[start, end)` 对应 HTTP 请求头 `Range: bytes=start-(end-1)`。例如样包的首块位于 `[142, 178)`，请求 `Range: bytes=142-177` 就能取得该块。相邻块或相邻缩略图可以合并成一次请求，再按索引拆分。 [HTTP Range 读取](CSTR-v1.zh-CN.md#2-http-流式读取) · [字节样包](CSTR-v1.zh-CN.md#3-字节样包)

`readingDirection` 可取 `ltr`（从左向右）、`rtl`（从右向左）或 `ttb`（从上向下）。索引始终按实际阅读顺序排列。浏览器阅读器据此调整翻页控件，`rtl` 也会反转左右方向键和缩略图排列。正文支持连续滚动。

## 转换 CBZ 和散图

需要 Python 3.10+。导入 AVIF 需要安装支持 AVIF 的 Pillow。以下命令在项目目录运行。

~~~powershell
py -m pip install -r requirements.txt
py pack.py --cbz book.cbz --list-pages
py pack.py --cbz book.cbz -o book.cstr
py pack.py --cbz book.cbz --thumbnails --reading-direction rtl -o book.cstr
py pack.py --cbz book.cbz --webp -o book-webp.cstr
py pack.py --cbz webtoon.cbz --strip-height 1600 -o webtoon.cstr
py pack.py -o book.cstr 10.png 2.png 1.png
~~~

所有输入都按路径逐级自然排序。`chapter 2/1.png` 在 `chapter 10/1.png` 之前，同目录下的 `1.png`、`2.png`、`10.png` 按数字顺序排列。也可以用 `--manifest pages.json` 指定参加打包的图片。清单是 JSON 字符串数组，路径相对于清单文件。`--list-pages` 可与以上任一种输入配合使用。

整页 JPEG、PNG、WebP 和 AVIF 在符合读取上限且无需校正方向或布局时，保留原始字节。需要校正、切段或格式转换的页面会编码为 WebP。单帧 GIF、BMP 和 TIFF 可通过转换导入。打包器会拒绝动画等多帧输入。

阅读器每块最多允许 128 MiB 的 RGBA 解码预算和 32 MiB 的压缩数据。可用 `--strip-height` 指定每段最大高度。面向只支持 WebP 的阅读器时，可用 `--webp` 强制转换，也可以用 `--quality` 指定 WebP 质量或用 `--lossless` 写出无损 WebP。

`--thumbnails` 为每页嵌入一张 WebP 缩略图。小图保持比例，以宽至少 160 像素、高至少 224 像素为目标。极长页取顶部，极宽页取中央，原图太小时保留可用像素。`--metadata` 接受 UTF-8 JSON 对象。用 `--reading-direction ltr|rtl|ttb` 设置方向，命令行值优先于元数据文件。

ComicStream 打包器和阅读器采用相同的上限。索引区最多 8 MiB，JSON 元数据最多 256 KiB，均按写入文件的字节数计算。

## 浏览器阅读

需要 Node.js 24+。

~~~powershell
npm ci
npm run build
npm start
~~~

在浏览器打开 `http://127.0.0.1:4173`，选择本地 `.cstr` 或输入 HTTP(S) 地址。可以用页码、上一页和下一页跳转。阅读区获得焦点时也支持左右方向键。

如果正文索引有效且正文块的字节范围完整，缩略图索引损坏或包尾的缩略图区截断时，阅读器会提示并关闭页面预览，正文仍可阅读。

本地开发服务器为 `library` 目录中的 `.cstr` 提供 Range 读取。`library/book.cstr` 可通过同源链接 `/library/book.cstr` 打开。远程服务应支持单区间 Range 和原始字节传输。跨域时需允许页面来源并暴露 `Content-Range`、`ETag`。同一地址对应的内容会更新时，勾选“链接内容可能更新”，该服务需提供强 ETag 并允许 `If-Range`。

## 验证

~~~powershell
npm test
py tests/create_fixture.py
~~~

生成样本后，保持 `npm start` 运行。安装 Python Playwright 和 Chromium 后，执行 `py tests/browser.py` 验证浏览器本地打开、HTTP Range、乱序下载和跳页。可通过 `CSTR_CHROME` 指定已安装浏览器，通过 `CSTR_PORT` 改端口。
