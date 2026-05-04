# 코드 해석 보고서: Raspberry Pi 실시간 영상 전송 + MQTT 상태 전송 프로그램

## 1. 목적

이 프로그램의 목적은 라즈베리 파이에 연결된 카메라 영상을 **정지된 단일 이미지가 아니라 연속 프레임 기반 실시간 영상**으로 캡처하여 데스크톱 PC로 전송하는 것이다. 전송 네트워크는 Wi-Fi와 Ethernet을 모두 사용할 수 있으며, 프로그램은 두 방식을 별도로 구분하지 않는다. 운영체제는 두 네트워크 모두를 IP 기반 네트워크 인터페이스로 제공하므로, 애플리케이션은 라즈베리 파이의 IP 주소와 포트 번호만 사용하면 된다.

추가로 라즈베리 파이의 상태 정보는 MQTT로 전송한다. 영상 데이터는 대역폭이 크므로 TCP 스트림으로 전송하고, CPU 사용률·메모리 사용률·온도·IP 주소·연결 상태 같은 상태 정보는 MQTT 메시지로 분리한다.

## 2. 전체 구조

```text
raspi_video_sender.py
    1. 카메라 열기
    2. while True 루프에서 프레임 연속 캡처
    3. 각 프레임을 JPEG로 압축
    4. 프레임 크기 헤더 생성
    5. TCP로 데스크톱에 실시간 전송
    6. MQTT로 라즈베리 파이 상태 JSON 발행

          ↓ TCP 영상 스트림

desktop_video_receiver.py
    1. 라즈베리 파이 TCP 서버에 접속
    2. 4바이트 크기 헤더 수신
    3. JPEG 프레임 데이터 수신
    4. JPEG 복원
    5. 화면에 반복 출력

          ↓ MQTT 상태 메시지

MQTT Broker / Monitoring Client
    1. capstone/raspi/status 토픽 구독
    2. CPU, 메모리, 디스크, 온도, FPS, 연결 상태 확인
```

## 3. 실시간 영상 전송 방식

이 프로그램은 사진 한 장을 전송하고 종료하는 방식이 아니다. `send_frames()` 함수 내부에서 `while True` 반복문을 사용하여 카메라 프레임을 계속 읽고, 매 프레임마다 JPEG 압축과 TCP 전송을 수행한다.

핵심 흐름은 다음과 같다.

```python
while True:
    ok, frame = cap.read()
    encoded, buffer = cv2.imencode(".jpg", frame, encode_params)
    payload = buffer.tobytes()
    header = struct.pack("!I", len(payload))
    client.sendall(header + payload)
```

수신 프로그램도 같은 방식으로 `while True` 반복문을 사용한다. 프레임을 하나 받은 뒤 종료하지 않고 다음 프레임을 계속 수신하여 화면에 갱신한다.

## 4. 전송 프로토콜

이 코드는 복잡한 표준 스트리밍 프로토콜 대신 직접 정의한 간단한 프레임 프로토콜을 사용한다.

각 프레임은 다음 구조로 전송된다.

```text
[4바이트 프레임 크기][JPEG 데이터]
```

예를 들어 JPEG 프레임 크기가 20,000바이트라면, 송신자는 먼저 `20000`이라는 크기 정보를 4바이트 정수로 보내고, 이어서 실제 JPEG 데이터를 보낸다.

수신자가 크기 정보를 먼저 읽는 이유는 TCP가 메시지 단위가 아니라 바이트 스트림 단위로 동작하기 때문이다. TCP에서는 한 번의 `sendall()`로 보낸 데이터가 수신 측에서 한 번의 `recv()`로 정확히 분리되어 도착한다고 보장되지 않는다. 따라서 수신자는 먼저 몇 바이트를 읽어야 하는지 알아야 한다.

## 5. `raspi_video_sender.py` 해석

### 5.1 역할

`raspi_video_sender.py`는 라즈베리 파이에서 실행되는 송신 서버이다. 서버라고 부르는 이유는 데스크톱이 먼저 접속하는 것이 아니라, 라즈베리 파이가 특정 포트에서 접속을 기다리기 때문이다.

