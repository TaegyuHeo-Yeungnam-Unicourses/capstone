# Raspberry Pi Realtime Video Streaming + MQTT Status

이 저장소는 라즈베리 파이에서 카메라 영상을 **정지 이미지가 아니라 연속 프레임 기반 실시간 영상 스트림**으로 캡처한 뒤, Wi-Fi 또는 Ethernet 네트워크를 통해 데스크톱으로 전송하는 예제입니다. 또한 MQTT를 통해 라즈베리 파이의 상태 정보를 주기적으로 발행할 수 있습니다.

## 1. 구성 개요

```text
[Raspberry Pi + Camera]
        |
        | TCP: realtime JPEG frame stream
        | MQTT: Raspberry Pi status JSON
        v
[Desktop PC / MQTT Broker / Monitoring Client]
```

- 라즈베리 파이: 카메라 프레임을 계속 캡처, JPEG 압축, TCP 실시간 전송
- 데스크톱: TCP 수신, JPEG 복원, OpenCV 창에 실시간 표시
- MQTT 브로커: 라즈베리 파이 상태 JSON 수신 및 중계
- 전송 방식: 길이 헤더 4바이트 + JPEG 프레임 데이터

Wi-Fi와 Ethernet은 프로그램 입장에서는 동일하게 IP 네트워크로 취급됩니다. 따라서 네트워크 종류를 바꾸더라도 코드 변경은 필요 없고, 데스크톱에서 접속할 라즈베리 파이의 IP 주소만 정확히 지정하면 됩니다.

## 2. 파일 구조

```text
capstone/
├── README.md
├── report.md
├── requirements.txt
└── src/
    ├── raspi_video_sender.py
    └── desktop_video_receiver.py
```

| 파일 | 역할 |
|---|---|
| `src/raspi_video_sender.py` | 라즈베리 파이에서 카메라 프레임을 연속 캡처하고 TCP로 데스크톱에 실시간 전송, MQTT 상태 발행 |
| `src/desktop_video_receiver.py` | 데스크톱에서 프레임을 연속 수신하고 화면에 출력 |
| `requirements.txt` | Python 의존성 목록 |
| `report.md` | 코드 구조와 동작 원리 해석 |

## 3. 준비물

### 하드웨어

- Raspberry Pi 4 또는 Raspberry Pi 5
- Raspberry Pi Camera Module 또는 USB 웹캠
- 데스크톱 PC 또는 노트북
- Wi-Fi 공유기 또는 Ethernet 케이블
- MQTT 브로커를 실행할 장치. 예: 데스크톱, 라즈베리 파이, 별도 서버

### 소프트웨어

- Python 3.10 이상 권장
- OpenCV
- NumPy
- Paho MQTT
- psutil
- Mosquitto 등 MQTT 브로커

## 4. 설치

라즈베리 파이와 데스크톱 양쪽에서 다음 명령을 실행합니다.

```bash
git clone https://github.com/TaegyuHeo-Yeungnam-Unicourses/capstone.git
cd capstone
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows 데스크톱에서는 가상환경 활성화 명령이 다릅니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 5. MQTT 브로커 준비

테스트용으로 Mosquitto를 사용할 수 있습니다.

### Debian / Raspberry Pi OS

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

### 상태 메시지 구독 테스트

MQTT 브로커가 `192.168.0.10`에서 실행 중이라면 다음처럼 구독합니다.

```bash
mosquitto_sub -h 192.168.0.10 -t capstone/raspi/status -v
```

수신 예시는 다음과 같습니다.

```json
{
  "timestamp": "2026-05-04T12:00:00+0900",
  "hostname": "raspberrypi",
  "ip_addresses": ["192.168.0.23"],
  "video_host": "0.0.0.0",
  "video_port": 5000,
  "client_connected": true,
  "frames_sent": 180,
  "fps_avg": 14.92,
  "last_frame_bytes": 35621,
  "cpu_percent": 31.5,
  "memory_percent": 42.7,
  "disk_percent": 18.4,
  "cpu_temperature_celsius": 54.2
}
```

## 6. 라즈베리 파이 IP 확인

라즈베리 파이에서 다음 명령으로 IP 주소를 확인합니다.

```bash
hostname -I
```

예시 출력:

```text
192.168.0.23
```

이 경우 데스크톱에서는 `--host 192.168.0.23`으로 접속합니다.

## 7. 실행 방법

### 7.1 라즈베리 파이에서 실시간 영상 송신만 실행

```bash
python3 src/raspi_video_sender.py --host 0.0.0.0 --port 5000 --camera 0 --width 640 --height 480 --fps 15 --quality 80
```

이 명령은 카메라에서 프레임을 계속 읽고, 각 프레임을 JPEG로 압축한 뒤 TCP로 데스크톱에 연속 전송합니다. 따라서 단일 정지 이미지를 보내는 방식이 아니라 실시간 영상 스트림 방식입니다.

### 7.2 라즈베리 파이에서 실시간 영상 + MQTT 상태 송신 실행

MQTT 브로커 IP가 `192.168.0.10`인 경우:

```bash
python3 src/raspi_video_sender.py \
  --host 0.0.0.0 \
  --port 5000 \
  --camera 0 \
  --width 640 \
  --height 480 \
  --fps 15 \
  --quality 80 \
  --mqtt-host 192.168.0.10 \
  --mqtt-port 1883 \
  --mqtt-topic capstone/raspi/status \
  --status-interval 5
