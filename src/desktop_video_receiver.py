#!/usr/bin/env python3
"""Desktop RTSP viewer, interruption-safe recorder, and backlog downloader.

Behavior:
    1. Open the live RTSP stream and display it in real time.
    2. Record live stream segments on the desktop.
    3. If the live stream is interrupted, immediately save the current file even
       when it is shorter than 1 hour.
    4. Print short stdout messages for live stream errors and recovery.
    5. After connectivity returns, keep live viewing as the priority and slowly
       download Raspberry Pi backlog files through HTTP.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import urlopen

import cv2


DEFAULT_SEGMENT_SECONDS = 3600.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="View RTSP in real time, save interrupted files, and slowly sync Raspberry Pi backlog."
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
        help="Directory where live desktop video files are stored.",
    )
    parser.add_argument(
        "--segment-seconds",
        type=float,
        default=DEFAULT_SEGMENT_SECONDS,
        help="Normal live segment duration. Default: 3600 seconds.",
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
        "--backlog-manifest-url",
        default=None,
        help="Raspberry Pi backlog manifest URL. Example: http://192.168.0.23:8080/manifest.json",
    )
    parser.add_argument(
        "--backlog-output-dir",
        type=Path,
        default=Path("raspi_backlog"),
        help="Directory where downloaded Raspberry Pi backlog files are stored.",
    )
    parser.add_argument(
        "--backlog-chunk-bytes",
        type=int,
        default=1024 * 256,
        help="Backlog download chunk size. Smaller values reduce live-stream interference.",
    )
    parser.add_argument(
        "--backlog-sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between backlog chunks so live RTSP remains the priority.",
    )
    parser.add_argument(
        "--backlog-check-interval",
        type=float,
        default=30.0,
        help="Seconds between backlog manifest checks after live stream recovery.",
    )
    return parser.parse_args()


def valid_fps(value: float, fallback: float) -> float:
    if value <= 1.0 or value > 240.0:
        return fallback
    return value


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class SegmentRecorder:
    """Write live frames and always preserve interruption fragments."""

    def __init__(self, output_dir: Path, segment_seconds: float) -> None:
        self.output_dir = output_dir
        self.segment_seconds = segment_seconds
        self.writer: cv2.VideoWriter | None = None
        self.temp_path: Path | None = None
        self.started_at_monotonic = 0.0
        self.started_at_label = ""
        self.frame_count = 0
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def start(self, frame, fps: float) -> None:
        height, width = frame.shape[:2]
        self.frame_count = 0
        self.started_at_monotonic = time.monotonic()
        self.started_at_label = timestamp()
        self.temp_path = self.output_dir / f"live_{self.started_at_label}.tmp.avi"

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        self.writer = cv2.VideoWriter(str(self.temp_path), fourcc, fps, (width, height))
        if not self.writer.isOpened():
            raise RuntimeError(f"Cannot open video writer: {self.temp_path}")

        print(f"RECORD_START {self.temp_path}", flush=True)

    def write(self, frame, fps: float) -> None:
        if self.writer is None:
            self.start(frame, fps)

        assert self.writer is not None
        self.writer.write(frame)
        self.frame_count += 1

        if self.elapsed_seconds >= self.segment_seconds:
            self.finalize(reason="complete")

    @property
    def elapsed_seconds(self) -> float:
        if self.writer is None:
            return 0.0
        return time.monotonic() - self.started_at_monotonic

    def finalize(self, *, reason: str) -> Path | None:
        if self.writer is None or self.temp_path is None:
            return None

        elapsed = time.monotonic() - self.started_at_monotonic
        end_label = timestamp()
        self.writer.release()
        self.writer = None

        prefix = "rtsp" if reason == "complete" else "interrupted"
        final_path = self.output_dir / (
            f"{prefix}_{self.started_at_label}_to_{end_label}_{int(elapsed)}s.avi"
        )
        self.temp_path.rename(final_path)
        print(f"RECORD_SAVED {final_path}", flush=True)

        self.temp_path = None
        self.started_at_monotonic = 0.0
        self.started_at_label = ""
        self.frame_count = 0
        return final_path

    def close(self) -> None:
        self.finalize(reason="closed")


class BacklogDownloader:
    """Slowly download Raspberry Pi local buffer files after live stream recovery."""

    def __init__(self, manifest_url: str | None, output_dir: Path, chunk_bytes: int, sleep_seconds: float) -> None:
        self.manifest_url = manifest_url
        self.output_dir = output_dir
        self.chunk_bytes = chunk_bytes
        self.sleep_seconds = sleep_seconds
        self.downloaded_names: set[str] = set()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for path in self.output_dir.iterdir():
            if path.is_file() and not path.name.endswith(".part"):
                self.downloaded_names.add(path.name)

    def enabled(self) -> bool:
        return self.manifest_url is not None

    def sync_once(self) -> None:
        if self.manifest_url is None:
            return

        try:
            with urlopen(self.manifest_url, timeout=5) as response:
                manifest = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            print(f"BACKLOG_ERROR {exc}", flush=True)
            return

        for item in manifest.get("files", []):
            name = item.get("name")
            relative_url = item.get("url")
            if not name or not relative_url or name in self.downloaded_names:
                continue
            self.download_file(name, urljoin(self.manifest_url, relative_url))

    def download_file(self, name: str, file_url: str) -> None:
        final_path = self.output_dir / name
        part_path = self.output_dir / f"{name}.part"

        if final_path.exists():
            self.downloaded_names.add(name)
            return

        print(f"BACKLOG_DOWNLOAD_START {name}", flush=True)
        try:
            with urlopen(file_url, timeout=10) as response, part_path.open("wb") as output:
                while True:
                    chunk = response.read(self.chunk_bytes)
                    if not chunk:
                        break
                    output.write(chunk)
                    output.flush()
                    time.sleep(self.sleep_seconds)
            part_path.rename(final_path)
            self.downloaded_names.add(name)
            print(f"BACKLOG_DOWNLOAD_DONE {name}", flush=True)
        except (OSError, URLError) as exc:
            print(f"BACKLOG_ERROR {name}: {exc}", flush=True)
            part_path.unlink(missing_ok=True)


def open_stream(url: str) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        raise ConnectionError(f"Cannot open RTSP stream: {url}")
    return capture


def monitor_stream(args: argparse.Namespace) -> None:
    recorder = SegmentRecorder(args.output_dir, args.segment_seconds)
    backlog = BacklogDownloader(
        args.backlog_manifest_url,
        args.backlog_output_dir,
        args.backlog_chunk_bytes,
        args.backlog_sleep_seconds,
    )
    was_connected = False
    last_backlog_check_at = 0.0

    try:
        while True:
            capture = None
            try:
                print(f"CONNECTING {args.url}", flush=True)
                capture = open_stream(args.url)
                stream_fps = valid_fps(capture.get(cv2.CAP_PROP_FPS), args.fallback_fps)
                if not was_connected:
                    print("CONNECTION_RESTORED", flush=True)
                was_connected = True

                while True:
                    ok, frame = capture.read()
                    if not ok:
                        raise ConnectionError("RTSP_LOST")

                    recorder.write(frame, stream_fps)

                    now = time.monotonic()
                    if backlog.enabled() and now - last_backlog_check_at >= args.backlog_check_interval:
                        backlog.sync_once()
                        last_backlog_check_at = now

                    if args.display:
                        cv2.imshow("RTSP Realtime Monitor", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            return
            except KeyboardInterrupt:
                return
            except Exception as exc:
                print(f"LIVE_ERROR {exc}", flush=True)
                recorder.finalize(reason="interrupted")
                was_connected = False
                time.sleep(args.reconnect_delay)
            finally:
                if capture is not None:
                    capture.release()
    finally:
        recorder.close()
        cv2.destroyAllWindows()


def main() -> int:
    args = parse_args()
    monitor_stream(args)
    print("STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