이 파일은 두 가지 역할을 동시에 수행한다.

1. TCP를 통한 실시간 영상 스트리밍
2. MQTT를 통한 라즈베리 파이 상태 정보 발행

### 5.2 주요 라이브러리

| 라이브러리 | 역할 |
|---|---|
| `argparse` | 실행 옵션 처리 |
| `json` | MQTT 상태 메시지를 JSON 문자열로 변환 |
| `socket` | TCP 네트워크 통신 |
| `struct` | 프레임 크기를 4바이트 바이너리 값으로 변환 |
| `time` | FPS 계산, 상태 발행 주기 제어 |
| `cv2` | 카메라 캡처와 JPEG 인코딩 |
| `psutil` | CPU, 메모리, 디스크, 네트워크 주소 정보 수집 |
| `paho.mqtt.client` | MQTT 브로커 접속 및 상태 메시지 발행 |

### 5.3 실행 옵션 처리

```python
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--camera", default="0")
parser.add_argument("--width", type=int, default=640)
parser.add_argument("--height", type=int, default=480)
parser.add_argument("--fps", type=int, default=15)
parser.add_argument("--quality", type=int, default=80)
parser.add_argument("--mqtt-host", default=None)
parser.add_argument("--mqtt-port", type=int, default=1883)
parser.add_argument("--mqtt-topic", default="capstone/raspi/status")
parser.add_argument("--status-interval", type=float, default=5.0)
```

이 옵션들은 코드 수정 없이 실행 조건을 바꾸기 위해 사용된다.

- `--host 0.0.0.0`: 모든 네트워크 인터페이스에서 접속을 허용한다.
- `--port 5000`: 데스크톱이 접속할 TCP 영상 스트리밍 포트이다.
- `--camera 0`: 기본 카메라 장치를 사용한다.
- `--width`, `--height`: 영상 해상도를 요청한다.
- `--fps`: 초당 프레임 수를 요청한다.
- `--quality`: JPEG 압축 품질을 지정한다.
- `--mqtt-host`: MQTT 브로커 주소이다. 이 값을 생략하면 MQTT는 비활성화된다.
- `--mqtt-topic`: 라즈베리 파이 상태 정보를 보낼 MQTT 토픽이다.
- `--status-interval`: 상태 정보를 몇 초마다 보낼지 결정한다.

### 5.4 카메라 인자 변환

```python
def camera_argument(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value
```

OpenCV의 `VideoCapture()`는 `0` 같은 정수 인덱스와 `/dev/video0` 같은 문자열 경로를 모두 받을 수 있다. 이 함수는 사용자가 `--camera 0`을 입력하면 정수 `0`으로 바꾸고, `/dev/video0`처럼 숫자가 아닌 값은 문자열 그대로 유지한다.

### 5.5 카메라 열기

```python
cap = cv2.VideoCapture(camera_argument(args.camera))
if not cap.isOpened():
    raise RuntimeError(...)
```

`cv2.VideoCapture()`는 카메라 장치를 여는 객체이다. `cap.isOpened()`가 `False`이면 카메라가 연결되지 않았거나, 권한 문제가 있거나, 잘못된 장치 번호를 사용한 것이다.

```python
cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
cap.set(cv2.CAP_PROP_FPS, args.fps)
```

이 부분은 카메라에 원하는 해상도와 FPS를 요청한다. 단, 실제 카메라가 해당 설정을 지원하지 않으면 요청값과 실제값이 다를 수 있다.

### 5.6 MQTT 상태 발행 객체

```python
class MqttStatusPublisher:
    def __init__(self, args):
        self.enabled = args.mqtt_host is not None
        ...
        self.client = mqtt.Client(...)
        self.client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
        self.client.loop_start()
```

`MqttStatusPublisher`는 MQTT 발행 기능을 담당하는 작은 래퍼 클래스이다. `--mqtt-host`가 지정된 경우에만 MQTT를 활성화한다. 지정하지 않으면 영상 스트리밍만 수행한다.

