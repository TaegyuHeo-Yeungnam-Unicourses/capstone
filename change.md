# 변경 사항 정리: TCP-JPEG 구조에서 RTSP 기반 구조로 전환

## 1. 변경 목적

기존 구현은 라즈베리 파이 Python 프로그램이 카메라 프레임을 OpenCV로 읽고, 각 프레임을 JPEG로 압축한 뒤, 자체 TCP 소켓 프로토콜로 데스크톱에 전송하는 구조였다.

이번 변경의 목적은 다음과 같다.

1. 영상 전송 방식을 자체 TCP-JPEG 방식에서 RTSP 기반 실시간 스트리밍 방식으로 변경한다.
2. 데스크톱에서 표준 RTSP URL을 통해 영상을 실시간 확인할 수 있게 한다.
3. 데스크톱에서 영상이 1시간 이상 연속 수신되었을 때 파일로 저장한다.
4. MQTT 상태 전송은 유지하되, RTSP 송출 상태와 RTSP URL 정보를 포함하도록 변경한다.

## 2. 변경 전 구조

```text
[Raspberry Pi Python Sender]
    - OpenCV VideoCapture
    - JPEG encode
    - 4-byte size header
    - TCP socket sendall
        |
        v
[Desktop Python Receiver]
    - TCP socket connect
    - 4-byte size header read
    - JPEG payload read
    - OpenCV imdecode
    - cv2.imshow
```

### 변경 전 특징

| 항목 | 내용 |
|---|---|
| 영상 프로토콜 | 자체 TCP-JPEG 프로토콜 |
| 프레임 단위 | `[4바이트 크기][JPEG 데이터]` |
| 수신 프로그램 | 전용 Python 수신 코드 필요 |
| 외부 도구 호환성 | VLC, NVR, 일반 RTSP 클라이언트와 직접 호환 어려움 |
| 저장 기능 | 없음 |
| MQTT | 라즈베리 파이 상태 정보 발행 |

## 3. 변경 후 구조

```text
[Raspberry Pi Camera]
        |
        | libcamera-vid 또는 ffmpeg
        v
[RTSP Server]
        |
        | rtsp://<raspberry-pi-ip>:8554/capstone
        v
[Desktop RTSP Viewer/Recorder]

[Raspberry Pi Status]
        |
        | MQTT JSON
        v
[MQTT Broker / Monitor]
```

### 변경 후 특징

| 항목 | 내용 |
|---|---|
| 영상 프로토콜 | RTSP |
| 영상 코덱 | H.264 중심 |
| 라즈베리 파이 송신 | `libcamera-vid` + `ffmpeg` 또는 `ffmpeg` V4L2 |
| 데스크톱 수신 | OpenCV가 RTSP URL을 직접 열어 수신 |
| 외부 도구 호환성 | VLC, FFmpeg, NVR, OpenCV와 연동 가능 |
| 저장 기능 | 1시간 이상 연속 수신 구간 파일 저장 |
| MQTT | RTSP URL, 송출 상태, 해상도, FPS, bitrate, 시스템 상태 발행 |

## 4. 변경된 파일 목록

| 파일 | 변경 내용 |
|---|---|
| `src/raspi_video_sender.py` | TCP-JPEG 송신 코드를 제거하고 RTSP 발행 파이프라인 실행 코드로 변경 |
| `src/desktop_video_receiver.py` | TCP-JPEG 수신 코드를 제거하고 RTSP 실시간 표시 및 1시간 이상 저장 코드로 변경 |
| `README.md` | RTSP 서버 준비, RTSP 송출, 데스크톱 확인/저장 방법 중심으로 재작성 |
| `report.md` | RTSP 구조와 1시간 저장 로직 중심 설명 필요 |
| `change.md` | 이번 변경의 목적, 구조 변화, 실행 방식, 설계상 의미 정리 |

## 5. `raspi_video_sender.py` 변경 내용

### 5.1 기존 방식 제거

기존 코드는 다음 작업을 Python 내부에서 직접 수행했다.

