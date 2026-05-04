#!/usr/bin/env python3
"""Raspberry Pi RTSP video publisher with MQTT status publishing.

This program no longer sends custom TCP-JPEG frames. It starts an external
camera/encoder pipeline and publishes an H.264 stream to an RTSP server.

Recommended topology:
    Raspberry Pi camera -> libcamera-vid/ffmpeg -> RTSP server -> desktop viewer

The RTSP server can run on the Raspberry Pi, the desktop, or another machine.
For a simple local-lab setup, run MediaMTX on the Raspberry Pi and publish to:
    rtsp://127.0.0.1:8554/capstone
Desktop clients then view:
    rtsp://<raspberry-pi-ip>:8554/capstone

MQTT is kept as a separate low-bandwidth status channel.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish Raspberry Pi camera video as RTSP and publish device status over MQTT."
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
        help="Target video bitrate in bits per second. Used by libcamera-vid and v4l2 ffmpeg encoding.",
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


def build_status_payload(
    *,
    args: argparse.Namespace,
    started_at: float,
    publisher_alive: bool,
    capture_return_code: int | None,
    ffmpeg_return_code: int | None,
) -> str:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    public_url = args.public_rtsp_url or args.rtsp_url

    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": socket.gethostname(),
        "ip_addresses": local_ipv4_addresses(),
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
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "disk_percent": disk.percent,
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
        "-c:v",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        args.rtsp_transport,
        args.rtsp_url,
    ]

    print("[INFO] Starting libcamera capture pipeline")
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
        "rtsp",
        "-rtsp_transport",
        args.rtsp_transport,
        args.rtsp_url,
    ]

    print("[INFO] Starting V4L2 RTSP pipeline")
    print("[INFO] " + " ".join(ffmpeg_command))

    ffmpeg_process = subprocess.Popen(ffmpeg_command)
    return PipelineProcesses(capture_process=None, ffmpeg_process=ffmpeg_process)


def start_pipeline(args: argparse.Namespace) -> PipelineProcesses:
    if args.source == "libcamera":
        return start_libcamera_pipeline(args)
    return start_v4l2_pipeline(args)


def run(args: argparse.Namespace) -> int:
    status_publisher = MqttStatusPublisher(args)
    processes = start_pipeline(args)
    started_at = time.monotonic()
    public_url = args.public_rtsp_url or args.rtsp_url

    print(f"[INFO] RTSP publish URL: {args.rtsp_url}")
    print(f"[INFO] Desktop view URL: {public_url}")

    try:
        while True:
            capture_rc = processes.poll_capture()
            ffmpeg_rc = processes.poll_ffmpeg()
            alive = processes.alive()

            status_payload = build_status_payload(
                args=args,
                started_at=started_at,
                publisher_alive=alive,
                capture_return_code=capture_rc,
                ffmpeg_return_code=ffmpeg_rc,
            )
            status_publisher.publish_if_due(status_payload)

            if not alive:
                print(
                    f"[ERROR] RTSP pipeline stopped. capture_return_code={capture_rc}, "
                    f"ffmpeg_return_code={ffmpeg_rc}",
                    file=sys.stderr,
                )
                return 1

            time.sleep(1.0)
    finally:
        processes.stop()
        status_publisher.close()


def main() -> int:
    args = parse_args()
    try:
        return run(args)
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