`loop_start()`를 호출하는 이유는 MQTT 클라이언트가 내부 네트워크 처리를 별도 스레드에서 수행하도록 하기 위해서이다. 이렇게 하면 영상 전송 루프가 MQTT 네트워크 처리 때문에 불필요하게 멈추는 상황을 줄일 수 있다.

### 5.7 상태 메시지 생성

```python
def build_status_payload(...):
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    payload = {
        "timestamp": ...,
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
```

이 함수는 라즈베리 파이의 상태를 JSON으로 변환한다. 포함되는 정보는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `timestamp` | 상태 메시지 생성 시각 |
| `hostname` | 라즈베리 파이 호스트 이름 |
| `ip_addresses` | 라즈베리 파이의 IPv4 주소 목록 |
| `video_host` | 영상 서버 바인드 주소 |
| `video_port` | 영상 서버 포트 |
| `client_connected` | 데스크톱 수신 클라이언트 연결 여부 |
| `frames_sent` | 현재 연결에서 전송한 프레임 수 |
| `fps_avg` | 평균 전송 FPS |
| `last_frame_bytes` | 마지막 JPEG 프레임 크기 |
| `cpu_percent` | CPU 사용률 |
| `memory_percent` | 메모리 사용률 |
| `disk_percent` | 루트 디스크 사용률 |
| `cpu_temperature_celsius` | CPU 온도. 읽을 수 없으면 `null` |

### 5.8 CPU 온도 읽기

```python
Path("/sys/class/thermal/thermal_zone0/temp")
```

라즈베리 파이 OS 계열 Linux에서는 CPU 온도를 `/sys/class/thermal/thermal_zone0/temp`에서 읽을 수 있는 경우가 많다. 값은 일반적으로 밀리섭씨 단위이므로 `1000`으로 나누어 섭씨 온도로 변환한다.

### 5.9 TCP 서버 생성

```python
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((args.host, args.port))
server.listen(1)
server.settimeout(1.0)
```

- `AF_INET`: IPv4 사용
- `SOCK_STREAM`: TCP 사용
- `SO_REUSEADDR`: 프로그램 재실행 시 포트 재사용 문제 완화
- `bind()`: 지정한 IP와 포트에 서버 연결
- `listen(1)`: 클라이언트 접속 대기
- `settimeout(1.0)`: 클라이언트가 아직 없어도 1초마다 깨어나 MQTT idle 상태를 발행할 수 있게 함

### 5.10 대기 중 상태 발행

```python
publish_idle_status(args, status_publisher)
```

데스크톱 클라이언트가 아직 접속하지 않은 상태에서도 MQTT를 통해 `client_connected: false` 상태를 보낼 수 있다. 이를 통해 모니터링 시스템은 라즈베리 파이 프로세스가 살아 있지만 데스크톱 수신부가 연결되지 않았다는 점을 구분할 수 있다.

### 5.11 프레임 캡처와 전송

```python
ok, frame = cap.read()
encoded, buffer = cv2.imencode(".jpg", frame, encode_params)
payload = buffer.tobytes()
header = struct.pack("!I", last_frame_bytes)
client.sendall(header + payload)
```

`cap.read()`는 카메라에서 한 프레임을 읽는다. 이 과정이 반복되므로 실시간 영상처럼 동작한다.

카메라 원본 프레임은 일반적으로 압축되지 않은 배열이다. 그대로 보내면 네트워크 대역폭을 많이 사용하므로 JPEG로 압축한다.

예를 들어 640×480 컬러 이미지는 원본 기준 약 921,600바이트이다.

```text
640 × 480 × 3 = 921,600 bytes
```

JPEG로 압축하면 장면과 품질 설정에 따라 훨씬 작은 크기로 줄어든다.

### 5.12 전송 중 상태 발행

```python
status_payload = build_status_payload(
    client_connected=True,
    frames_sent=frame_count,
    fps_avg=fps_avg,
    last_frame_bytes=last_frame_bytes,
    video_host=args.host,
    video_port=args.port,
)
status_publisher.publish_if_due(status_payload)
```

