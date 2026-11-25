import { FormatError, LIMITS } from "./cstr.js";

export function bitmapCost(width: number, height: number) {
  if (width > 16383 || height > 16383 || width * height * 4 > LIMITS.decoded) {
    throw new FormatError("图像尺寸超过解码上限");
  }
  return width * height * 4;
}

export async function decodeWebP(
  bytes: Uint8Array<ArrayBuffer>, width: number, height: number, outputWidth = width, outputHeight = height,
) {
  bitmapCost(width, height);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const invalid = () => new FormatError("WebP 损坏或尺寸与索引不符");
  if (bytes.length < 20 || view.getUint32(0) !== 0x52494646 || view.getUint32(8) !== 0x57454250
      || view.getUint32(4, true) + 8 !== bytes.length) throw invalid();
  let offset = 12;
  let frame = false;
  while (offset + 8 <= bytes.length) {
    const tag = view.getUint32(offset);
    const length = view.getUint32(offset + 4, true);
    const body = offset + 8;
    offset = body + length + (length & 1);
    if (offset > bytes.length) throw invalid();
    if (tag === 0x414e494d || tag === 0x414e4d46) throw invalid(); // ANIM, ANMF
    if (tag === 0x56503858) { // VP8X
      if (length !== 10 || (bytes[body] & 2)
          || (view.getUint32(body + 4, true) & 0xffffff) + 1 !== width
          || (view.getUint32(body + 6, true) >>> 8) + 1 !== height) throw invalid();
    }
    if (tag === 0x5650384c) { // VP8L
      if (frame || length < 5 || bytes[body] !== 0x2f) throw invalid();
      const dimensions = view.getUint32(body + 1, true);
      if ((dimensions & 0x3fff) + 1 !== width || ((dimensions >>> 14) & 0x3fff) + 1 !== height) {
        throw invalid();
      }
      frame = true;
    }
    if (tag === 0x56503820) { // VP8
      if (frame || length < 10 || (bytes[body] & 1) || bytes[body + 3] !== 0x9d
          || view.getUint16(body + 4) !== 0x012a
          || (view.getUint16(body + 6, true) & 0x3fff) !== width
          || (view.getUint16(body + 8, true) & 0x3fff) !== height) throw invalid();
      frame = true;
    }
  }
  if (!frame || offset !== bytes.length) throw invalid();
  const options: ImageBitmapOptions = { imageOrientation: "none" };
  if (outputWidth !== width || outputHeight !== height) {
    options.resizeWidth = outputWidth;
    options.resizeHeight = outputHeight;
    options.resizeQuality = "high";
  }
  const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/webp" }), options);
  if (bitmap.width === outputWidth && bitmap.height === outputHeight) return bitmap;
  bitmap.close();
  throw invalid();
}
