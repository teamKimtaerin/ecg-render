# ECG Render Server Phase 2 빠른 시작 가이드

## 🚀 5분 만에 Phase 2 스트리밍 파이프라인 구동하기

### 1. 필수 요구사항 ✅

```bash
# Python 3.12+ 설치 확인
python --version

# Redis 설치 및 실행
brew install redis
redis-server

# 의존성 설치
pip install -r requirements.txt
playwright install chromium
```

### 2. 환경 변수 설정 (선택사항)

```bash
# 기본값으로 동작하지만, 필요시 설정
export REDIS_URL="redis://localhost:6379"
export S3_BUCKET="ecg-rendered-videos"
export MAX_CONCURRENT_JOBS="3"
export LOG_LEVEL="INFO"
```

### 3. Phase 2 시스템 구동 🎯

#### 방법 1: Celery Worker 모드 (권장)
```bash
# Terminal 1: Redis 서버 시작
redis-server

# Terminal 2: Celery Worker 시작
python celery_worker.py

# Terminal 3: Render Server 시작
python render_server.py --port 8090
```

#### 방법 2: Standalone 모드 (간단한 테스트)
```bash
# Terminal 1: Redis 서버 시작
redis-server

# Terminal 2: Standalone 서버 시작
python render_server.py --port 8090 --mode standalone
```

### 4. 동작 확인 ✅

```bash
# 서버 상태 확인
curl http://localhost:8090/health

# 큐 상태 확인
curl http://localhost:8090/queue/status

# Phase 2 통합 테스트 실행
python test_phase2_integration.py

# 실제 렌더링 테스트
python test_standalone_render.py
```

### 5. Phase 2 렌더링 작업 제출 📤

```bash
curl -X POST http://localhost:8090/render \
  -H "Content-Type: application/json" \
  -d '{
    "jobId": "test-phase2-001",
    "videoUrl": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
    "scenario": {
      "cues": [
        {
          "text": "Phase 2 스트리밍 테스트",
          "start": 1.0,
          "end": 3.0,
          "style": {
            "fontSize": "32px",
            "color": "white"
          }
        }
      ]
    },
    "options": {
      "width": 1280,
      "height": 720,
      "fps": 30
    },
    "callbackUrl": "http://your-api-server:8000/callback"
  }'
```

### 6. 작업 상태 모니터링 📊

```bash
# 작업 상태 확인 (Phase 2 metrics 포함)
curl http://localhost:8090/api/render/{job_id}/status
```

**응답 예시 (Phase 2 metrics 포함):**
```json
{
  "jobId": "test-phase2-001",
  "status": "processing",
  "progress": 45,
  "frames_processed": 450,
  "frames_dropped": 2,
  "drop_rate": 0.004,
  "memory_peak_mb": 1847.3,
  "memory_trend": "stable"
}
```

## 🔧 문제 해결

### Redis 연결 문제
```bash
# Redis 프로세스 확인
ps aux | grep redis

# Redis 재시작
brew services restart redis

# 포트 6379 사용 확인
lsof -i :6379
```

### Python 패키지 문제
```bash
# 최신 호환 버전 설치
pip install --upgrade celery redis aiohttp playwright torch
```

### GPU/CPU 모드 전환
```bash
# CPU 모드 (로컬 테스트)
python render_server.py --port 8090 --cpu-mode

# GPU 모드 (프로덕션)
python render_server.py --port 8090 --gpu-mode
```

## 📈 Phase 2 성능 지표

| 항목 | Phase 1 | Phase 2 | 개선율 |
|------|---------|---------|--------|
| **메모리/워커** | ~6GB | ~2GB | **-70%** |
| **동시 작업** | 3-4개 | 8-10개 | **+150%** |
| **Frame Drop** | 5-10% | <1% | **-90%** |
| **응답 속도** | DB 쿼리 | Redis 캐시 | **10x** |

## 🎯 API Server 통합

Phase 2는 기존 API Server와 완전 호환됩니다:

- ✅ **기존 엔드포인트**: `/render` 동일하게 사용
- ✅ **추가 메트릭**: Phase 2 metrics가 자동으로 포함
- ✅ **하위 호환성**: 기존 코드 수정 불필요
- ✅ **성능 향상**: 즉시 70% 메모리 절약 효과

## 💡 주요 Phase 2 기능

1. **스트리밍 파이프라인**: 디스크 I/O 없는 실시간 frame 처리
2. **메모리 최적화**: 동적 GC 및 OOM 예방
3. **백프레셔 관리**: 시스템 부하에 따른 자동 속도 조절
4. **실시간 메트릭**: frames_dropped, memory_usage, drop_rate 추적
5. **병렬 처리**: 4개 브라우저 인스턴스로 동시 렌더링

---

**🚀 이제 Phase 2 스트리밍 파이프라인이 준비되었습니다!**

문제가 발생하면 로그를 확인하거나 `curl http://localhost:8090/health`로 상태를 점검하세요.