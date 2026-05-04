# 코드 해석 보고서: Raspberry Pi 실시간 영상 전송 프로그램

## 1. 목적

이 프로그램의 목적은 라즈베리 파이에 연결된 카메라 영상을 실시간으로 캡처하여 데스크톱 PC로 전송하는 것이다. 전송 네트워크는 Wi-Fi와 Ethernet을 모두 사용할 수 있으며, 프로그램은 두 방식을 별도로 구분하지 않는다. 운영체제는 두 네트워크 모두를 IP 기반 네트워크 인터페이스로 제공하므로, 애플리케이션은 라즈베리 파이의 IP 주소와 포트 번호만 사용하면 된다.

## 2. 전체 구조

```text
raspi_video_sender.py
    1. 카메라 열기
    2. 프레임 캡처
    3. JPEG 압축
    4. 프레임 크기 헤더 생성
    5. TCP로 데스크톱에 전송

          ↓ 네트워크

desktop_video_receiver.py
    1. 라즈베리 파이 TCP 서버에 접속
    2. 4바이트 크기 헤더 수신
    3. JPEG 프레임 데이터 수신
    4. JPEG 복원
    5. 화면에 출력
```

## 3. 전송 프로토콜

이 코드는 복잡한 표준 스트리밍 프로토콜 대신 직접 정의한 간단한 프레임 프로토콜을 사용한다.

각 프레임은 다음 구조로 전송된다.

```text
[4바이트 프레임 크기][JPEG 데이터]
```

예를 들어 JPEG 프레임 크기가 20,000바이트라면, 송신자는 먼저 `20000`이라는 크기 정보를 4바이트 정수로 보내고, 이어서 실제 JPEG 데이터를 보낸다.

수신자가 크기 정보를 먼저 읽는 이유는 TCP가 메시지 단위가 아니라 바이트 스트림 단위로 동작하기 때문이다. TCP에서는 한 번의 `sendall()`로 보낸 데이터가 수신 측에서 한 번의 `recv()`로 정확히 분리되어 도착한다고 보장되지 않는다. 따라서 수신자는 먼저 몇 바이트를 읽어야 하는지 알아야 한다.

## 4. `raspi_video_sender.py` 해석

### 4.1 역할

`raspi_video_sender.py`는 라즈베리 파이에서 실행되는 송신 서버이다. 서버라고 부르는 이유는 데스크톱이 먼저 접속하는 것이 아니라, 라즈베리 파이가 특정 포트에서 접속을 기다리기 때문이다.

### 4.2 주요 라이브러리

| 라이브러리 | 역할 |
|---|---|
| `argparse` | 실행 옵션 처리 |
| `socket` | TCP 네트워크 통신 |
| `struct` | 프레임 크기를 4바이트 바이너리 값으로 변환 |
| `time` | FPS 로그 계산 및 대기 |
| `cv2` | 카메라 캡처와 JPEG 인코딩 |

### 4.3 실행 옵션 처리

```python
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--camera", default="0")
parser.add_argument("--width", type=int, default=640)
parser.add_argument("--height", type=int, default=480)
parser.add_argument("--fps", type=int, default=15)
parser.add_argument("--quality", type=int, default=80)
```

이 옵션들은 코드 수정 없이 실행 조건을 바꾸기 위해 사용된다.

- `--host 0.0.0.0`: 모든 네트워크 인터페이스에서 접속을 허용한다.
- `--port 5000`: 데스크톱이 접속할 TCP 포트이다.
- `--camera 0`: 기본 카메라 장치를 사용한다.
- `--width`, `--height`: 영상 해상도를 요청한다.
- `--fps`: 초당 프레임 수를 요청한다.
- `--quality`: JPEG 압축 품질을 지정한다.

### 4.4 카메라 인자 변환

```python
def camera_argument(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value
```

OpenCV의 `VideoCapture()`는 `0` 같은 정수 인덱스와 `/dev/video0` 같은 문자열 경로를 모두 받을 수 있다. 이 함수는 사용자가 `--camera 0`을 입력하면 정수 `0`으로 바꾸고, `/dev/video0`처럼 숫자가 아닌 값은 문자열 그대로 유지한다.

### 4.5 카메라 열기

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

### 4.6 TCP 서버 생성

```python
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((args.host, args.port))
server.listen(1)
```

- `AF_INET`: IPv4 사용
- `SOCK_STREAM`: TCP 사용
- `SO_REUSEADDR`: 프로그램 재실행 시 포트 재사용 문제 완화
- `bind()`: 지정한 IP와 포트에 서버 연결
- `listen(1)`: 클라이언트 접속 대기

