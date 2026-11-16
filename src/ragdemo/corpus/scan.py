"""Turn a text PDF into a scan: rendered pages, slightly rotated, noisy, JPEG-compressed, no text layer."""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

DPI = 170


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    """What a flatbed scanner and a fax-era copier do to a clean page."""
    gray = img.convert("L")
    angle = rng.uniform(-1.4, 1.4)
    gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    arr = np.asarray(gray, dtype=np.float32)
    h, w = arr.shape
    # uneven lighting across the page
    gradient = np.linspace(rng.uniform(0.9, 0.97), 1.0, w, dtype=np.float32)[None, :]
    arr = arr * gradient
    # sensor noise and a little toner dust
    noise = np.random.default_rng(rng.randint(0, 2**31 - 1)).normal(0, rng.uniform(5, 9), arr.shape).astype(np.float32)
    arr = arr + noise
    specks = np.random.default_rng(rng.randint(0, 2**31 - 1)).random(arr.shape) < 0.0004
    arr[specks] = rng.uniform(20, 90)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    out = Image.fromarray(arr, mode="L")
    out = out.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.35, 0.7)))
    if rng.random() < 0.3:  # a fold line
        fold = np.asarray(out, dtype=np.float32)
        row = rng.randint(int(h * 0.3), int(h * 0.7))
        fold[row - 2 : row + 2, :] *= 0.82
        out = Image.fromarray(np.clip(fold, 0, 255).astype(np.uint8), mode="L")
    return out


def scan_pdf(src: Path, dst: Path, rng: random.Random) -> int:
    """Render `src` page by page, degrade, and save an image-only PDF to `dst`. Returns the page count."""
    pdf = pdfium.PdfDocument(str(src))
    pages: list[Image.Image] = []
    try:
        for page in pdf:
            bitmap = page.render(scale=DPI / 72, grayscale=True)
            pages.append(degrade(bitmap.to_pil(), rng))
    finally:
        pdf.close()
    pages[0].save(dst, "PDF", save_all=True, append_images=pages[1:], resolution=float(DPI), quality=rng.randint(45, 65))
    return len(pages)
