import { CODEC, FormatError } from "./cstr.js";
import { bitmapCost, decodeWebP } from "./webp.js";

function validPng(view: DataView, width: number, height: number) {
  if (view.byteLength < 33 || view.getUint32(0) !== 0x89504e47 || view.getUint32(4) !== 0x0d0a1a0a
      || view.getUint32(8) !== 13 || view.getUint32(12) !== 0x49484452 // IHDR
      || view.getUint32(16) !== width || view.getUint32(20) !== height) return false;
  for (let offset = 33; offset + 12 <= view.byteLength;) {
    const length = view.getUint32(offset);
    const type = view.getUint32(offset + 4);
    offset += length + 12;
    if (offset > view.byteLength) return false;
    // CSTR chunks contain one frame, including PNG files with animation metadata.
    if ([0x6163544c, 0x6663544c, 0x66644154].includes(type)) return false; // acTL, fcTL, fdAT
    if (type === 0x49454e44) return length === 0; // IEND
  }
  return false;
}

function validJpeg(view: DataView, width: number, height: number) {
  if (view.byteLength < 2 || view.getUint16(0) !== 0xffd8) return false; // SOI
  let offset = 2;
  while (offset < view.byteLength && view.getUint8(offset) === 0xff) {
    do { offset++; } while (offset < view.byteLength && view.getUint8(offset) === 0xff);
    if (offset + 3 > view.byteLength) return false;
    const marker = view.getUint8(offset++);
    if (marker === 0xd9 || marker === 0xda) return false; // EOI, SOS
    const size = view.getUint16(offset);
    if (size < 2 || offset + size > view.byteLength) return false;
    // Start-of-frame markers carry dimensions. Other 0xCx markers are tables or extensions.
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return size >= 8 && view.getUint16(offset + 3) === height && view.getUint16(offset + 5) === width;
    }
    offset += size;
  }
  return false;
}

function* avifBoxes(view: DataView, start: number, end: number) {
  let offset = start;
  while (offset + 8 <= end) {
    let size = view.getUint32(offset);
    const type = String.fromCharCode(
      view.getUint8(offset + 4), view.getUint8(offset + 5), view.getUint8(offset + 6), view.getUint8(offset + 7),
    );
    let headerSize = 8;
    if (size === 1 && offset + 16 <= end) {
      size = Number(view.getBigUint64(offset + 8));
      headerSize = 16;
    } else if (size === 0) {
      size = end - offset;
    }
    if (size < headerSize || size > end - offset) break;
    yield { type, start: offset + headerSize, end: offset + size };
    offset += size;
  }
  if (offset !== end) throw new FormatError("AVIF 区段长度无效");
}

function* avifImageProperties(view: DataView, start: number, end: number) {
  // meta is a FullBox. Its child boxes start after the four version/flag bytes.
  for (const box of avifBoxes(view, start + 4, end)) {
    if (box.type !== "iprp") continue;
    for (const container of avifBoxes(view, box.start, box.end)) {
      if (container.type === "ipco") yield* avifBoxes(view, container.start, container.end);
    }
  }
}

function validAvif(view: DataView) {
  let stillBrand = false;
  let hasExtents = false;
  for (const { type, start, end } of avifBoxes(view, 0, view.byteLength)) {
    if (type === "ftyp") {
      if (end - start < 8 || (end - start) % 4) return false;
      stillBrand = view.getUint32(start) === 0x61766966; // avif
      for (let brand = start + 8; !stillBrand && brand < end; brand += 4) {
        stillBrand = view.getUint32(brand) === 0x61766966;
      }
    }
    if (type === "moov") return false; // An image sequence stores a movie track.
    if (type !== "meta") continue;
    for (const extents of avifImageProperties(view, start, end)) {
      if (extents.type !== "ispe") continue;
      if (extents.end - extents.start !== 12 || view.getUint32(extents.start) !== 0) return false;
      bitmapCost(view.getUint32(extents.start + 4), view.getUint32(extents.start + 8));
      hasExtents = true;
    }
  }
  return stillBrand && hasExtents;
}

const stillDecoders = {
  [CODEC.avif]: { mime: "image/avif", name: "AVIF", validate: validAvif },
  [CODEC.png]: { mime: "image/png", name: "PNG", validate: validPng },
  [CODEC.jpeg]: { mime: "image/jpeg", name: "JPEG", validate: validJpeg },
} as const;

export async function decodeImage(
  bytes: Uint8Array<ArrayBuffer>, codec: number, width: number, height: number,
  outputWidth = width, outputHeight = height,
) {
  if (codec === CODEC.webp) return decodeWebP(bytes, width, height, outputWidth, outputHeight);
  if (codec !== CODEC.avif && codec !== CODEC.png && codec !== CODEC.jpeg) {
    throw new FormatError("此阅读器暂不支持该图像编码");
  }
  bitmapCost(width, height);
  const { mime, name, validate } = stillDecoders[codec];
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (!validate(view, width, height)) throw new FormatError(name + " 损坏或尺寸与索引不符");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(new Blob([bytes], { type: mime }));
  } catch (error) {
    throw new FormatError("无法解码 " + name + " 图像", { cause: error });
  }
  if (bitmap.width === width && bitmap.height === height && width === outputWidth && height === outputHeight) {
    return bitmap;
  }
  try {
    if (bitmap.width !== width || bitmap.height !== height) {
      throw new FormatError(name + " 尺寸与索引不符");
    }
    return await createImageBitmap(bitmap, { resizeWidth: outputWidth, resizeHeight: outputHeight, resizeQuality: "high" });
  } finally {
    bitmap.close();
  }
}