```text
카메라 열기 → 프레임 읽기 → JPEG 압축 → TCP 전송
```

이 방식은 학습용으로 단순하지만, 표준 스트리밍 생태계와 직접 호환되지 않는다는 한계가 있었다.

### 5.2 RTSP 발행 방식으로 변경

새 코드는 Python이 직접 프레임을 전송하지 않고, 외부 명령을 실행하여 RTSP 서버로 스트림을 발행한다.

Raspberry Pi Camera Module 사용 시:

```text
libcamera-vid → H.264 stdout pipe → ffmpeg → RTSP server
```

USB 웹캠 사용 시:

```text
ffmpeg V4L2 input → H.264 encoding → RTSP server
```

### 5.3 주요 옵션 변화

| 옵션 | 의미 |
|---|---|
| `--source libcamera` | Raspberry Pi Camera Module 사용 |
| `--source v4l2` | USB 웹캠 사용 |
| `--device /dev/video0` | V4L2 장치 경로 |
| `--width` | 송출 해상도 가로 |
| `--height` | 송출 해상도 세로 |
| `--fps` | 송출 FPS |
| `--bitrate` | H.264 bitrate |
| `--rtsp-url` | RTSP 서버에 발행할 URL |
| `--public-rtsp-url` | 데스크톱이 접속할 URL |
| `--rtsp-transport` | RTSP 전송 방식. 기본값은 TCP |

### 5.4 MQTT 상태 정보 변경

기존 MQTT 상태 정보는 TCP 영상 포트, frame count, JPEG frame size 등을 중심으로 했다.

변경 후 MQTT 상태 정보는 RTSP 송출 상태 중심이다.

| 필드 | 의미 |
|---|---|
| `stream_type` | 현재 영상 스트림 방식. `rtsp` |
| `rtsp_url` | 데스크톱이 볼 수 있는 RTSP URL |
| `publish_url` | 라즈베리 파이가 RTSP 서버에 발행하는 URL |
| `source` | `libcamera` 또는 `v4l2` |
| `width`, `height` | 송출 해상도 |
| `fps` | 송출 FPS |
| `bitrate` | 송출 bitrate |
| `publisher_alive` | RTSP 발행 파이프라인 생존 여부 |
| `capture_return_code` | 캡처 프로세스 종료 코드 |
| `ffmpeg_return_code` | ffmpeg 프로세스 종료 코드 |
| `uptime_seconds` | 송출 프로그램 실행 시간 |
| `cpu_percent` | CPU 사용률 |
| `memory_percent` | 메모리 사용률 |
| `disk_percent` | 디스크 사용률 |
| `cpu_temperature_celsius` | CPU 온도 |

## 6. `desktop_video_receiver.py` 변경 내용

### 6.1 기존 방식 제거

기존 데스크톱 코드는 라즈베리 파이 TCP 소켓에 직접 접속하고, 다음 순서로 프레임을 처리했다.

```text
TCP 연결 → 4바이트 크기 헤더 수신 → JPEG 데이터 수신 → JPEG 디코딩 → 화면 출력
```

### 6.2 RTSP 수신 방식으로 변경

새 코드는 RTSP URL을 OpenCV로 연다.

```python
capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
```

RTSP 스트림을 열고 다음 작업을 반복한다.

```text
RTSP 프레임 수신 → 화면 표시 → 임시 파일 기록 → 1시간 이상이면 파일 확정 저장
```

### 6.3 1시간 이상 저장 로직

요구사항은 “영상의 길이가 1시간 이상 전송되었을 때 파일로 저장”이다.

이를 위해 데스크톱 프로그램은 다음 방식으로 동작한다.

1. RTSP 스트림을 열면 임시 파일에 기록을 시작한다.
2. 연속 수신 시간이 `--segment-seconds` 이상이면 임시 파일을 정상 파일명으로 변경한다.
3. 기본 기준은 `3600`초이다.
4. 1시간 전에 스트림이 끊기면 기본적으로 임시 파일을 삭제한다.
5. `--keep-short-files` 옵션을 주면 1시간 미만 파일도 `short_` prefix로 보존한다.

