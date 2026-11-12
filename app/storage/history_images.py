"""聊天图片文件；Timeline 只保存引用和显示尺寸。"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from urllib.parse import unquote_to_bytes
import uuid
import warnings

from PIL import Image, ImageOps


# 320px 聊天图片在 2x 屏幕上使用 640px 缩略图；预览覆盖常见桌面窗口。
THUMBNAIL_EDGE = 640
PREVIEW_EDGE = 2560


class HistoryImages:
    def __init__(self, timeline_path: Path) -> None:
        self.root = timeline_path.parent / "images"

    def path(self, image: dict, variant: str = "original") -> Path:
        suffix = ".image" if variant == "original" else f".{variant}.webp"
        return self.root / (image["id"] + suffix)

    def _save_previews(self, image: dict) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(self.path(image)) as source:
                    width, height = source.size
                    if source.getexif().get(274) in (5, 6, 7, 8):
                        width, height = height, width
                    image.update(width=width, height=height)
                    # thumbnail 先用解码器缩小 JPEG，再做最终缩放。
                    source.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
                    preview = ImageOps.exif_transpose(source)
                    if preview.mode not in ("RGB", "RGBA"):
                        preview = preview.convert("RGBA")
                    if max(width, height) > PREVIEW_EDGE:
                        preview.save(self.path(image, "preview"), "WEBP", quality=85)
                    preview.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
                    preview.save(self.path(image, "thumbnail"), "WEBP", quality=80)
        except Exception:
            # 衍生图失败保留原件，历史页仍可显式打开原图。
            self._remove_paths([self.path(image, variant) for variant in ("thumbnail", "preview")])
            logging.getLogger(__name__).warning("聊天图片预览生成失败: %s", image["id"], exc_info=True)

    def save(self, observations) -> list[dict]:
        images = []
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for observation in observations:
                header, content = observation.data_url.split(",", 1)
                media_type = header[5:].split(";", 1)[0].lower()
                image = {"id": uuid.uuid4().hex, "mediaType": media_type,
                         "width": observation.width, "height": observation.height}
                data = (base64.b64decode(content, validate=True) if header.endswith(";base64")
                        else unquote_to_bytes(content))
                images.append(image)
                # 引用在文件完整落盘后才写入 Timeline。
                with self.path(image).open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._save_previews(image)
            return images
        except BaseException:
            self.remove(images)
            raise

    def remove(self, images: list[dict]) -> None:
        self._remove_paths([self.path(image, variant) for image in images
                            for variant in ("original", "thumbnail", "preview")])

    def _remove_paths(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("未引用的聊天图片清理失败", exc_info=True)
