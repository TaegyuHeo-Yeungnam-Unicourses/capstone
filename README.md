# Raspberry Pi RTSP Realtime Streaming + MQTT Status

이 저장소는 라즈베리 파이 카메라 영상을 RTSP 기반 실시간 스트림으로 송출하고, 데스크톱에서 해당 영상을 실시간 확인하며, 1시간 이상 연속 전송된 구간을 파일로 저장하는 예제입니다. MQTT는 라즈베리 파이 상태 정보를 전달하는 별도 저대역폭 채널로 유지합니다.

## 1. 구성 개요

```text
[Raspberry Pi + Camera]
        |
        | libcamera-vid or ffmpeg
        v
[RTSP Server: MediaMTX 등]
        |
        | rtsp://<raspberry-pi-ip>:8554/capstone
        v
[Desktop Viewer/Recorder]

[Raspberry Pi]
        |
        | MQTT status JSON
        v
[MQTT Broker / Monitoring Client]
```

## 2. 변경된 핵심 구조

이전 구현은 Python이 직접 JPEG 프레임을 TCP 소켓으로 전송하는 구조였습니다. 현재 구현은 RTSP 서버를 중심으로 동작합니다.

| 구분 | 이전 구조 | 현재 구조 |
|---|---|---|
| 영상 전송 | 자체 TCP-JPEG 프레임 프로토콜 | RTSP 기반 H.264 스트림 |
| 송신 주체 | Python OpenCV 루프 | `libcamera-vid` + `ffmpeg` 또는 `ffmpeg` V4L2 |
| 수신 주체 | Python TCP 클라이언트 | OpenCV/FFmpeg RTSP 클라이언트 |
| 호환성 | 전용 수신 코드 필요 | VLC, FFmpeg, NVR, OpenCV와 연동 가능 |
| 상태 전송 | MQTT 선택 기능 | MQTT 유지, RTSP URL과 송출 상태 포함 |
| 장시간 저장 | 없음 | 데스크톱에서 1시간 이상 연속 구간 저장 |

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
| `src/raspi_video_sender.py` | 라즈베리 파이에서 RTSP 서버로 카메라 영상을 발행하고 MQTT 상태 전송 |
| `src/desktop_video_receiver.py` | 데스크톱에서 RTSP 영상을 실시간 확인하고 1시간 이상 구간 저장 |
| `requirements.txt` | Python 의존성 목록 |
| `report.md` | 코드 구조와 동작 원리 해석 |
| `change.md` | TCP-JPEG에서 RTSP 구조로 변경된 내용 중심 설명 |

## 4. 준비물

### 하드웨어

- Raspberry Pi 4 또는 Raspberry Pi 5
- Raspberry Pi Camera Module 또는 USB 웹캠
- 데스크톱 PC 또는 노트북
- Wi-Fi 공유기 또는 Ethernet 케이블
- MQTT 브로커를 실행할 장치
- RTSP 서버를 실행할 장치. 실험에서는 Raspberry Pi에서 MediaMTX 실행 권장

### 소프트웨어

- Python 3.10 이상 권장
- OpenCV
- NumPy
- Paho MQTT
- psutil
- FFmpeg
- Raspberry Pi Camera Module 사용 시 `libcamera-vid`
- RTSP 서버. 예: MediaMTX
- MQTT 브로커. 예: Mosquitto

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

MQTT 상태 메시지는 RTSP URL, 송출 상태, 해상도, FPS, bitrate, CPU, 메모리, 디스크, 온도 정보를 포함합니다.

## 8. 라즈베리 파이 IP 확인

```bash
hostname -I
```

예시:

```text
192.168.0.23
```

## 9. 라즈베리 파이에서 RTSP 송출 실행

### 9.1 Raspberry Pi Camera Module 사용

MediaMTX가 라즈베리 파이에서 실행 중이고, 데스크톱에서 볼 URL이 `rtsp://192.168.0.23:8554/capstone`인 경우:

```bash
python3 src/raspi_video_sender.py \
  --source libcamera \
  --width 1280 \
  --height 720 \
  --fps 30 \
  --bitrate 2500000 \
  --rtsp-url rtsp://127.0.0.1:8554/capstone \
  --public-rtsp-url rtsp://192.168.0.23:8554/capstone \
  --mqtt-host 192.168.0.10 \
  --mqtt-topic capstone/raspi/status
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
  --mqtt-host 192.168.0.10
```

