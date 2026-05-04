#!/usr/bin/env python3
"""Desktop video receiver for the Raspberry Pi camera sender."""

from __future__ import annotations

import argparse
import socket
import struct
import time

import cv2
import numpy as np


HEADER_SIZE = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive Raspberry Pi camera frames.")
    parser.add_argument("--host", required=True, help="Raspberry Pi IP address")
    parser.add_argument("--port", type=int, default=5000, help="TCP port")
    parser.add_argument("--no-display", action="store_true", help="Do not open a video window")
    parser.add_argument("--reconnect-delay", type=float, default=2.0)
    return parser.parse_args()


def recvall(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        packet = sock.recv(size - len(data))
        if not packet:
            raise ConnectionError("connection closed")
        data.extend(packet)
    return bytes(data)


def receive_frame(sock: socket.socket):
    header = recvall(sock, HEADER_SIZE)
    frame_size = struct.unpack("!I", header)[0]
    frame_data = recvall(sock, frame_size)
    encoded_frame = np.frombuffer(frame_data, dtype=np.uint8)
    frame = cv2.imdecode(encoded_frame, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("invalid JPEG frame")
    return frame


def main() -> int:
    args = parse_args()

    while True:
        try:
            print(f"[INFO] Connecting to {args.host}:{args.port}")
            with socket.create_connection((args.host, args.port), timeout=5) as sock:
                sock.settimeout(None)
                print("[INFO] Connected. Press q to quit.")

                while True:
                    frame = receive_frame(sock)
                    if not args.no_display:
                        cv2.imshow("Raspberry Pi Realtime Video", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            cv2.destroyAllWindows()
                            return 0
        except KeyboardInterrupt:
            cv2.destroyAllWindows()
            print("\n[INFO] Stopped by user.")
            return 0
        except Exception as exc:
            print(f"[WARN] {exc}. Reconnecting in {args.reconnect_delay:.1f}s")
            time.sleep(args.reconnect_delay)


if __name__ == "__main__":
    raise SystemExit(main())
