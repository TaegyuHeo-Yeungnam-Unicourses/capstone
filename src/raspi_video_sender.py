#!/usr/bin/env python3
"""Raspberry Pi realtime camera sender with MQTT status publishing.

This program continuously captures frames from a Raspberry Pi camera or USB
camera, compresses every frame as JPEG, and streams the frames to one desktop
client through a length-prefixed TCP socket.

It can also publish Raspberry Pi status information to an MQTT broker. The
video stream and the status channel are intentionally separated:

- TCP socket: high-bandwidth realtime video frames
- MQTT: low-bandwidth device status telemetry
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from contextlib import closing
from pathlib import Path

import cv2
import psutil
import paho.mqtt.client as mqtt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream Raspberry Pi camera frames to a desktop over TCP and publish status over MQTT."
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address. Use 0.0.0.0 to listen on Wi-Fi and Ethernet interfaces.",
    )
    parser.add_argument("--port", type=int, default=5000, help="TCP video streaming port.")
    parser.add_argument(
        "--camera",
        default="0",
        help="OpenCV camera index or device path. Examples: 0, /dev/video0",
    )
    parser.add_argument("--width", type=int, default=640, help="Capture width.")
    parser.add_argument("--height", type=int, default=480, help="Capture height.")
    parser.add_argument("--fps", type=int, default=15, help="Requested capture FPS.")
    parser.add_argument(
        "--quality",
        type=int,
        default=80,
        choices=range(1, 101),
        metavar="1-100",
        help="JPEG quality. Lower values reduce bandwidth and image quality.",
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
        default="capstone-raspi-video-sender",
        help="MQTT client ID.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=5.0,
        help="Seconds between MQTT status messages.",
    )
    return parser.parse_args()


def camera_argument(value: str) -> int | str:
    """Return an int camera index when possible, otherwise a device path/string."""
    try:
        return int(value)
    except ValueError:
        return value


def open_camera(args: argparse.Namespace) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(camera_argument(args.camera))
    if not cap.isOpened():
        raise RuntimeError(
            f"Cannot open camera {args.camera!r}. Check the camera connection and permission."
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    return cap


def read_cpu_temperature_celsius() -> float | None:
    """Read Raspberry Pi CPU temperature when the Linux thermal file exists."""
    temp_file = Path("/sys/class/thermal/thermal_zone0/temp")
    if not temp_file.exists():
        return None

    try:
        return int(temp_file.read_text(encoding="utf-8").strip()) / 1000.0
    except (OSError, ValueError):
        return None


def local_ipv4_addresses() -> list[str]:
    """Return local non-loopback IPv4 addresses for status monitoring."""
    addresses: list[str] = []
    for items in psutil.net_if_addrs().values():
        for item in items:
            if item.family == socket.AF_INET and not item.address.startswith("127."):
                addresses.append(item.address)
    return addresses


def build_status_payload(
    *,
    client_connected: bool,
    frames_sent: int,
    fps_avg: float,
    last_frame_bytes: int,
    video_host: str,
    video_port: int,
) -> str:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": socket.gethostname(),
        "ip_addresses": local_ipv4_addresses(),
        "video_host": video_host,
        "video_port": video_port,
        "client_connected": client_connected,
        "frames_sent": frames_sent,
        "fps_avg": round(fps_avg, 2),
        "last_frame_bytes": last_frame_bytes,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "disk_percent": disk.percent,
        "cpu_temperature_celsius": read_cpu_temperature_celsius(),
    }
    return json.dumps(payload, ensure_ascii=False)


class MqttStatusPublisher:
    """Small wrapper that publishes Raspberry Pi status only when enabled."""

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


def send_frames(
    client: socket.socket,
    cap: cv2.VideoCapture,
    args: argparse.Namespace,
    status_publisher: MqttStatusPublisher,
) -> None:
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
    frame_count = 0
    last_frame_bytes = 0
    started_at = time.monotonic()

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[WARN] Failed to read a frame from camera.", file=sys.stderr)
            time.sleep(0.05)
            continue

        encoded, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not encoded:
            print("[WARN] Failed to JPEG-encode a frame.", file=sys.stderr)
            continue

        payload = buffer.tobytes()
        last_frame_bytes = len(payload)
        header = struct.pack("!I", last_frame_bytes)
        client.sendall(header + payload)

        frame_count += 1
        elapsed = max(time.monotonic() - started_at, 0.001)
        fps_avg = frame_count / elapsed

        status_payload = build_status_payload(
            client_connected=True,
            frames_sent=frame_count,
            fps_avg=fps_avg,
            last_frame_bytes=last_frame_bytes,
            video_host=args.host,
            video_port=args.port,
        )
        status_publisher.publish_if_due(status_payload)

        if frame_count % 60 == 0:
            print(f"[INFO] Sent {frame_count} realtime frames ({fps_avg:.1f} FPS avg)")


def publish_idle_status(args: argparse.Namespace, status_publisher: MqttStatusPublisher) -> None:
    status_payload = build_status_payload(
        client_connected=False,
        frames_sent=0,
        fps_avg=0.0,
        last_frame_bytes=0,
        video_host=args.host,
        video_port=args.port,
    )
    status_publisher.publish_if_due(status_payload)


def run_server(args: argparse.Namespace) -> None:
    cap = open_camera(args)
    status_publisher = MqttStatusPublisher(args)

    try:
        with closing(cap), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((args.host, args.port))
            server.listen(1)
            server.settimeout(1.0)
            print(f"[INFO] Realtime video server listening on {args.host}:{args.port}")
            print("[INFO] Run desktop_video_receiver.py from the desktop to connect.")

            while True:
                publish_idle_status(args, status_publisher)
                try:
                    client, address = server.accept()
                except TimeoutError:
                    continue
                except socket.timeout:
                    continue

                with client:
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    print(f"[INFO] Desktop connected: {address[0]}:{address[1]}")
                    try:
                        send_frames(client, cap, args, status_publisher)
                    except (BrokenPipeError, ConnectionResetError):
                        print("[WARN] Desktop disconnected. Waiting for a new connection...")
    finally:
        status_publisher.close()


def main() -> int:
    args = parse_args()
    try:
        run_server(args)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should show a concise runtime error.
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
