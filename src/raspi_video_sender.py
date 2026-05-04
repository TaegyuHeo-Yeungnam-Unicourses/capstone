#!/usr/bin/env python3
"""Raspberry Pi realtime camera sender.

This program captures frames from a Raspberry Pi camera or USB camera,
compresses each frame as JPEG, and sends the frame stream to one desktop
client through a length-prefixed TCP socket.

Network note:
    Wi-Fi and Ethernet do not require different application code. Both expose
    an IP address to the operating system, so the desktop only needs to connect
    to the Raspberry Pi IP address and TCP port.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from contextlib import closing

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send Raspberry Pi camera frames to a desktop over TCP."
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address. Use 0.0.0.0 to listen on Wi-Fi and Ethernet interfaces.",
    )
    parser.add_argument("--port", type=int, default=5000, help="TCP port to listen on.")
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


def send_frames(client: socket.socket, cap: cv2.VideoCapture, quality: int) -> None:
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    frame_count = 0
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
        header = struct.pack("!I", len(payload))
        client.sendall(header + payload)

        frame_count += 1
        if frame_count % 60 == 0:
            elapsed = max(time.monotonic() - started_at, 0.001)
            print(f"[INFO] Sent {frame_count} frames ({frame_count / elapsed:.1f} FPS avg)")


def run_server(args: argparse.Namespace) -> None:
    cap = open_camera(args)

    with closing(cap), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(1)
        print(f"[INFO] Listening on {args.host}:{args.port}")
        print("[INFO] Run desktop_video_receiver.py from the desktop to connect.")

        while True:
            client, address = server.accept()
            with client:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                print(f"[INFO] Desktop connected: {address[0]}:{address[1]}")
                try:
                    send_frames(client, cap, args.quality)
                except (BrokenPipeError, ConnectionResetError):
                    print("[WARN] Desktop disconnected. Waiting for a new connection...")


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