```

주요 옵션:

| 옵션 | 기본값 | 의미 |
|---|---:|---|
| `--host` | `0.0.0.0` | 라즈베리 파이가 수신 대기할 주소. `0.0.0.0`은 모든 네트워크 인터페이스를 의미 |
| `--port` | `5000` | TCP 영상 스트리밍 포트 번호 |
| `--camera` | `0` | OpenCV 카메라 번호 또는 `/dev/video0` 같은 장치 경로 |
| `--width` | `640` | 영상 가로 해상도 |
| `--height` | `480` | 영상 세로 해상도 |
| `--fps` | `15` | 요청 FPS |
| `--quality` | `80` | JPEG 압축 품질. 낮추면 대역폭 사용량 감소 |
| `--mqtt-host` | 없음 | MQTT 브로커 주소. 생략하면 MQTT 비활성화 |
| `--mqtt-port` | `1883` | MQTT 브로커 포트 |
| `--mqtt-topic` | `capstone/raspi/status` | 상태 정보를 발행할 토픽 |
| `--status-interval` | `5.0` | 상태 정보 발행 주기, 초 단위 |

### 7.3 데스크톱에서 수신 클라이언트 실행

라즈베리 파이 IP가 `192.168.0.23`인 경우:

```bash
python3 src/desktop_video_receiver.py --host 192.168.0.23 --port 5000
```

Windows PowerShell에서는 다음과 같이 실행할 수 있습니다.

```powershell
python src/desktop_video_receiver.py --host 192.168.0.23 --port 5000
```

영상 창에서 `q`를 누르면 종료됩니다.

## 8. Wi-Fi 사용 시

1. 라즈베리 파이와 데스크톱을 같은 공유기에 연결합니다.
2. 라즈베리 파이에서 `hostname -I`로 IP를 확인합니다.
3. 라즈베리 파이에서 `raspi_video_sender.py`를 실행합니다.
4. 데스크톱에서 `desktop_video_receiver.py --host <라즈베리파이 IP>`를 실행합니다.
5. MQTT를 사용할 경우 MQTT 브로커 주소를 `--mqtt-host`에 지정합니다.

Wi-Fi는 설치가 편하지만 전파 간섭, 공유기 거리, 벽 구조에 따라 지연과 프레임 손실이 증가할 수 있습니다.

## 9. Ethernet 사용 시

### 공유기 또는 스위치를 사용하는 경우

1. 라즈베리 파이와 데스크톱을 같은 공유기 또는 스위치에 유선 LAN으로 연결합니다.
2. 라즈베리 파이 IP를 확인합니다.
3. Wi-Fi와 동일한 명령으로 실행합니다.

### 라즈베리 파이와 데스크톱을 직접 연결하는 경우

직접 연결에서는 DHCP가 없을 수 있으므로 고정 IP 설정이 필요할 수 있습니다.

예시:

- 라즈베리 파이: `192.168.10.2`
- 데스크톱: `192.168.10.1`
- 서브넷 마스크: `255.255.255.0`

데스크톱에서는 다음처럼 접속합니다.

```bash
python3 src/desktop_video_receiver.py --host 192.168.10.2 --port 5000
```

Ethernet은 Wi-Fi보다 지연과 끊김이 적고, 실시간 영상 전송 실험에서 더 안정적입니다.

## 10. 문제 해결

### 카메라가 열리지 않는 경우

```bash
ls /dev/video*
```

카메라 장치가 `/dev/video0`로 잡혔다면 다음처럼 실행합니다.

```bash
python3 src/raspi_video_sender.py --camera /dev/video0
```

### 데스크톱에서 접속이 안 되는 경우

확인 항목:

1. 라즈베리 파이와 데스크톱이 같은 네트워크에 있는지 확인
2. 라즈베리 파이 IP가 맞는지 확인
3. 라즈베리 파이 송신 프로그램이 먼저 실행되어 있는지 확인
4. 방화벽이 TCP 5000 포트를 차단하지 않는지 확인

라즈베리 파이에 ping 테스트:

```bash
ping <라즈베리파이 IP>
```

### MQTT 상태 메시지가 안 보이는 경우

확인 항목:

1. MQTT 브로커가 실행 중인지 확인
2. `--mqtt-host` 주소가 맞는지 확인
3. 라즈베리 파이에서 브로커의 1883 포트에 접근 가능한지 확인
4. 구독 토픽과 발행 토픽이 같은지 확인

```bash
mosquitto_sub -h <MQTT 브로커 IP> -t capstone/raspi/status -v
```

### 영상이 느리거나 끊기는 경우

다음 값을 낮춥니다.

```bash
python3 src/raspi_video_sender.py --width 320 --height 240 --fps 10 --quality 60
```

대역폭을 줄이는 순서:

1. JPEG 품질 낮추기
2. 해상도 낮추기
3. FPS 낮추기
4. Wi-Fi 대신 Ethernet 사용

## 11. 현재 구현의 한계

- 한 번에 데스크톱 클라이언트 1대만 연결하는 구조입니다.
- 영상 TCP 전송에는 암호화가 없으므로 외부망 노출에는 적합하지 않습니다.
- MQTT도 기본 설정에서는 평문 통신이므로 실제 배포 시 인증, TLS, VPN을 고려해야 합니다.
- RTSP, WebRTC, HLS 같은 표준 스트리밍 프로토콜은 사용하지 않습니다.
- 실험용 최소 구현이므로 실제 제품에서는 인증, 암호화, 재접속 정책, 로그 관리, 장애 복구가 추가되어야 합니다.

## 12. 확장 방향

- RTSP 서버 방식으로 변경하여 VLC, FFmpeg, NVR과 연동
- MQTT 상태 정보를 Home Assistant, Node-RED, Grafana 등과 연동
- YOLO/OpenCV 기반 객체 탐지를 데스크톱 수신부에 연결
- 영상 저장 기능과 이벤트 기반 녹화 기능 추가
- TLS 또는 VPN을 통한 보안 강화
