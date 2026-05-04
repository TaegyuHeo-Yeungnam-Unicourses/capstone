# Raspberry Pi RTSP Realtime Streaming + Offline Buffer + Backlog Sync

이 저장소는 라즈베리 파이 카메라 영상을 RTSP 기반 실시간 스트림으로 송출하고, Wi-Fi와 Ethernet이 모두 끊긴 경우에도 라즈베리 파이 OS가 설치된 SD카드의 여유 공간에 영상을 1시간 단위로 순환 저장하는 예제입니다. 연결이 복구되면 데스크톱은 실시간 RTSP 영상을 우선 수신하고, 라즈베리 파이에 저장된 백로그 영상을 HTTP로 조금씩 내려받습니다.

## 1. 구성 개요

```text
[Raspberry Pi + Camera]
        |
        | libcamera-vid or ffmpeg
        v
[ffmpeg tee]
        |                         |
        | RTSP live stream         | local 1-hour circular buffer
        v                         v
[RTSP Server]              [Raspberry Pi SD card]
        |                         |
        | live RTSP                | HTTP backlog server
        v                         v
[Desktop Viewer/Recorder]  [Desktop backlog downloader]

[Raspberry Pi]
        |
        | MQTT status JSON
        v
[MQTT Broker / Monitoring Client]
```

## 2. 핵심 동작

| 상황 | 동작 |
|---|---|
| 정상 연결 | 라즈베리 파이는 RTSP 실시간 송출과 SD카드 1시간 단위 로컬 저장을 동시에 수행 |
| Wi-Fi와 Ethernet 모두 끊김 | 라즈베리 파이는 표준출력에 `NETWORK_DOWN` 출력 후 SD카드 로컬 순환 저장 유지 |
| 연결 복구 | 라즈베리 파이는 표준출력에 `NETWORK_RESTORED` 출력 후 RTSP 파이프라인 재시작 |
| 데스크톱 수신 중 끊김 | 데스크톱은 표준출력에 `LIVE_ERROR ...` 출력, 현재 녹화 파일을 1시간 미만이어도 저장 |
| 데스크톱 연결 복구 | 데스크톱은 `CONNECTION_RESTORED` 출력 후 실시간 RTSP 수신을 우선 수행 |
| 복구 후 백로그 수신 | 데스크톱은 백그라운드 스레드에서 라즈베리 파이 저장 파일을 조금씩 다운로드 |

## 3. 파일 구조

```text
capstone/
├── README.md
├── change.md
├── report.md
├── requirements.txt
└── src/
    ├── raspi_video_sender.py
    └── desktop_video_receiver.py
```

| 파일 | 역할 |
|---|---|
| `src/raspi_video_sender.py` | RTSP 송출, 로컬 1시간 순환 저장, 네트워크 끊김 감지, HTTP 백로그 서버, MQTT 상태 전송 |
| `src/desktop_video_receiver.py` | RTSP 실시간 확인, 끊김 즉시 파일 저장, 복구 메시지 출력, 백로그 저속 다운로드 |
| `requirements.txt` | Python 의존성 목록 |
| `report.md` | 코드 구조와 동작 원리 해석 |
| `change.md` | 주요 변경 사항 정리 |

## 4. 준비물

- Raspberry Pi 4 또는 Raspberry Pi 5
- Raspberry Pi Camera Module 또는 USB 웹캠
- 데스크톱 PC 또는 노트북
- Wi-Fi 공유기 또는 Ethernet 케이블
- RTSP 서버. 예: MediaMTX
- MQTT 브로커. 예: Mosquitto
- FFmpeg
- Raspberry Pi Camera Module 사용 시 `libcamera-vid`

## 5. Python 의존성 설치

라즈베리 파이와 데스크톱 양쪽에서 다음 명령을 실행합니다.

```bash
git clone https://github.com/TaegyuHeo-Yeungnam-Unicourses/capstone.git
cd capstone
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows 데스크톱에서는 다음처럼 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 6. RTSP 서버 준비

RTSP는 스트림을 중계할 서버가 필요합니다. 테스트용으로 MediaMTX를 사용할 수 있습니다.

라즈베리 파이에서 MediaMTX를 실행하면 기본적으로 다음 URL 형태를 사용할 수 있습니다.

```text
rtsp://<라즈베리파이 IP>:8554/capstone
```

송신 프로그램은 로컬 RTSP 서버에 다음 URL로 발행합니다.

```text
rtsp://127.0.0.1:8554/capstone
```

데스크톱은 라즈베리 파이의 실제 IP 주소를 사용해 접속합니다.

```text
rtsp://192.168.0.23:8554/capstone
```

## 7. MQTT 브로커 준비

Debian / Raspberry Pi OS 기준:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

상태 메시지 구독 예시:

```bash
mosquitto_sub -h 192.168.0.10 -t capstone/raspi/status -v
```

## 8. 라즈베리 파이 IP 확인

```bash
hostname -I
```

예시:

```text
192.168.0.23
```

## 9. 라즈베리 파이에서 RTSP + 로컬 순환 저장 + 백로그 서버 실행

### 9.1 Raspberry Pi Camera Module 사용

```bash
python3 src/raspi_video_sender.py \
  --source libcamera \
  --width 1280 \
  --height 720 \
  --fps 30 \
  --bitrate 2500000 \
  --rtsp-url rtsp://127.0.0.1:8554/capstone \
  --public-rtsp-url rtsp://192.168.0.23:8554/capstone \
  --local-buffer-dir /home/pi/capstone_buffer \
  --segment-seconds 3600 \
  --min-free-gb 4 \
  --max-buffer-gb 32 \
  --backlog-host 0.0.0.0 \
  --backlog-port 8080 \
  --wifi-interface wlan0 \
  --ethernet-interface eth0 \
  --mqtt-host 192.168.0.10