## 10. 데스크톱에서 실시간 확인 및 1시간 이상 저장

```bash
python3 src/desktop_video_receiver.py \
  --url rtsp://192.168.0.23:8554/capstone \
  --output-dir recordings \
  --segment-seconds 3600
```

동작 방식:

1. 데스크톱에서 RTSP 스트림을 실시간으로 표시합니다.
2. 동시에 임시 파일에 프레임을 기록합니다.
3. 연속 수신 시간이 `--segment-seconds` 이상이면 정상 녹화 파일로 확정 저장합니다.
4. 기본값은 `3600`초, 즉 1시간입니다.
5. 스트림이 1시간 전에 끊기면 기본적으로 해당 임시 파일은 삭제됩니다.
6. 짧은 파일도 남기려면 `--keep-short-files`를 사용합니다.

테스트 시에는 1시간을 기다리지 않도록 다음처럼 짧게 지정할 수 있습니다.

```bash
python3 src/desktop_video_receiver.py \
  --url rtsp://192.168.0.23:8554/capstone \
  --segment-seconds 30
```

## 11. Wi-Fi와 Ethernet

코드 관점에서는 Wi-Fi와 Ethernet 모두 RTSP URL을 통해 접속하므로 실행 명령은 동일합니다. 차이는 네트워크 품질입니다.

| 항목 | Wi-Fi | Ethernet |
|---|---|---|
| 설치 편의성 | 높음 | 케이블 필요 |
| 지연 안정성 | 환경 영향 큼 | 상대적으로 안정적 |
| 장시간 녹화 안정성 | 끊김 가능성 있음 | 더 적합 |
| RTSP 스트리밍 | 가능 | 권장 |
| MQTT 상태 전송 | 대역폭이 작아 보통 문제 적음 | 안정적 |

1시간 이상 연속 저장을 목표로 할 경우 Ethernet을 우선 권장합니다.

## 12. 문제 해결

### RTSP 화면이 열리지 않는 경우

확인 항목:

1. RTSP 서버가 실행 중인지 확인
2. 라즈베리 파이 송신 프로그램이 RTSP 서버에 정상 발행 중인지 확인
3. 데스크톱에서 `rtsp://<라즈베리파이 IP>:8554/capstone` 주소를 사용했는지 확인
4. 방화벽이 8554 포트를 차단하지 않는지 확인
5. VLC 또는 FFmpeg로 같은 URL을 열어 확인

### libcamera 명령을 찾을 수 없는 경우

Raspberry Pi Camera Module을 쓰는 경우 Raspberry Pi OS의 camera stack이 필요합니다. USB 웹캠이면 `--source v4l2`를 사용합니다.

### 영상이 느리거나 끊기는 경우

대역폭을 줄입니다.

```bash
python3 src/raspi_video_sender.py \
  --source libcamera \
  --width 640 \
  --height 480 \
  --fps 15 \
  --bitrate 1000000 \
  --rtsp-url rtsp://127.0.0.1:8554/capstone
```

### MQTT 상태 메시지가 안 보이는 경우

```bash
mosquitto_sub -h <MQTT 브로커 IP> -t capstone/raspi/status -v
```

확인 항목:

1. MQTT 브로커 실행 여부
2. `--mqtt-host` 주소
3. 1883 포트 접근 가능 여부
4. 발행 토픽과 구독 토픽 일치 여부

## 13. 현재 구현의 한계

- RTSP 서버는 별도로 실행되어야 합니다.
- 영상 RTSP 전송과 MQTT는 기본 설정에서 암호화되지 않습니다.
- 데스크톱 저장 코드는 OpenCV `VideoWriter` 기반이므로 코덱 지원은 운영체제와 OpenCV 빌드 환경에 영향을 받습니다.
- 파일 확정 기준은 데스크톱이 실제로 연속 수신한 시간입니다. 라즈베리 파이 송출 시간과 완전히 같지 않을 수 있습니다.

## 14. 확장 방향

- MediaMTX 설정 파일을 저장소에 추가
- systemd 서비스 파일 추가
- MQTT 인증/TLS 설정 추가
- 데스크톱 녹화 파일을 MP4/H.264로 저장하도록 FFmpeg 기반 recorder 추가
- YOLO/OpenCV 객체 탐지와 이벤트 기반 저장 기능 연결
