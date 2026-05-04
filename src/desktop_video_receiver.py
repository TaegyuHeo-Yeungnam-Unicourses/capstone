#!/usr/bin/env python3
"""Desktop RTSP viewer and one-hour segment recorder.

The desktop opens an RTSP stream, displays it in real time, and writes the
received frames into temporary video segments. A segment is finalized as a
normal video file only when it has received at least --segment-seconds seconds
of continuous stream data. The default threshold is 3600 seconds, i.e. 1 hour.

This behavior matches the requirement:
    - real-time monitoring on the desktop
    - save to a file when the transmitted video length is 1 hour or more
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2


DEFAULT_SEGMENT_SECONDS = 3600.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="View an RTSP stream in real time and save completed one-hour segments."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="RTSP stream URL. Example: rtsp://192.168.0.23:8554/capstone",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("recordings"),
        help="Directory where completed video files are stored.",
    )
    parser.add_argument(
        "--segment-seconds",
        type=float,
        default=DEFAULT_SEGMENT_SECONDS,
        help="Minimum continuous stream duration before a file is finalized. Default: 3600 seconds.",
    )
    parser.add_argument(
        "--display",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show realtime video window. Use --no-display on headless systems.",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=3.0,
        help="Seconds to wait before reconnecting after stream failure.",
    )
    parser.add_argument(
        "--fallback-fps",
        type=float,
        default=30.0,
        help="FPS used when the RTSP decoder does not report a valid FPS.",
    )
    parser.add_argument(
        "--keep-short-files",
        action="store_true",
        help="Keep interrupted segments shorter than --segment-seconds with a short_ prefix.",
    )
    return parser.parse_args()


def valid_fps(value: float, fallback: float) -> float:
    if value <= 1.0 or value > 240.0:
        return fallback
    return value


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class SegmentRecorder:
    """Write stream frames and finalize only sufficiently long segments."""

    def __init__(self, output_dir: Path, segment_seconds: float, keep_short_files: bool) -> None:
        self.output_dir = output_dir
        self.segment_seconds = segment_seconds
        self.keep_short_files = keep_short_files
        self.writer: cv2.VideoWriter | None = None
        self.temp_path: Path | None = None
        self.started_at_monotonic = 0.0
        self.started_at_label = ""
        self.fps = 0.0
        self.frame_size: tuple[int, int] | None = None
        self.frame_count = 0

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def start(self, frame, fps: float) -> None:
        height, width = frame.shape[:2]
        self.frame_size = (width, height)
        self.fps = fps
        self.frame_count = 0
        self.started_at_monotonic = time.monotonic()
        self.started_at_label = timestamp()
        self.temp_path = self.output_dir / f"recording_{self.started_at_label}.tmp.avi"

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        self.writer = cv2.VideoWriter(str(self.temp_path), fourcc, fps, self.frame_size)
        if not self.writer.isOpened():
            raise RuntimeError(f"Cannot open video writer: {self.temp_path}")

        print(f"[INFO] Recording temporary segment: {self.temp_path}")

    def write(self, frame, fps: float) -> None:
        if self.writer is None:
            self.start(frame, fps)

        assert self.writer is not None
        self.writer.write(frame)
        self.frame_count += 1

        if self.elapsed_seconds >= self.segment_seconds:
            self.finalize(completed=True)

    @property
    def elapsed_seconds(self) -> float:
        if self.writer is None:
            return 0.0
        return time.monotonic() - self.started_at_monotonic

    def finalize(self, *, completed: bool) -> None:
        if self.writer is None or self.temp_path is None:
            return

        self.writer.release()
        self.writer = None

        elapsed = self.elapsed_seconds
        end_label = timestamp()

        if completed:
            final_path = self.output_dir / (
                f"rtsp_{self.started_at_label}_to_{end_label}_{int(self.segment_seconds)}s.avi"
            )
            self.temp_path.rename(final_path)
            print(f"[INFO] Saved completed segment: {final_path}")
        elif self.keep_short_files:
            final_path = self.output_dir / (
                f"short_{self.started_at_label}_to_{end_label}_{int(elapsed)}s.avi"
            )
            self.temp_path.rename(final_path)
            print(f"[WARN] Kept short interrupted segment: {final_path}")
        else:
            self.temp_path.unlink(missing_ok=True)
            print("[WARN] Deleted interrupted segment shorter than threshold.")

        self.temp_path = None
        self.frame_size = None
        self.frame_count = 0
        self.started_at_monotonic = 0.0
        self.started_at_label = ""

    def close(self) -> None:
        self.finalize(completed=False)


def open_stream(url: str) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        raise ConnectionError(f"Cannot open RTSP stream: {url}")
    return capture


def monitor_stream(args: argparse.Namespace) -> None:
    recorder = SegmentRecorder(args.output_dir, args.segment_seconds, args.keep_short_files)

    try:
        while True:
            try:
                print(f"[INFO] Opening RTSP stream: {args.url}")
                capture = open_stream(args.url)
                stream_fps = valid_fps(capture.get(cv2.CAP_PROP_FPS), args.fallback_fps)
                print(f"[INFO] Stream opened. Recording threshold: {args.segment_seconds:.0f}s")

                while True:
                    ok, frame = capture.read()
                    if not ok:
                        raise ConnectionError("RTSP frame read failed")

                    recorder.write(frame, stream_fps)

                    if args.display:
                        cv2.imshow("RTSP Realtime Monitor", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            return
            except KeyboardInterrupt:
                return
            except Exception as exc:
                print(f"[WARN] {exc}. Reconnecting in {args.reconnect_delay:.1f}s")
                recorder.finalize(completed=False)
                time.sleep(args.reconnect_delay)
            finally:
                try:
                    capture.release()  # type: ignore[name-defined]
                except Exception:
                    pass
    finally:
        recorder.close()
        cv2.destroyAllWindows()


def main() -> int:
    args = parse_args()
    monitor_stream(args)
    print("[INFO] Stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