### 4.7 클라이언트 접속 처리

```python
client, address = server.accept()
```

`accept()`는 데스크톱 클라이언트가 접속할 때까지 대기한다. 접속이 들어오면 실제 통신에 사용할 `client` 소켓과 접속한 주소 `address`를 반환한다.

### 4.8 프레임 캡처

```python
ok, frame = cap.read()
```

`cap.read()`는 카메라에서 한 프레임을 읽는다.

- `ok`: 프레임 읽기 성공 여부
- `frame`: 실제 이미지 데이터

실패하면 경고 메시지를 출력하고 잠시 대기한 뒤 다음 프레임을 다시 시도한다.

### 4.9 JPEG 인코딩

```python
encoded, buffer = cv2.imencode(".jpg", frame, encode_params)
payload = buffer.tobytes()
```

카메라 원본 프레임은 일반적으로 압축되지 않은 배열이다. 그대로 보내면 네트워크 대역폭을 많이 사용하므로 JPEG로 압축한다.

예를 들어 640×480 컬러 이미지는 원본 기준 약 921,600바이트이다.

```text
640 × 480 × 3 = 921,600 bytes
```

JPEG로 압축하면 장면과 품질 설정에 따라 훨씬 작은 크기로 줄어든다.

### 4.10 4바이트 헤더 생성

```python
header = struct.pack("!I", len(payload))
```

`struct.pack("!I", 값)`은 정수를 4바이트 unsigned integer로 변환한다.

- `!`: 네트워크 바이트 순서, 즉 big-endian
- `I`: 4바이트 unsigned integer

수신 측도 같은 형식으로 `struct.unpack("!I", header)`를 사용하므로, 송신자와 수신자가 같은 규칙으로 프레임 크기를 해석한다.

### 4.11 데이터 전송

```python
client.sendall(header + payload)
```

`sendall()`은 주어진 바이트 데이터를 가능한 한 모두 보낼 때까지 반복 전송한다. 단순 `send()`는 일부만 보낼 수 있으므로, 프레임 단위 데이터 전송에서는 `sendall()`이 더 안전하다.

### 4.12 연결 종료 처리

```python
except (BrokenPipeError, ConnectionResetError):
    print("[WARN] Desktop disconnected...")
```

데스크톱 프로그램이 종료되거나 네트워크가 끊기면 송신 중 예외가 발생할 수 있다. 이 코드는 예외를 잡고 다시 새로운 데스크톱 접속을 기다린다.

## 5. `desktop_video_receiver.py` 해석

### 5.1 역할

`desktop_video_receiver.py`는 데스크톱에서 실행되는 수신 클라이언트이다. 라즈베리 파이의 IP 주소와 포트 번호로 접속한 뒤, 프레임을 계속 받아 화면에 표시한다.

### 5.2 주요 라이브러리

| 라이브러리 | 역할 |
|---|---|
| `argparse` | 실행 옵션 처리 |
| `socket` | TCP 접속 및 수신 |
| `struct` | 4바이트 크기 헤더 해석 |
| `time` | 재접속 대기 |
| `cv2` | JPEG 디코딩과 화면 표시 |
| `numpy` | 수신 바이트를 이미지 배열로 변환 |

### 5.3 실행 옵션

```python
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--no-display", action="store_true")
parser.add_argument("--reconnect-delay", type=float, default=2.0)
```

- `--host`: 라즈베리 파이 IP 주소이다. 필수값이다.
- `--port`: 송신 서버 포트이다.
- `--no-display`: GUI 창을 열지 않고 수신만 수행한다.
- `--reconnect-delay`: 연결 실패 후 재접속까지 기다리는 시간이다.

### 5.4 정확한 바이트 수신 함수

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

예를 들어 20,000바이트 프레임을 받아야 하는데 `recv()`가 8,000바이트만 반환할 수 있다. 이 경우 나머지 12,000바이트를 추가로 받아야 한다.

### 5.5 프레임 수신

```python
header = recvall(sock, HEADER_SIZE)
frame_size = struct.unpack("!I", header)[0]
frame_data = recvall(sock, frame_size)
```

먼저 4바이트 헤더를 읽어 프레임 크기를 알아낸다. 그 다음 정확히 `frame_size`만큼 JPEG 데이터를 읽는다.

### 5.6 JPEG 디코딩

```python
encoded_frame = np.frombuffer(frame_data, dtype=np.uint8)
frame = cv2.imdecode(encoded_frame, cv2.IMREAD_COLOR)
```

