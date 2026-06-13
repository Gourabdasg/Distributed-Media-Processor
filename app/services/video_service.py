"""
Video Processing Service
Uses FFmpeg-python for video transcoding, thumbnail extraction, and format conversion.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any

import ffmpeg

from app.core.config import settings
from app.models.schemas import VideoProcessingOptions, VideoTranscodeOptions, VideoThumbnailOptions

logger = logging.getLogger(__name__)


class VideoProcessor:
    """Handles all video processing operations using FFmpeg."""

    # ─── Main Processing Pipeline ────────────────────────

    def process(
        self,
        input_path: str,
        output_dir: str,
        options: VideoProcessingOptions,
    ) -> Dict[str, Any]:
        """
        Full video processing pipeline.
        Returns dict with output paths and metadata.
        """
        logger.info(f"Processing video: {input_path}")
        os.makedirs(output_dir, exist_ok=True)

        results = {
            "input_path": input_path,
            "output_dir": output_dir,
            "transcoded_path": None,
            "thumbnail_paths": [],
            "metadata": self.get_video_metadata(input_path),
        }

        # Step 1: Trim (if requested)
        working_path = input_path
        if options.trim_start is not None or options.trim_end is not None:
            trimmed_path = os.path.join(output_dir, "trimmed_input.mp4")
            working_path = self._trim_video(
                input_path, trimmed_path,
                options.trim_start, options.trim_end
            )

        # Step 2: Transcode
        if options.transcode:
            stem = Path(input_path).stem
            ext = options.transcode.output_format.value
            output_video_path = os.path.join(output_dir, f"{stem}_processed.{ext}")
            results["transcoded_path"] = self._transcode(
                working_path, output_video_path, options.transcode, options.mute
            )

        # Step 3: Extract thumbnails
        if options.thumbnails:
            results["thumbnail_paths"] = self._extract_thumbnails(
                input_path, output_dir, options.thumbnails
            )

        logger.info(f"Video processing complete: {results}")
        return results

    # ─── Transcode ───────────────────────────────────────

    def _transcode(
        self,
        input_path: str,
        output_path: str,
        opts: VideoTranscodeOptions,
        mute: bool = False,
    ) -> str:
        """Transcode video to target format and codec."""
        logger.info(f"Transcoding {input_path} → {output_path}")

        stream = ffmpeg.input(input_path)

        video_kwargs = {
            "vcodec": opts.video_codec,
            "crf": opts.crf,
            "preset": opts.preset,
        }

        # Apply scaling if requested
        if opts.scale_width or opts.scale_height:
            w = opts.scale_width or -1
            h = opts.scale_height or -1
            # FFmpeg requires dimensions divisible by 2
            scale_filter = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2"
            video = stream.video.filter("scale", w=w, h=h)
        else:
            video = stream.video

        video = video.filter("format", "yuv420p")  # Ensure browser compatibility

        if mute:
            output_stream = ffmpeg.output(
                video, output_path, **video_kwargs, **{"an": None}
            )
        else:
            audio_kwargs = {"acodec": opts.audio_codec}
            if not mute and opts.audio_codec == "aac":
                audio_kwargs["audio_bitrate"] = "128k"

            output_stream = ffmpeg.output(
                video, stream.audio,
                output_path,
                **video_kwargs,
                **audio_kwargs,
            )

        try:
            ffmpeg.run(
                output_stream,
                overwrite_output=True,
                quiet=True,
                capture_stderr=True,
            )
            logger.info(f"Transcode complete: {output_path} ({os.path.getsize(output_path)} bytes)")
            return output_path
        except ffmpeg.Error as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            logger.error(f"FFmpeg transcode error: {stderr}")
            raise RuntimeError(f"Video transcode failed: {stderr[:500]}")

    # ─── Thumbnail Extraction ────────────────────────────

    def _extract_thumbnails(
        self,
        input_path: str,
        output_dir: str,
        opts: VideoThumbnailOptions,
    ) -> List[str]:
        """Extract N thumbnails evenly distributed across video duration."""
        metadata = self.get_video_metadata(input_path)
        duration = metadata.get("duration", 0)

        if duration <= 0:
            logger.warning("Video duration is 0 or unknown, extracting frame at t=1s")
            timestamps = [1.0]
        else:
            # Distribute timestamps evenly (avoid very start/end)
            margin = duration * 0.05
            usable = duration - 2 * margin
            if opts.count == 1:
                timestamps = [duration / 2]
            else:
                step = usable / (opts.count - 1) if opts.count > 1 else usable
                timestamps = [margin + i * step for i in range(opts.count)]

        thumbnail_paths = []
        stem = Path(input_path).stem

        for i, ts in enumerate(timestamps):
            out_path = os.path.join(output_dir, f"{stem}_thumb_{i + 1:02d}.{opts.format}")
            try:
                (
                    ffmpeg
                    .input(input_path, ss=ts)
                    .filter("scale", opts.width, opts.height,
                            force_original_aspect_ratio="decrease")
                    .filter("pad", opts.width, opts.height, "(ow-iw)/2", "(oh-ih)/2")
                    .output(out_path, vframes=1, format="image2")
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True)
                )
                thumbnail_paths.append(out_path)
                logger.debug(f"Thumbnail {i+1}/{opts.count} extracted at t={ts:.1f}s")
            except ffmpeg.Error as e:
                stderr = e.stderr.decode() if e.stderr else ""
                logger.error(f"Thumbnail extraction failed at t={ts}: {stderr[:200]}")

        return thumbnail_paths

    # ─── Trim ────────────────────────────────────────────

    def _trim_video(
        self,
        input_path: str,
        output_path: str,
        start: Optional[float],
        end: Optional[float],
    ) -> str:
        """Trim video to a specific time range."""
        kwargs = {}
        if start is not None:
            kwargs["ss"] = start
        if end is not None:
            kwargs["t"] = end - (start or 0)

        try:
            (
                ffmpeg
                .input(input_path, **kwargs)
                .output(output_path, c="copy")
                .overwrite_output()
                .run(quiet=True, capture_stderr=True)
            )
            logger.info(f"Video trimmed: {output_path}")
            return output_path
        except ffmpeg.Error as e:
            stderr = e.stderr.decode() if e.stderr else ""
            raise RuntimeError(f"Trim failed: {stderr[:300]}")

    # ─── Metadata ────────────────────────────────────────

    def get_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """Extract video metadata using ffprobe."""
        try:
            probe = ffmpeg.probe(video_path)
            video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
            audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]

            metadata = {
                "duration": float(probe["format"].get("duration", 0)),
                "file_size": int(probe["format"].get("size", 0)),
                "bit_rate": int(probe["format"].get("bit_rate", 0)),
                "format": probe["format"].get("format_name", "unknown"),
            }

            if video_streams:
                vs = video_streams[0]
                r = vs.get("r_frame_rate", "0/1").split("/")
                fps = round(int(r[0]) / int(r[1]), 2) if len(r) == 2 and int(r[1]) else 0
                metadata.update({
                    "width": vs.get("width"),
                    "height": vs.get("height"),
                    "video_codec": vs.get("codec_name"),
                    "fps": fps,
                    "pixel_format": vs.get("pix_fmt"),
                })

            if audio_streams:
                as_ = audio_streams[0]
                metadata.update({
                    "audio_codec": as_.get("codec_name"),
                    "audio_channels": as_.get("channels"),
                    "audio_sample_rate": as_.get("sample_rate"),
                })

            return metadata
        except ffmpeg.Error as e:
            logger.error(f"ffprobe failed for {video_path}: {e}")
            return {}

    # ─── Utility ─────────────────────────────────────────

    def check_ffmpeg_available(self) -> bool:
        """Verify FFmpeg is installed and accessible."""
        try:
            result = subprocess.run(
                [settings.FFMPEG_PATH, "-version"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


# Singleton instance
video_processor = VideoProcessor()