```

### 9.2 USB 웹캠 사용

```bash
python3 src/raspi_video_sender.py \
  --source v4l2 \
  --device /dev/video0 \
  --width 1280 \
  --height 720 \
  --fps 30 \
  --bitrate 2500000 \
  --rtsp-url rtsp://127.0.0.1:8554/capstone \
  --public-rtsp-url rtsp://192.168.0.23:8554/capstone \
  --local-buffer-dir /home/pi/capstone_buffer \
  --segment-seconds 3600 \
  --backlog-port 8080
```

## 10. 라즈베리 파이 표준출력 메시지

| 메시지 | 의미 |
|---|---|
| `NETWORK_DOWN` | `wlan0`과 `eth0` 모두 IPv4 주소가 없거나 down 상태 |
| `NETWORK_RESTORED` | Wi-Fi 또는 Ethernet 중 하나 이상 복구됨 |
| `BUFFER_DELETE <file>` | SD카드 여유 공간 확보를 위해 가장 오래된 완료 영상 삭제 |
| `PIPELINE_RESTART ...` | ffmpeg/libcamera 파이프라인 장애 감지 후 재시작 |

## 11. 데스크톱에서 실시간 확인 + 끊김 파일 저장 + 백로그 수신

```bash
python3 src/desktop_video_receiver.py \
  --url rtsp://192.168.0.23:8554/capstone \
  --output-dir recordings \
  --segment-seconds 3600 \
  --backlog-manifest-url http://192.168.0.23:8080/manifest.json \
  --backlog-output-dir raspi_backlog \
  --backlog-chunk-bytes 262144 \
  --backlog-sleep-seconds 0.2
```

동작 방식:

1. RTSP 실시간 영상을 우선 표시합니다.
2. 실시간 영상은 데스크톱에도 파일로 기록됩니다.
3. 정상적으로 1시간이 지나면 `rtsp_...avi` 파일로 저장됩니다.
4. 중간에 끊기면 즉시 `interrupted_...avi` 파일로 저장됩니다.
5. 연결이 복구되면 `CONNECTION_RESTORED`를 출력합니다.
6. 라즈베리 파이 백로그 파일은 별도 백그라운드 스레드에서 천천히 다운로드됩니다.
7. 백로그 다운로드는 `raspi_backlog/`에 저장됩니다.

## 12. 데스크톱 표준출력 메시지

| 메시지 | 의미 |
|---|---|
| `CONNECTING <url>` | RTSP 연결 시도 |
| `CONNECTION_RESTORED` | RTSP 연결 성공 또는 복구 |
| `LIVE_ERROR <reason>` | 실시간 RTSP 수신 실패 |
| `RECORD_START <file>` | 데스크톱 녹화 시작 |
| `RECORD_SAVED <file>` | 1시간 완료 또는 끊김으로 인해 파일 저장 |
| `BACKLOG_DOWNLOAD_START <file>` | 라즈베리 파이 백로그 파일 다운로드 시작 |
| `BACKLOG_DOWNLOAD_DONE <file>` | 백로그 파일 다운로드 완료 |
| `BACKLOG_ERROR <reason>` | 백로그 manifest 조회 또는 파일 다운로드 실패 |

## 13. 백로그 manifest 확인

브라우저 또는 curl로 확인할 수 있습니다.

```bash
curl http://192.168.0.23:8080/manifest.json
```

응답 예시:

```json
{
  "generated_at": "2026-05-04T12:00:00+0900",
  "segment_seconds": 3600,
  "files": [
    {
      "name": "capstone_20260504_100000.ts",
      "size": 1122334455,
      "mtime": 1777860000.0,
      "url": "/files/capstone_20260504_100000.ts"
    }
  ]
}
```

## 14. Wi-Fi와 Ethernet 장애 처리

코드는 `--wifi-interface`와 `--ethernet-interface`로 지정된 인터페이스를 검사합니다. 기본값은 각각 `wlan0`, `eth0`입니다.

둘 다 IPv4 주소가 없거나 down 상태가 되면 라즈베리 파이는 다음을 출력합니다.

```text
NETWORK_DOWN
```

이 상태에서도 로컬 저장은 계속됩니다. 저장 파일은 `--local-buffer-dir`에 1시간 단위로 생성되고, `--min-free-gb`, `--max-buffer-gb` 조건에 따라 오래된 파일부터 삭제됩니다.

## 15. 주의 사항

- RTSP 서버는 별도로 실행되어야 합니다.
- RTSP와 HTTP 백로그 서버는 기본적으로 암호화되지 않습니다.
- SD카드 수명과 저장 공간을 고려해야 합니다.
- 1시간 단위 순환 저장은 완료된 segment 파일 기준입니다. 현재 쓰는 중인 파일은 백로그 manifest에 바로 노출하지 않습니다.
- 백로그 다운로드는 실시간 영상을 우선하기 위해 작은 chunk와 sleep을 사용합니다.
