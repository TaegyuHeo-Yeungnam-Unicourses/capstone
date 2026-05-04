#!/usr/bin/env python3
"""Raspberry Pi RTSP publisher with local circular recording and backlog serving.

Main behavior:
    1. Publish live camera video through RTSP.
    2. Always write one-hour local video segments to the Raspberry Pi SD card.
    3. If both Wi-Fi and Ethernet are down, print a short stdout error message.
    4. When network connectivity returns, live RTSP remains the priority.
    5. Completed local segments are exposed through a small HTTP backlog server so
       the desktop can slowly download missed files without blocking live viewing.

Recommended topology:
    Raspberry Pi camera -> libcamera-vid/ffmpeg -> RTSP server -> desktop viewer
                                           |
                                           +-> local 1-hour circular segments

The local buffer is intentionally independent from network state. This means
recording can continue even while Wi-Fi and Ethernet are both unavailable.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import paho.mqtt.client as mqtt
import psutil


VIDEO_SUFFIXES = {".ts", ".mp4", ".avi", ".mkv"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish Raspberry Pi camera video as RTSP, record a circular local buffer, "
            "and serve completed backlog files to the desktop."
        )
    )
    parser.add_argument(
        "--source",
        choices=("libcamera", "v4l2"),
        default="libcamera",
        help="Camera capture backend. Use libcamera for Raspberry Pi Camera Module, v4l2 for USB webcam.",
    )
    parser.add_argument(
        "--device",
        default="/dev/video0",
        help="V4L2 device path used only when --source v4l2 is selected.",
    )
    parser.add_argument("--width", type=int, default=1280, help="Capture width.")
    parser.add_argument("--height", type=int, default=720, help="Capture height.")
    parser.add_argument("--fps", type=int, default=30, help="Capture frame rate.")
    parser.add_argument(
        "--bitrate",
        type=int,
        default=2_500_000,
        help="Target video bitrate in bits per second.",
    )
    parser.add_argument(
        "--rtsp-url",
        default="rtsp://127.0.0.1:8554/capstone",
        help="RTSP publish URL. Requires an RTSP server such as MediaMTX to be running.",
    )
    parser.add_argument(
        "--public-rtsp-url",
        default=None,
        help="URL that desktop clients should use. If omitted, --rtsp-url is reported in MQTT status.",
    )
    parser.add_argument(
        "--rtsp-transport",
        choices=("tcp", "udp"),
        default="tcp",
        help="RTSP transport used by ffmpeg while publishing.",
    )
    parser.add_argument(
        "--ffmpeg-loglevel",
        default="warning",
        help="ffmpeg log level. Examples: quiet, error, warning, info.",
    )
    parser.add_argument(
        "--local-buffer-dir",
        type=Path,
        default=Path("/home/pi/capstone_buffer"),
        help="Directory on the Raspberry Pi SD card for local circular video segments.",
    )
    parser.add_argument(
        "--segment-seconds",
        type=int,
        default=3600,
        help="Local recording segment duration. Default: 3600 seconds, i.e. 1 hour.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=4.0,
        help="Keep at least this much free space by deleting oldest completed segments.",
    )
    parser.add_argument(
        "--max-buffer-gb",
        type=float,
        default=32.0,
        help="Maximum total size of completed local buffer files before oldest files are deleted.",
    )
    parser.add_argument(
        "--completed-file-quiet-seconds",
        type=float,
        default=30.0,
        help="Only files unchanged for this many seconds are exposed as completed backlog files.",
    )
    parser.add_argument(
        "--backlog-host",
        default="0.0.0.0",
        help="HTTP backlog server bind address.",
    )
    parser.add_argument("--backlog-port", type=int, default=8080, help="HTTP backlog server port.")
    parser.add_argument(
        "--wifi-interface",
        default="wlan0",
        help="Wi-Fi interface name used for network-down detection.",
    )
    parser.add_argument(
        "--ethernet-interface",
        default="eth0",
        help="Ethernet interface name used for network-down detection.",
    )
    parser.add_argument(
        "--network-check-interval",
        type=float,
        default=2.0,
        help="Seconds between Wi-Fi/Ethernet state checks.",
    )
    parser.add_argument(
        "--mqtt-host",
        default=None,
        help="MQTT broker host. If omitted, MQTT status publishing is disabled.",
    )
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port.")
    parser.add_argument(
        "--mqtt-topic",
        default="capstone/raspi/status",
        help="MQTT topic for Raspberry Pi status JSON.",
    )
    parser.add_argument(
        "--mqtt-client-id",
        default="capstone-raspi-rtsp-publisher",
        help="MQTT client ID.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=5.0,
        help="Seconds between MQTT status messages.",
    )
    parser.add_argument(
        "--pipeline-restart-delay",
        type=float,
        default=5.0,
        help="Seconds to wait before restarting a failed camera/ffmpeg pipeline.",
    )
    return parser.parse_args()


def read_cpu_temperature_celsius() -> float | None:
    temp_file = Path("/sys/class/thermal/thermal_zone0/temp")
    if not temp_file.exists():
        return None

    try:
        return int(temp_file.read_text(encoding="utf-8").strip()) / 1000.0
    except (OSError, ValueError):
        return None


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    for items in psutil.net_if_addrs().values():
        for item in items:
            if item.family == socket.AF_INET and not item.address.startswith("127."):
                addresses.append(item.address)
    return addresses


def interface_has_ipv4(interface_name: str) -> bool:
    stats = psutil.net_if_stats().get(interface_name)
    if stats is None or not stats.isup:
        return False

    for item in psutil.net_if_addrs().get(interface_name, []):
        if item.family == socket.AF_INET and not item.address.startswith("127."):
            return True
    return False


def network_available(args: argparse.Namespace) -> bool:
    return interface_has_ipv4(args.wifi_interface) or interface_has_ipv4(args.ethernet_interface)


def completed_video_files(buffer_dir: Path, quiet_seconds: float) -> list[Path]:
    now = time.time()
    files: list[Path] = []
    if not buffer_dir.exists():
        return files

    for path in buffer_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if path.name.startswith(".") or ".tmp" in path.name:
            continue
        try:
            if now - path.stat().st_mtime >= quiet_seconds:
                files.append(path)
        except OSError:
            continue
    return sorted(files, key=lambda item: item.stat().st_mtime)


def buffer_total_size(files: list[Path]) -> int:
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def cleanup_circular_buffer(args: argparse.Namespace) -> None:
    args.local_buffer_dir.mkdir(parents=True, exist_ok=True)
    min_free_bytes = int(args.min_free_gb * 1024**3)
    max_buffer_bytes = int(args.max_buffer_gb * 1024**3)

    while True:
        files = completed_video_files(args.local_buffer_dir, args.completed_file_quiet_seconds)
        if not files:
            return

        usage = psutil.disk_usage(str(args.local_buffer_dir))
        total_size = buffer_total_size(files)
        if usage.free >= min_free_bytes and total_size <= max_buffer_bytes:
            return

        oldest = files[0]
        try:
            oldest.unlink()
            print(f"BUFFER_DELETE {oldest.name}", flush=True)
        except OSError as exc:
            print(f"BUFFER_DELETE_FAILED {oldest.name}: {exc}", flush=True)
            return


def build_status_payload(
    *,
    args: argparse.Namespace,
    started_at: float,
    publisher_alive: bool,
    capture_return_code: int | None,
    ffmpeg_return_code: int | None,
    net_available: bool,
) -> str:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(args.local_buffer_dir))
    public_url = args.public_rtsp_url or args.rtsp_url
    files = completed_video_files(args.local_buffer_dir, args.completed_file_quiet_seconds)

    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": socket.gethostname(),
        "ip_addresses": local_ipv4_addresses(),
        "wifi_interface": args.wifi_interface,
        "wifi_up": interface_has_ipv4(args.wifi_interface),
        "ethernet_interface": args.ethernet_interface,
        "ethernet_up": interface_has_ipv4(args.ethernet_interface),
        "network_available": net_available,
        "stream_type": "rtsp",
        "rtsp_url": public_url,
        "publish_url": args.rtsp_url,
        "source": args.source,
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "bitrate": args.bitrate,
        "publisher_alive": publisher_alive,
        "capture_return_code": capture_return_code,
        "ffmpeg_return_code": ffmpeg_return_code,
        "uptime_seconds": round(time.monotonic() - started_at, 1),
        "local_buffer_dir": str(args.local_buffer_dir),
        "local_segment_seconds": args.segment_seconds,
        "backlog_url": f"http://{socket.gethostname()}:{args.backlog_port}/manifest.json",
        "completed_backlog_files": len(files),
        "completed_backlog_bytes": buffer_total_size(files),
        "buffer_disk_free_bytes": disk.free,
        "buffer_disk_percent": disk.percent,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "cpu_temperature_celsius": read_cpu_temperature_celsius(),
    }
    return json.dumps(payload, ensure_ascii=False)


class MqttStatusPublisher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.enabled = args.mqtt_host is not None
        self.topic = args.mqtt_topic
        self.interval = args.status_interval
        self.last_published_at = 0.0
        self.client: mqtt.Client | None = None

        if not self.enabled:
            return

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.mqtt_client_id)
        self.client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
        self.client.loop_start()
        print(f"[INFO] MQTT status enabled: {args.mqtt_host}:{args.mqtt_port} topic={self.topic}")

    def publish_if_due(self, payload: str) -> None:
        if not self.enabled or self.client is None:
            return

        now = time.monotonic()
        if now - self.last_published_at < self.interval:
            return

        self.client.publish(self.topic, payload, qos=0, retain=False)
        self.last_published_at = now

    def close(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()


class BacklogRequestHandler(BaseHTTPRequestHandler):
    server: "BacklogHttpServer"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler uses this name.
        parsed = urlparse(self.path)
        if parsed.path == "/manifest.json":
            self.send_manifest()
            return
        if parsed.path.startswith("/files/"):
            filename = unquote(parsed.path.removeprefix("/files/"))
            self.send_file(filename)
            return
        self.send_error(404, "not found")

    def send_manifest(self) -> None:
        files = completed_video_files(self.server.buffer_dir, self.server.quiet_seconds)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "segment_seconds": self.server.segment_seconds,
            "files": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                    "url": f"/files/{path.name}",
                }
                for path in files
            ],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, filename: str) -> None:
        if "/" in filename or "\\" in filename or filename.startswith("."):
            self.send_error(400, "invalid filename")
            return

        path = self.server.buffer_dir / filename
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            self.send_error(404, "not found")
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            file_size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except OSError as exc:
            self.send_error(500, str(exc))


class BacklogHttpServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], args: argparse.Namespace) -> None:
        super().__init__(address, BacklogRequestHandler)
        self.buffer_dir = args.local_buffer_dir
        self.quiet_seconds = args.completed_file_quiet_seconds
        self.segment_seconds = args.segment_seconds


@dataclass
class PipelineProcesses:
    capture_process: subprocess.Popen[bytes] | None
    ffmpeg_process: subprocess.Popen[bytes]

    def poll_capture(self) -> int | None:
        if self.capture_process is None:
            return None
        return self.capture_process.poll()

    def poll_ffmpeg(self) -> int | None:
        return self.ffmpeg_process.poll()

    def alive(self) -> bool:
        capture_ok = self.capture_process is None or self.capture_process.poll() is None
        ffmpeg_ok = self.ffmpeg_process.poll() is None
        return capture_ok and ffmpeg_ok

    def stop(self) -> None:
        for process in (self.ffmpeg_process, self.capture_process):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)

        deadline = time.monotonic() + 5.0
        for process in (self.ffmpeg_process, self.capture_process):
            if process is None:
                continue
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if process.poll() is None:
                process.kill()


def tee_output_spec(args: argparse.Namespace) -> str:
    args.local_buffer_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = args.local_buffer_dir / "capstone_%Y%m%d_%H%M%S.ts"
    rtsp_output = f"[f=rtsp:rtsp_transport={args.rtsp_transport}:onfail=ignore]{args.rtsp_url}"
    local_output = (
        "[f=segment:segment_format=mpegts:segment_time="
        f"{args.segment_seconds}:reset_timestamps=1:strftime=1]"
        f"{segment_pattern}"
    )
    return f"{rtsp_output}|{local_output}"


def start_libcamera_pipeline(args: argparse.Namespace) -> PipelineProcesses:
    capture_command = [
        "libcamera-vid",
        "--timeout",
        "0",
        "--inline",
        "--nopreview",
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--framerate",
        str(args.fps),
        "--bitrate",
        str(args.bitrate),
        "--codec",
        "h264",
        "--output",
        "-",
    ]
    ffmpeg_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        args.ffmpeg_loglevel,
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-f",
        "h264",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-f",
        "tee",
        tee_output_spec(args),
    ]

    print("[INFO] Starting libcamera RTSP + local buffer pipeline")
    print("[INFO] " + " ".join(capture_command))
    print("[INFO] " + " ".join(ffmpeg_command))

    capture_process = subprocess.Popen(capture_command, stdout=subprocess.PIPE)
    if capture_process.stdout is None:
        raise RuntimeError("failed to open libcamera stdout pipe")

    ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=capture_process.stdout)
    capture_process.stdout.close()
    return PipelineProcesses(capture_process=capture_process, ffmpeg_process=ffmpeg_process)


def start_v4l2_pipeline(args: argparse.Namespace) -> PipelineProcesses:
    ffmpeg_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        args.ffmpeg_loglevel,
        "-f",
        "v4l2",
        "-framerate",
        str(args.fps),
        "-video_size",
        f"{args.width}x{args.height}",
        "-i",
        args.device,
        "-an",
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-b:v",
        str(args.bitrate),
        "-pix_fmt",
        "yuv420p",
        "-f",
        "tee",
        tee_output_spec(args),
    ]

    print("[INFO] Starting V4L2 RTSP + local buffer pipeline")
    print("[INFO] " + " ".join(ffmpeg_command))

    ffmpeg_process = subprocess.Popen(ffmpeg_command)
    return PipelineProcesses(capture_process=None, ffmpeg_process=ffmpeg_process)


def start_pipeline(args: argparse.Namespace) -> PipelineProcesses:
    if args.source == "libcamera":
        return start_libcamera_pipeline(args)
    return start_v4l2_pipeline(args)


def start_backlog_server(args: argparse.Namespace) -> BacklogHttpServer:
    server = BacklogHttpServer((args.backlog_host, args.backlog_port), args)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[INFO] Backlog server: http://{args.backlog_host}:{args.backlog_port}/manifest.json")
    return server


def run(args: argparse.Namespace) -> int:
    args.local_buffer_dir.mkdir(parents=True, exist_ok=True)
    status_publisher = MqttStatusPublisher(args)
    backlog_server = start_backlog_server(args)
    processes = start_pipeline(args)
    started_at = time.monotonic()
    public_url = args.public_rtsp_url or args.rtsp_url
    last_network_state = network_available(args)
    last_network_check_at = 0.0
    last_cleanup_at = 0.0
    last_restart_attempt_at = 0.0

    print(f"[INFO] RTSP publish URL: {args.rtsp_url}")
    print(f"[INFO] Desktop view URL: {public_url}")
    if not last_network_state:
        print("NETWORK_DOWN", flush=True)

    try:
        while True:
            now = time.monotonic()
            if now - last_network_check_at >= args.network_check_interval:
                current_network_state = network_available(args)
                if last_network_state and not current_network_state:
                    print("NETWORK_DOWN", flush=True)
                elif not last_network_state and current_network_state:
                    print("NETWORK_RESTORED", flush=True)
                    processes.stop()
                    processes = start_pipeline(args)
                    last_restart_attempt_at = now
                last_network_state = current_network_state
                last_network_check_at = now

            if now - last_cleanup_at >= 30.0:
                cleanup_circular_buffer(args)
                last_cleanup_at = now

            capture_rc = processes.poll_capture()
            ffmpeg_rc = processes.poll_ffmpeg()
            alive = processes.alive()

            if not alive and now - last_restart_attempt_at >= args.pipeline_restart_delay:
                print(
                    f"PIPELINE_RESTART capture={capture_rc} ffmpeg={ffmpeg_rc}",
                    flush=True,
                )
                processes.stop()
                processes = start_pipeline(args)
                last_restart_attempt_at = now
                alive = processes.alive()

            status_payload = build_status_payload(
                args=args,
                started_at=started_at,
                publisher_alive=alive,
                capture_return_code=capture_rc,
                ffmpeg_return_code=ffmpeg_rc,
                net_available=last_network_state,
            )
            status_publisher.publish_if_due(status_payload)
            time.sleep(1.0)
    finally:
        processes.stop()
        status_publisher.close()
        backlog_server.shutdown()
        backlog_server.server_close()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
        return 0
    except FileNotFoundError as exc:
        print(f"[ERROR] Required command not found: {exc.filename}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI should show a concise runtime error.
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