프레임 전송 중에도 주기적으로 MQTT 상태 메시지를 발행한다. 이때 `client_connected`는 `true`가 되고, `frames_sent`, `fps_avg`, `last_frame_bytes` 값이 실제 영상 송신 상태를 반영한다.

### 5.13 연결 종료 처리

```python
except (BrokenPipeError, ConnectionResetError):
    print("[WARN] Desktop disconnected...")
```

데스크톱 프로그램이 종료되거나 네트워크가 끊기면 송신 중 예외가 발생할 수 있다. 이 코드는 예외를 잡고 다시 새로운 데스크톱 접속을 기다린다.

## 6. `desktop_video_receiver.py` 해석

### 6.1 역할

`desktop_video_receiver.py`는 데스크톱에서 실행되는 수신 클라이언트이다. 라즈베리 파이의 IP 주소와 포트 번호로 접속한 뒤, 프레임을 계속 받아 화면에 표시한다.

### 6.2 주요 라이브러리

| 라이브러리 | 역할 |
|---|---|
| `argparse` | 실행 옵션 처리 |
| `socket` | TCP 접속 및 수신 |
| `struct` | 4바이트 크기 헤더 해석 |
| `time` | 재접속 대기 |
| `cv2` | JPEG 디코딩과 화면 표시 |
| `numpy` | 수신 바이트를 이미지 배열로 변환 |

### 6.3 정확한 바이트 수신 함수

```python
def recvall(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        packet = sock.recv(size - len(data))
        if not packet:
            raise ConnectionError("connection closed")
        data.extend(packet)
    return bytes(data)
```

이 함수는 지정한 크기만큼 정확히 받을 때까지 `recv()`를 반복한다. TCP에서는 데이터가 한 번에 모두 도착하지 않을 수 있기 때문에 이 함수가 필요하다.

### 6.4 프레임 수신

```python
header = recvall(sock, HEADER_SIZE)
frame_size = struct.unpack("!I", header)[0]
frame_data = recvall(sock, frame_size)
```

먼저 4바이트 헤더를 읽어 프레임 크기를 알아낸다. 그 다음 정확히 `frame_size`만큼 JPEG 데이터를 읽는다.

### 6.5 JPEG 디코딩과 화면 출력

```python
encoded_frame = np.frombuffer(frame_data, dtype=np.uint8)
frame = cv2.imdecode(encoded_frame, cv2.IMREAD_COLOR)
cv2.imshow("Raspberry Pi Realtime Video", frame)
```

`np.frombuffer()`는 수신한 바이트 데이터를 NumPy 배열로 해석한다. `cv2.imdecode()`는 JPEG 바이트 배열을 OpenCV 이미지 프레임으로 복원한다. 이 과정이 반복되므로 화면이 계속 갱신되고 실시간 영상처럼 표시된다.

## 7. Wi-Fi와 Ethernet의 차이

코드 관점에서는 Wi-Fi와 Ethernet 모두 IP 주소를 사용하는 네트워크이다. 따라서 같은 TCP 소켓 코드와 같은 MQTT 코드를 사용할 수 있다.

하지만 실제 성능 측면에서는 차이가 있다.

| 항목 | Wi-Fi | Ethernet |
|---|---|---|
| 설치 편의성 | 높음 | 케이블 필요 |
| 지연 안정성 | 환경 영향 큼 | 상대적으로 안정적 |
| 대역폭 안정성 | 거리, 벽, 간섭 영향 | 안정적 |
| 실시간 영상 적합성 | 가능하지만 변동 있음 | 더 적합 |
| MQTT 상태 전송 | 매우 적은 대역폭이라 보통 문제 적음 | 안정적 |

실시간 영상 처리 실험에서는 Ethernet이 더 안정적이며, 이동성과 설치 편의성이 중요하면 Wi-Fi를 사용할 수 있다.

## 8. MQTT를 영상과 분리한 이유

영상 데이터와 상태 데이터는 성격이 다르다.

| 데이터 | 특징 | 적합한 방식 |
|---|---|---|
| 영상 프레임 | 크고 연속적이며 지연에 민감 | TCP 스트림 또는 RTSP |
| 상태 정보 | 작고 주기적이며 여러 시스템이 구독 가능 | MQTT |