### 6.4 저장 파일명

1시간 이상 저장된 파일은 다음 형식으로 저장된다.

```text
rtsp_<시작시각>_to_<종료시각>_3600s.avi
```

예시:

```text
recordings/rtsp_20260504_140000_to_20260504_150000_3600s.avi
```

## 7. 실행 방식 변화

### 7.1 RTSP 서버 실행 필요

RTSP 방식에서는 중간에 RTSP 서버가 필요하다. 예를 들어 MediaMTX를 사용할 수 있다.

기본 URL 예시:

```text
rtsp://<라즈베리파이 IP>:8554/capstone
```

### 7.2 라즈베리 파이 송출 실행

Raspberry Pi Camera Module 기준:

```bash
python3 src/raspi_video_sender.py \
  --source libcamera \
  --width 1280 \
  --height 720 \
  --fps 30 \
  --bitrate 2500000 \
  --rtsp-url rtsp://127.0.0.1:8554/capstone \
  --public-rtsp-url rtsp://192.168.0.23:8554/capstone \
  --mqtt-host 192.168.0.10
```

### 7.3 데스크톱 수신 및 저장 실행

```bash
python3 src/desktop_video_receiver.py \
  --url rtsp://192.168.0.23:8554/capstone \
  --output-dir recordings \
  --segment-seconds 3600
```

테스트용으로 1시간을 기다리지 않으려면 다음처럼 짧게 지정할 수 있다.

```bash
python3 src/desktop_video_receiver.py \
  --url rtsp://192.168.0.23:8554/capstone \
  --segment-seconds 30
```

## 8. 설계상 의미

### 8.1 표준 프로토콜 사용

RTSP는 CCTV, NVR, VLC, FFmpeg, OpenCV 등에서 널리 지원되는 영상 스트리밍 프로토콜이다. 따라서 전용 Python 수신기만 사용하는 구조보다 확장성이 높다.

### 8.2 라즈베리 파이 부담 감소

Python이 프레임을 직접 읽고 JPEG 압축을 반복하는 방식보다, 카메라/인코더 파이프라인과 FFmpeg를 활용하는 방식이 영상 송출 구조에 더 적합하다.

### 8.3 데스크톱 중심 저장

영상 파일 저장은 데스크톱에서 수행한다. 이 구조는 라즈베리 파이를 영상 입력·송출 노드로 단순화하고, 저장·분석·AI 처리를 데스크톱에서 담당하게 만든다.

### 8.4 MQTT와 영상 스트림 분리

영상은 RTSP, 상태는 MQTT로 분리했다. 영상은 대역폭이 크고 연속성이 중요하며, 상태 정보는 작고 주기적인 메시지이기 때문이다.

## 9. 주의 사항

1. RTSP 서버가 먼저 실행되어 있어야 한다.
2. `--rtsp-url`은 라즈베리 파이가 RTSP 서버에 발행하는 주소이다.
3. `--public-rtsp-url`은 데스크톱이 접속할 주소이다.
4. 라즈베리 파이에서 RTSP 서버를 실행한다면 publish URL은 `127.0.0.1`, desktop URL은 라즈베리 파이 실제 IP를 사용한다.
5. 1시간 저장 기준은 라즈베리 파이 송출 시간이 아니라 데스크톱이 실제로 연속 수신한 시간이다.
6. 기본 저장 확장자는 `.avi`이며, OpenCV/운영체제 코덱 지원에 따라 결과 파일 재생 호환성이 달라질 수 있다.

## 10. 향후 개선 방향

1. MediaMTX 설정 파일 추가
2. 라즈베리 파이 systemd 서비스 등록
3. 데스크톱 recorder를 FFmpeg subprocess 기반 MP4 저장 방식으로 개선
4. MQTT 인증과 TLS 적용
5. RTSP 인증 적용
6. YOLO/OpenCV 기반 객체 탐지 연결
7. 1시간 저장 파일에 날짜별 디렉터리 자동 분류 추가