`np.frombuffer()`는 수신한 바이트 데이터를 NumPy 배열로 해석한다. `cv2.imdecode()`는 JPEG 바이트 배열을 OpenCV 이미지 프레임으로 복원한다.

### 5.7 화면 출력

```python
cv2.imshow("Raspberry Pi Realtime Video", frame)
if cv2.waitKey(1) & 0xFF == ord("q"):
    cv2.destroyAllWindows()
    return 0
```

`cv2.imshow()`는 프레임을 창에 표시한다. `cv2.waitKey(1)`은 키 입력을 확인하며, 사용자가 `q`를 누르면 프로그램을 종료한다.

### 5.8 재접속 구조

```python
except Exception as exc:
    print(f"[WARN] {exc}. Reconnecting...")
    time.sleep(args.reconnect_delay)
```

네트워크가 끊기거나 라즈베리 파이 송신 프로그램이 중단되면 예외가 발생한다. 수신 프로그램은 일정 시간 대기한 뒤 다시 접속을 시도한다.

## 6. Wi-Fi와 Ethernet의 차이

코드 관점에서는 Wi-Fi와 Ethernet 모두 IP 주소를 사용하는 네트워크이다. 따라서 같은 TCP 소켓 코드를 사용할 수 있다.

하지만 실제 성능 측면에서는 차이가 있다.

| 항목 | Wi-Fi | Ethernet |
|---|---|---|
| 설치 편의성 | 높음 | 케이블 필요 |
| 지연 안정성 | 환경 영향 큼 | 상대적으로 안정적 |
| 대역폭 안정성 | 거리, 벽, 간섭 영향 | 안정적 |
| 실시간 영상 적합성 | 가능하지만 변동 있음 | 더 적합 |

실시간 영상 처리 실험에서는 Ethernet이 더 안정적이며, 이동성과 설치 편의성이 중요하면 Wi-Fi를 사용할 수 있다.

## 7. 성능 조절 방법

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

## 8. 설계상 장점

1. 구조가 단순하여 학습용으로 이해하기 쉽다.
2. Wi-Fi와 Ethernet을 모두 지원한다.
3. 라즈베리 파이에서는 압축과 송신만 수행하므로 데스크톱에서 더 무거운 AI 처리를 담당할 수 있다.
4. 클라우드로 원본 영상을 보내지 않는 로컬 처리 구조로 확장하기 쉽다.

## 9. 설계상 한계

1. 표준 스트리밍 프로토콜이 아니므로 VLC나 NVR과 직접 호환되지 않는다.
2. 인증과 암호화가 없다.
3. 클라이언트 1대 연결만 고려한다.
4. 오디오 전송은 지원하지 않는다.
5. 네트워크 상태가 나쁘면 프레임 지연이 누적될 수 있다.

## 10. 향후 개선 방향

### 10.1 RTSP 도입

RTSP를 사용하면 VLC, FFmpeg, NVR 프로그램과 연동하기 쉽다. 다만 구현 복잡도는 현재 방식보다 높다.

### 10.2 MQTT 상태 전송

영상 스트림은 TCP 또는 RTSP로 보내고, 라즈베리 파이의 CPU 온도, 네트워크 상태, 카메라 상태 등은 MQTT로 따로 보낼 수 있다. 이렇게 하면 영상 데이터와 상태 데이터를 역할별로 분리할 수 있다.

### 10.3 데스크톱 AI 처리 연결

수신한 `frame`을 YOLO, OpenCV 객체 추적, 배경 차분 알고리즘에 바로 입력할 수 있다.

예시 구조:

```python
frame = receive_frame(sock)
result = ai_model.detect(frame)
```

### 10.4 보안 강화

실제 배포 환경에서는 다음이 필요하다.

- VPN 또는 내부망 사용
- TLS 암호화
- 접속 인증
- 방화벽 규칙 제한
- 로그 기록

## 11. 결론

이 코드는 라즈베리 파이를 저전력 영상 캡처 노드로 사용하고, 데스크톱을 고성능 처리 장치로 사용하는 구조의 기본 골격이다. 현재 구현은 단순한 TCP-JPEG 전송 방식이지만, 실시간 영상 전송 원리, TCP 바이트 스트림 처리, 프레임 압축, 데스크톱 수신 구조를 이해하기에 적합하다.

종합설계 프로젝트에서는 이 구조를 기반으로 라즈베리 파이는 영상 입력과 송신을 담당하고, 데스크톱은 객체 탐지, 이상 상황 판단, 저장, 알림 기능을 담당하도록 확장할 수 있다.
