"""Render one uploaded PDF in a subprocess separate from the web worker."""

import json
import math
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from study_notes import MAX_NOTE_PAGES


def render(directory: Path) -> int:
    if sys.platform == "linux":
        import resource

        memory = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    sizes = []
    try:
        with pdfium.PdfDocument(directory / "original.pdf") as document:
            if not len(document):
                return 2
            if len(document) > MAX_NOTE_PAGES:
                return 3
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        return 2
                    bitmap = page.render(scale=min(1440 / width, 2040 / height))
                    try:
                        with bitmap.to_pil().convert("RGB") as image:
                            sizes.append(image.size)
                            image.save(directory / f"page-{index + 1}.webp", quality=88, method=1)
                            if index == 0:
                                image.thumbnail((360, 510), Image.Resampling.LANCZOS)
                                image.save(directory / "cover.webp", quality=85, method=1)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    except (pdfium.PdfiumError, ValueError, OverflowError):
        return 2
    print(json.dumps(sizes))
    return 0


if __name__ == "__main__":
    raise SystemExit(render(Path(sys.argv[1])))
