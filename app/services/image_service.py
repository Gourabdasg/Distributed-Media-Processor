"""
Image Processing Service
Uses Pillow for image manipulation: resize, crop, compress, watermark.
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ExifTags
from PIL.Image import Resampling

from app.core.config import settings
from app.models.schemas import ImageProcessingOptions, ImageResizeOptions, ImageWatermarkOptions

logger = logging.getLogger(__name__)


class ImageProcessor:
    """Handles all image processing operations."""

    RESAMPLING = Resampling.LANCZOS

    # ─── Main Processing Pipeline ────────────────────────

    def process(
        self,
        input_path: str,
        output_path: str,
        options: ImageProcessingOptions,
    ) -> dict:
        """
        Full image processing pipeline.
        Returns metadata about the processed image.
        """
        logger.info(f"Processing image: {input_path}")
        start_info = {}

        with Image.open(input_path) as img:
            # Record original stats
            start_info = {
                "original_format": img.format,
                "original_size": img.size,
                "original_mode": img.mode,
                "original_file_size": os.path.getsize(input_path),
            }

            # Step 1: Auto-orient based on EXIF
            if options.auto_orient:
                img = self._auto_orient(img)

            # Step 2: Convert to RGB if needed
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")

            # Step 3: Crop
            if options.crop:
                img = self._crop(img, options.crop)

            # Step 4: Resize
            if options.resize:
                img = self._resize(img, options.resize)

            # Step 5: Grayscale
            if options.grayscale:
                img = img.convert("L").convert("RGB")

            # Step 6: Watermark
            if options.watermark:
                img = self._apply_watermark(img, options.watermark)

            # Step 7: Compress & Save
            compress_opts = options.compress
            output_format = "JPEG"
            quality = settings.IMAGE_DEFAULT_QUALITY
            strip_metadata = True

            if compress_opts:
                output_format = compress_opts.output_format.value.upper()
                if output_format == "JPEG":
                    output_format = "JPEG"
                quality = compress_opts.quality
                strip_metadata = compress_opts.strip_metadata

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            if output_format in ("JPEG", "JPG") and img.mode == "RGBA":
                img = img.convert("RGB")
            save_kwargs = self._get_save_kwargs(output_format, quality, strip_metadata, img)
            img.save(output_path, format=output_format, **save_kwargs)

        output_size = os.path.getsize(output_path)
        reduction_pct = round(
            (1 - output_size / start_info["original_file_size"]) * 100, 1
        ) if start_info["original_file_size"] > 0 else 0

        result = {
            **start_info,
            "output_format": output_format,
            "output_file_size": output_size,
            "size_reduction_percent": reduction_pct,
            "output_path": output_path,
        }
        logger.info(
            f"Image processed: {start_info['original_file_size']} → "
            f"{output_size} bytes ({reduction_pct}% reduction)"
        )
        return result

    # ─── Resize ──────────────────────────────────────────

    def _resize(self, img: Image.Image, opts: ImageResizeOptions) -> Image.Image:
        """Resize image with aspect ratio control."""
        orig_w, orig_h = img.size
        target_w = opts.width or orig_w
        target_h = opts.height or orig_h

        if not opts.upscale:
            target_w = min(target_w, orig_w)
            target_h = min(target_h, orig_h)

        if opts.maintain_aspect_ratio:
            img.thumbnail((target_w, target_h), self.RESAMPLING)
            logger.debug(f"Resized (aspect preserved): {orig_w}x{orig_h} → {img.size}")
        else:
            img = img.resize((target_w, target_h), self.RESAMPLING)
            logger.debug(f"Resized (forced): {orig_w}x{orig_h} → {img.size}")

        return img

    # ─── Crop ────────────────────────────────────────────

    def _crop(self, img: Image.Image, crop_box: dict) -> Image.Image:
        """Crop image using (left, top, right, bottom) box."""
        left = crop_box.get("left", 0)
        top = crop_box.get("top", 0)
        right = crop_box.get("right", img.width)
        bottom = crop_box.get("bottom", img.height)
        box = (left, top, right, bottom)
        logger.debug(f"Cropping to box {box}")
        return img.crop(box)

    # ─── Watermark ───────────────────────────────────────

    def _apply_watermark(self, img: Image.Image, opts: ImageWatermarkOptions) -> Image.Image:
        """Apply text watermark to the image."""
        text = opts.text or settings.IMAGE_WATERMARK_TEXT
        opacity = int(255 * opts.opacity)

        # Convert to RGBA for transparency support
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Try to load a font, fallback to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                      opts.font_size)
        except (IOError, OSError):
            font = ImageFont.load_default()

        # Calculate text bounding box
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding = 20
        w, h = img.size

        position_map = {
            "top-left":     (padding, padding),
            "top-right":    (w - text_w - padding, padding),
            "bottom-left":  (padding, h - text_h - padding),
            "bottom-right": (w - text_w - padding, h - text_h - padding),
            "center":       ((w - text_w) // 2, (h - text_h) // 2),
        }
        pos = position_map.get(opts.position, position_map["bottom-right"])

        # Draw shadow for visibility
        shadow_offset = max(1, opts.font_size // 20)
        draw.text(
            (pos[0] + shadow_offset, pos[1] + shadow_offset),
            text, font=font, fill=(0, 0, 0, opacity // 2)
        )
        # Draw main text
        draw.text(pos, text, font=font, fill=(255, 255, 255, opacity))

        combined = Image.alpha_composite(img, overlay)
        return combined.convert("RGB")

    # ─── Auto-Orient (EXIF) ──────────────────────────────

    def _auto_orient(self, img: Image.Image) -> Image.Image:
        """Rotate image based on EXIF orientation data."""
        try:
            exif = img._getexif()
            if exif is None:
                return img

            orientation_key = next(
                (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
            )
            if orientation_key is None:
                return img

            orientation = exif.get(orientation_key)
            rotations = {
                3: Image.ROTATE_180,
                6: Image.ROTATE_270,
                8: Image.ROTATE_90,
            }
            if orientation in rotations:
                img = img.transpose(rotations[orientation])
        except Exception:
            pass  # Best-effort: ignore EXIF errors
        return img

    # ─── Save Options ────────────────────────────────────

    def _get_save_kwargs(
        self, fmt: str, quality: int, strip_metadata: bool, img: Image.Image
    ) -> dict:
        """Build format-specific save parameters."""
        kwargs = {}

        if fmt in ("JPEG", "JPG"):
            # JPEG doesn't support alpha
            if img.mode == "RGBA":
                img = img.convert("RGB")
            kwargs["quality"] = quality
            kwargs["optimize"] = True
            kwargs["progressive"] = True
            if not strip_metadata:
                kwargs["exif"] = img.info.get("exif", b"")

        elif fmt == "PNG":
            # PNG compression: 0 (none) to 9 (max)
            kwargs["compress_level"] = max(0, min(9, (100 - quality) // 11))
            kwargs["optimize"] = True

        elif fmt == "WEBP":
            kwargs["quality"] = quality
            kwargs["method"] = 6  # Slowest but best compression
            kwargs["lossless"] = quality == 100

        return kwargs

    # ─── Batch Variants ──────────────────────────────────

    def generate_responsive_variants(
        self,
        input_path: str,
        output_dir: str,
        sizes: List[Tuple[int, int]] = None,
    ) -> List[str]:
        """Generate multiple responsive image sizes."""
        sizes = sizes or [(320, 240), (640, 480), (1280, 720), (1920, 1080)]
        output_paths = []

        with Image.open(input_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            for w, h in sizes:
                variant = img.copy()
                variant.thumbnail((w, h), self.RESAMPLING)
                stem = Path(input_path).stem
                out_path = os.path.join(output_dir, f"{stem}_{w}x{h}.jpg")
                os.makedirs(output_dir, exist_ok=True)
                variant.save(out_path, "JPEG", quality=85, optimize=True)
                output_paths.append(out_path)
                logger.debug(f"Variant saved: {out_path}")

        return output_paths


# Singleton instance
image_processor = ImageProcessor()
