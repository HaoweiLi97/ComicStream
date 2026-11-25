# ComicStream

ComicStream 把漫画图片打包成 `.cstr`，浏览器按当前页和视口读取图像块。格式和可重建的字节样包见 [CSTR-v1.md](CSTR-v1.md)。

ComicStream is a browser-based comic and manga reader with a Python CBZ-to-CSTR converter. Its TypeScript reader uses HTTP byte-range requests for on-demand page loading, random page access, and viewport-based reading of long webtoon strips.

## 主要功能

- **CBZ 转换与散图打包**。支持 ZIP 漫画包、图片文件和 JSON 清单，按文件路径自然排序，并生成独立的 WebP 图像块。
- **HTTP Range 流式阅读**。先读前置页表和块表，再加载当前页所需图像。支持浏览器本地文件、远程 HTTP(S) 地址和页码跳转。
- **长条漫分段**。打包时按高度切分，阅读器按视口按需解码，并限制预读并发和位图缓存。
- **CSTR v1 容器规范**。文档包含二进制布局、Range 读取协议和可重建的测试样本。开发服务器使用 Node.js，打包器使用 Python 和 Pillow。

## 转换 CBZ 和散图

需要 Python 3.10+。以下命令在项目目录运行。

~~~powershell
py -m pip install -r requirements.txt
py pack.py --cbz book.cbz --list-pages
py pack.py --cbz book.cbz -o book.cstr
py pack.py --cbz webtoon.cbz --strip-height 1600 -o webtoon.cstr
py pack.py -o book.cstr 10.png 2.png 1.png
~~~

所有输入都按路径逐级自然排序。`chapter 2/1.png` 在 `chapter 10/1.png` 之前，同目录下的 `1.png`、`2.png`、`10.png` 按数字顺序排列。也可以用 `--manifest pages.json` 指定参加打包的图片。清单是 JSON 字符串数组，路径相对于清单文件。`--list-pages` 可与以上任一种输入配合使用。

普通页默认整页编码成 WebP。达到 WebP 尺寸、自带阅读器单块 128 MiB RGBA 预算或 32 MiB 压缩大小上限时，打包器会自动切段。长条漫可用 `--strip-height` 指定每段的最大高度。`--lossless` 启用无损编码，`--quality` 调整编码质量，`--metadata` 接受 UTF-8 JSON 对象，阅读器会显示其中的 `title`。

## 浏览器阅读

需要 Node.js 24+。

~~~powershell
npm ci
npm run build
npm start
~~~

在浏览器打开 `http://127.0.0.1:4173`，选择本地 `.cstr` 或输入 HTTP(S) 地址。可以用页码、上一页和下一页跳转。阅读区获得焦点时也支持左右方向键。

本地开发服务器为 `library` 目录中的 `.cstr` 提供 Range 读取。`library/book.cstr` 可通过同源链接 `/library/book.cstr` 打开。远程服务应支持单区间 Range 和原始字节传输。跨域时需允许页面来源并暴露 `Content-Range`、`ETag`。同一地址对应的内容会更新时，勾选“链接内容可能更新”，该服务需提供强 ETag 并允许 `If-Range`。当前阅读器解码 WebP。

## 验证

~~~powershell
npm test
py tests/create_fixture.py
~~~

生成样本后，保持 `npm start` 运行。安装 Python Playwright 和 Chromium 后，执行 `py tests/browser.py` 验证浏览器本地打开、HTTP Range、乱序下载和跳页。可通过 `CSTR_CHROME` 指定已安装浏览器，通过 `CSTR_PORT` 改端口。
