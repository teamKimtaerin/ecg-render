# ECG GPU Render Server

GPU 기반 MotionText 비디오 렌더링 서버

## 📋 Overview

이 서버는 MotionText 시나리오를 기반으로 동영상에 자막을 렌더링하는 GPU 가속 서버입니다.
Playwright를 사용하여 브라우저 환경에서 렌더링하고, FFmpeg GPU 인코딩으로 최종 비디오를 생성합니다.

## 🚀 Features

- **GPU 가속 렌더링**: NVIDIA GPU를 활용한 고속 처리
- **Playwright 기반**: 브라우저 환경에서 정확한 MotionText 렌더링
- **Redis 작업 큐**: 비동기 작업 처리 및 큐 관리
- **S3 통합**: AWS S3를 통한 비디오 업로드/다운로드
- **실시간 콜백**: 렌더링 진행상황을 Backend로 실시간 전송

## 🏗️ Architecture

```
GPU Render Server (Port 8090)
├── render_server.py       # FastAPI 메인 서버
├── modules/
│   ├── queue.py          # Redis 작업 큐 관리
│   ├── worker.py         # 렌더링 워커
│   ├── callbacks.py      # Backend 콜백 서비스
│   └── ffmpeg.py         # GPU 비디오 인코딩
└── src/
    ├── s3.py             # AWS S3 서비스
    └── logger.py         # 로깅 설정
```

## 📡 API Endpoints

### 렌더링 작업 제출
```http
POST /api/render/process
{
  "job_id": "uuid",
  "video_url": "https://example.com/video.mp4",
  "scenario": {
    "version": "1.3",
    "cues": [
      {
        "start": 0,
        "end": 5,
        "text": "Hello World"
      }
    ]
  },
  "options": {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "quality": 90
  },
  "callback_url": "http://backend/api/render/callback"
}
```

### 작업 상태 확인
```http
GET /api/render/{job_id}/status
```

### 작업 취소
```http
POST /api/render/{job_id}/cancel
```

### 헬스체크
```http
GET /health
```

## 🔧 Installation & Setup

### Prerequisites
- Python 3.11+
- NVIDIA GPU (CUDA 11.8+)
- Redis
- FFmpeg with NVENC support

### Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Environment Variables
```bash
# .env
S3_BUCKET=ecg-rendered-videos
REDIS_URL=redis://localhost:6379
BACKEND_CALLBACK_URL=http://backend:8000
MAX_CONCURRENT_JOBS=3
AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
```

## 🚀 Running

### Local Development
```bash
python render_server.py --host 0.0.0.0 --port 8090
```

### Docker
```bash
# Build
docker build -t ecg-gpu-render .

# Run (requires nvidia-docker2)
docker run --gpus all -p 8090:8090 ecg-gpu-render
```

### Docker Compose
```bash
docker-compose up -d
```

## 🎮 GPU Requirements

### Recommended Instances
- **Development**: g4dn.xlarge (1x T4, 16GB VRAM)
- **Production**: p3.2xlarge (1x V100, 16GB VRAM)

### GPU Memory Usage
- ~3-4GB per concurrent rendering job
- Maximum 3 concurrent jobs recommended

## 📊 Performance

### Target Performance
- **1분 영상 (1080p)**: 15-20초 렌더링
- **5분 영상 (1080p)**: 60-90초 렌더링
- **GPU 사용률**: 70-85%

### Scaling
- 수직 스케일링: GPU 메모리에 따른 동시 작업 수 조정
- 수평 스케일링: 여러 인스턴스 + Redis 클러스터

## 🔍 Monitoring

### Health Check
```bash
curl http://localhost:8090/health
```

### Queue Status
```bash
curl http://localhost:8090/api/render/queue/status
```

### Logs
```bash
# 컨테이너 로그
docker logs -f ecg-gpu-render

# 로컬 실행 시
tail -f logs/render_server.log
```

## 🧪 Testing

### Basic Test
```bash
curl -X POST http://localhost:8090/api/render/process \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "test-001",
    "video_url": "https://example.com/test.mp4",
    "scenario": {"cues": []},
    "options": {},
    "callback_url": "http://localhost:8000/callback"
  }'
```

## 🐛 Troubleshooting

### Common Issues

**GPU 인식 불가**
```bash
# NVIDIA 드라이버 확인
nvidia-smi

# CUDA 설치 확인
nvcc --version
```

**메모리 부족 (OOM)**
- `MAX_CONCURRENT_JOBS` 값 감소
- 더 높은 VRAM GPU 사용

**Redis 연결 실패**
- Redis 서버 상태 확인: `redis-cli ping`
- `REDIS_URL` 환경변수 확인

## 📝 Callback Format

렌더링 진행상황과 결과는 다음 형식으로 콜백됩니다:

### Progress Update
```json
{
  "job_id": "uuid",
  "status": "processing",
  "progress": 45,
  "message": "Rendering frame 450/1000",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

### Completion
```json
{
  "job_id": "uuid",
  "status": "completed",
  "progress": 100,
  "download_url": "https://s3.amazonaws.com/rendered/output.mp4",
  "file_size": 15728640,
  "duration": 60.5,
  "message": "Rendering completed successfully",
  "timestamp": "2025-01-15T10:31:00Z"
}
```

### Error
```json
{
  "job_id": "uuid",
  "status": "failed",
  "error_message": "GPU memory exhausted",
  "error_code": "RENDER_ERROR",
  "timestamp": "2025-01-15T10:30:30Z"
}
```

## 🔐 Security

- S3 접근은 IAM 역할 또는 액세스 키 사용
- 콜백 URL은 내부 네트워크만 허용 권장
- 임시 파일은 처리 완료 후 자동 삭제

## 📄 License

Proprietary - ECG Platform