MQTT는 발행자와 구독자를 분리하는 pub/sub 구조이므로, 라즈베리 파이는 상태를 한 번 발행하고 여러 모니터링 도구가 같은 토픽을 구독할 수 있다.

## 9. 성능 조절 방법

영상 전송량은 대략 다음 요소에 의해 결정된다.

```text
전송량 ≈ 프레임 크기 × FPS
```

프레임 크기는 해상도, JPEG 품질, 화면 복잡도에 영향을 받는다.

성능이 부족하면 다음 순서로 조절한다.

1. `--quality` 값을 낮춘다.
2. `--width`, `--height`를 낮춘다.
3. `--fps`를 낮춘다.
4. Wi-Fi 대신 Ethernet을 사용한다.

예시:

```bash
python3 src/raspi_video_sender.py --width 320 --height 240 --fps 10 --quality 60
```

## 10. 설계상 장점

1. 정지 영상이 아니라 연속 프레임 스트림으로 동작한다.
2. Wi-Fi와 Ethernet을 모두 지원한다.
3. 영상 스트림과 상태 메시지를 분리하여 구조가 명확하다.
4. 라즈베리 파이에서는 압축과 송신만 수행하므로 데스크톱에서 더 무거운 AI 처리를 담당할 수 있다.
5. MQTT를 통해 외부 모니터링 시스템과 쉽게 연동할 수 있다.
6. 클라우드로 원본 영상을 보내지 않는 로컬 처리 구조로 확장하기 쉽다.

## 11. 설계상 한계

1. 표준 스트리밍 프로토콜이 아니므로 VLC나 NVR과 직접 호환되지 않는다.
2. 영상 TCP 전송에는 인증과 암호화가 없다.
3. MQTT도 기본 설정에서는 평문 통신이다.
4. 클라이언트 1대 연결만 고려한다.
5. 오디오 전송은 지원하지 않는다.
6. 네트워크 상태가 나쁘면 프레임 지연이 누적될 수 있다.

## 12. 향후 개선 방향

### 12.1 RTSP 도입

RTSP를 사용하면 VLC, FFmpeg, NVR 프로그램과 연동하기 쉽다. 다만 구현 복잡도는 현재 방식보다 높다.

### 12.2 MQTT 확장

현재는 라즈베리 파이의 상태 정보만 MQTT로 보낸다. 향후에는 다음 정보를 추가할 수 있다.

- 카메라 연결 오류
- 프레임 드롭 수
- 네트워크 인터페이스별 송수신량
- 배터리 또는 UPS 상태
- 데스크톱 AI 분석 결과 요약

### 12.3 데스크톱 AI 처리 연결

수신한 `frame`을 YOLO, OpenCV 객체 추적, 배경 차분 알고리즘에 바로 입력할 수 있다.

예시 구조:

```python
frame = receive_frame(sock)
result = ai_model.detect(frame)
```

### 12.4 보안 강화

실제 배포 환경에서는 다음이 필요하다.

- VPN 또는 내부망 사용
- TLS 암호화
- MQTT 사용자 인증
- 접속 인증
- 방화벽 규칙 제한
- 로그 기록

## 13. 결론

이 코드는 라즈베리 파이를 저전력 영상 캡처 노드로 사용하고, 데스크톱을 고성능 처리 장치로 사용하는 구조의 기본 골격이다. 현재 구현은 단순한 TCP-JPEG 연속 프레임 전송 방식이지만, 정지 이미지 전송이 아닌 실시간 영상 송출 원리, TCP 바이트 스트림 처리, 프레임 압축, 데스크톱 수신 구조, MQTT 상태 모니터링 구조를 이해하기에 적합하다.

종합설계 프로젝트에서는 이 구조를 기반으로 라즈베리 파이는 영상 입력·송신·상태 보고를 담당하고, 데스크톱은 객체 탐지, 이상 상황 판단, 저장, 알림 기능을 담당하도록 확장할 수 있다.
