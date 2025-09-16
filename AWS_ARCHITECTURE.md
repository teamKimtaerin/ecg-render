# AWS Architecture for ECG Render System

## 🏗️ 전체 AWS 아키텍처 개요

ECG Render System은 3개의 마이크로서비스로 구성된 분산 비디오 처리 시스템입니다:

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWS Cloud (ap-northeast-2)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    VPC (10.0.0.0/16)                     │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │                                                           │    │
│  │  ┌─────────────────────┐  ┌─────────────────────────┐   │    │
│  │  │   Public Subnet      │  │    Private Subnet       │   │    │
│  │  │   (10.0.1.0/24)     │  │    (10.0.2.0/24)        │   │    │
│  │  │                      │  │                          │   │    │
│  │  │  ┌────────────┐     │  │  ┌──────────────────┐   │   │    │
│  │  │  │    ALB     │     │  │  │  Backend API     │   │   │    │
│  │  │  │            │────────────│  (ECS Fargate)   │   │   │    │
│  │  │  └────────────┘     │  │  │  Port: 8000      │   │   │    │
│  │  │                      │  │  └──────────────────┘   │   │    │
│  │  └─────────────────────┘  │         │                │   │    │
│  │                            │         │                │   │    │
│  │  ┌─────────────────────────────────────────────────┐ │   │    │
│  │  │         GPU Compute Subnet (10.0.3.0/24)        │ │   │    │
│  │  │                                                  │ │   │    │
│  │  │  ┌──────────────────┐  ┌──────────────────┐    │ │   │    │
│  │  │  │  ML Audio Server │  │ GPU Render Server │    │ │   │    │
│  │  │  │  (ECS on EC2)    │  │  (ECS on EC2)     │    │ │   │    │
│  │  │  │  g4dn.xlarge     │  │  g4dn.2xlarge     │    │ │   │    │
│  │  │  │  Port: 8080      │  │  Port: 8090       │    │ │   │    │
│  │  │  └──────────────────┘  └──────────────────┘    │ │   │    │
│  │  │                                                  │ │   │    │
│  │  └─────────────────────────────────────────────────┘ │   │    │
│  │                                                       │   │    │
│  └───────────────────────────────────────────────────────┘   │    │
│                                                               │    │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │    │
│  │   ElastiCache │  │     RDS      │  │       S3        │   │    │
│  │    (Redis)    │  │  (PostgreSQL)│  │   Bucket        │   │    │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │    │
│                                                               │    │
└───────────────────────────────────────────────────────────────┘
```

## 📦 주요 AWS 서비스 구성

### 1. **컴퓨팅 (ECS + EC2)**

#### Backend API Server (ECS Fargate)
```yaml
Service: Backend-API
Task Definition:
  CPU: 2 vCPU
  Memory: 4GB
  Container:
    - Image: ecg-backend-api:latest
    - Port: 8000
    - Environment:
      - DATABASE_URL: RDS endpoint
      - REDIS_URL: ElastiCache endpoint
      - S3_BUCKET: ecg-videos
Auto Scaling:
  Min: 2
  Max: 10
  Target CPU: 70%
```

#### ML Audio Server (ECS on EC2)
```yaml
Service: ML-Audio-Server
Instance Type: g4dn.xlarge
  - GPU: 1x NVIDIA T4 (16GB VRAM)
  - CPU: 4 vCPUs
  - Memory: 16GB
  - Storage: 125GB NVMe SSD
Task Definition:
  Container:
    - Image: ecg-ml-audio:latest
    - Port: 8080
    - GPU Required: Yes
Auto Scaling Group:
  Min: 1
  Max: 4
  Scale on: GPU utilization > 70%
```

#### GPU Render Server (ECS on EC2)
```yaml
Service: GPU-Render-Server
Instance Type: g4dn.2xlarge
  - GPU: 1x NVIDIA T4 (16GB VRAM)
  - CPU: 8 vCPUs
  - Memory: 32GB
  - Storage: 225GB NVMe SSD
Task Definition:
  Container:
    - Image: ecg-gpu-render:phase2
    - Port: 8090
    - GPU Required: Yes
    - Celery Workers: 4
Auto Scaling Group:
  Min: 2
  Max: 8
  Scale on: Queue depth > 10 jobs
```

### 2. **네트워킹**

#### VPC Configuration
```yaml
VPC:
  CIDR: 10.0.0.0/16
  Region: ap-northeast-2 (Seoul)

Subnets:
  Public:
    - 10.0.1.0/24 (AZ-a) - ALB
    - 10.0.10.0/24 (AZ-c) - NAT Gateway

  Private:
    - 10.0.2.0/24 (AZ-a) - Backend API
    - 10.0.20.0/24 (AZ-c) - Backend API (HA)

  GPU Compute:
    - 10.0.3.0/24 (AZ-a) - ML Audio & GPU Render
    - 10.0.30.0/24 (AZ-c) - ML Audio & GPU Render (HA)

Security Groups:
  ALB-SG:
    - Inbound: 443 (HTTPS) from 0.0.0.0/0
    - Outbound: 8000 to Backend-SG

  Backend-SG:
    - Inbound: 8000 from ALB-SG
    - Outbound:
      - 8080 to ML-SG
      - 8090 to GPU-SG
      - 6379 to Redis-SG
      - 5432 to RDS-SG

  GPU-SG:
    - Inbound: 8090 from Backend-SG
    - Outbound:
      - 443 to S3
      - 6379 to Redis-SG
```

### 3. **스토리지 (S3)**

```yaml
S3 Buckets:
  ecg-videos:
    Purpose: 원본 비디오 및 렌더링된 비디오 저장
    Structure:
      /uploads/       - 사용자 업로드 원본
      /analyzed/      - ML 분석 완료 비디오
      /rendered/      - GPU 렌더링 완료 비디오
      /temp/          - 임시 처리 파일

    Lifecycle Policy:
      - /temp/: 24시간 후 삭제
      - /uploads/: 30일 후 Glacier 이동
      - /rendered/: 90일 후 Glacier 이동

    Features:
      - Versioning: Enabled
      - Encryption: AES-256 (SSE-S3)
      - Transfer Acceleration: Enabled
      - CloudFront Distribution: Yes
```

### 4. **데이터베이스**

#### RDS PostgreSQL
```yaml
Engine: PostgreSQL 15.4
Instance Class: db.r6g.xlarge
  - vCPUs: 4
  - Memory: 32GB
  - Network: Up to 10 Gbps

Storage:
  - Type: GP3 SSD
  - Size: 500GB
  - IOPS: 12,000
  - Throughput: 500 MiB/s

High Availability:
  - Multi-AZ: Yes
  - Read Replicas: 2 (서울, 도쿄)
  - Automated Backups: 7일 보관
  - Point-in-time Recovery: Yes

Schema:
  - render_jobs: 렌더링 작업 상태
  - ml_analysis: ML 분석 결과
  - user_videos: 비디오 메타데이터
  - phase2_metrics: 스트리밍 파이프라인 메트릭
```

#### ElastiCache Redis
```yaml
Engine: Redis 7.0
Node Type: cache.r6g.xlarge
  - vCPUs: 4
  - Memory: 26.32 GB
  - Network: Up to 10 Gbps

Cluster Configuration:
  - Mode: Cluster Mode Enabled
  - Shards: 3
  - Replicas per Shard: 2
  - Total Nodes: 9

Features:
  - Automatic Failover: Yes
  - Backup: Daily snapshots
  - Encryption: In-transit & At-rest

Usage:
  - Celery Broker (DB 0)
  - Celery Results (DB 1)
  - Session Cache (DB 2)
  - Progress Updates (DB 3)
  - Phase 2 Metrics Cache (DB 4)
```

### 5. **모니터링 & 로깅**

#### CloudWatch
```yaml
Dashboards:
  - System Overview: CPU, Memory, Network
  - GPU Metrics: Utilization, Memory, Temperature
  - Application Metrics:
    - Render queue depth
    - Job completion rate
    - Frame drop rate (Phase 2)
    - Memory efficiency (Phase 2)

Alarms:
  Critical:
    - GPU Memory > 90%
    - Queue Depth > 100
    - Frame Drop Rate > 5%
    - DB Connection Pool Exhausted

  Warning:
    - CPU > 80% for 5 minutes
    - Memory > 85%
    - Disk Usage > 80%
    - Response Time > 2s

Log Groups:
  - /ecs/backend-api
  - /ecs/ml-audio-server
  - /ecs/gpu-render-server
  - /ecs/celery-workers
```

#### X-Ray
```yaml
Tracing:
  - API Request Flow
  - Celery Task Execution
  - S3 Upload/Download
  - Database Queries
  - Inter-service Communication
```

## 🚀 배포 파이프라인

### CI/CD with AWS CodePipeline

```yaml
Pipeline:
  Source:
    - GitHub Repository
    - Branch: main
    - Webhook: Push events

  Build (CodeBuild):
    - Docker Image Build
    - Unit Tests
    - Integration Tests
    - Security Scanning (ECR Scanning)

  Deploy:
    Development:
      - ECS Blue/Green Deployment
      - Automatic Rollback on Failure

    Staging:
      - Manual Approval Required
      - Load Testing
      - Performance Validation

    Production:
      - Manual Approval Required
      - Canary Deployment (10% → 50% → 100%)
      - CloudWatch Alarms Monitoring
```

## 💰 비용 최적화 전략

### 1. **EC2 Spot Instances**
```yaml
GPU Render Workers:
  - Spot Instance 활용 (최대 70% 비용 절감)
  - Spot Fleet 구성으로 안정성 확보
  - On-Demand 백업 인스턴스 유지
```

### 2. **Auto Scaling 정책**
```yaml
Time-based Scaling:
  - Peak Hours (09:00-18:00 KST): Min 4 workers
  - Off-peak: Min 2 workers
  - Weekends: Min 1 worker

Predictive Scaling:
  - ML 기반 수요 예측
  - 미리 스케일 아웃하여 대기 시간 감소
```

### 3. **S3 Intelligent Tiering**
```yaml
Storage Classes:
  - Standard: 최근 7일 파일
  - Standard-IA: 7-30일 파일
  - Glacier Instant: 30-90일 파일
  - Glacier Deep Archive: 90일 이상
```

## 🔒 보안 구성

### 1. **IAM Roles & Policies**
```yaml
ECS Task Roles:
  Backend-API-Role:
    - S3: Read/Write to ecg-videos/*
    - RDS: Full access to ecg-db
    - Secrets Manager: Read secrets

  GPU-Render-Role:
    - S3: Read/Write to ecg-videos/rendered/*
    - ElastiCache: Full access
    - CloudWatch: Write logs/metrics
```

### 2. **Secrets Management**
```yaml
AWS Secrets Manager:
  - Database credentials
  - API keys
  - JWT secrets
  - S3 access keys

Rotation Policy:
  - Automatic rotation every 30 days
  - Lambda function for rotation logic
```

### 3. **Network Security**
```yaml
WAF Rules:
  - Rate limiting: 1000 req/5min per IP
  - Geo-blocking: Allow KR, US, JP only
  - SQL injection protection
  - XSS protection

VPC Endpoints:
  - S3 VPC Endpoint (Gateway)
  - Secrets Manager VPC Endpoint
  - ECR VPC Endpoint
```

## 📊 Phase 2 성능 향상 (AWS 환경)

### Before (Phase 1)
```yaml
Instance Requirements:
  - g4dn.4xlarge × 3 (높은 메모리 요구)
  - 총 비용: $2.52/hour × 3 = $7.56/hour
  - 동시 처리: 9-12 jobs
```

### After (Phase 2)
```yaml
Instance Requirements:
  - g4dn.2xlarge × 4 (낮은 메모리 요구)
  - 총 비용: $1.26/hour × 4 = $5.04/hour
  - 동시 처리: 32-40 jobs
  - 비용 절감: 33%
  - 처리량 증가: 250%
```

## 🔄 재해 복구 (DR) 전략

```yaml
RTO (Recovery Time Objective): 1 hour
RPO (Recovery Point Objective): 5 minutes

Backup Strategy:
  - RDS: Automated backups + Cross-region snapshots
  - S3: Cross-region replication to us-west-2
  - ElastiCache: Daily snapshots to S3

Failover Plan:
  - Primary: Seoul (ap-northeast-2)
  - Secondary: Tokyo (ap-northeast-1)
  - DNS Failover: Route 53 Health Checks
  - Data Sync: AWS DataSync for S3
```

## 🎯 확장 가능성

### Horizontal Scaling
- **GPU Workers**: 최대 20개 인스턴스까지 확장 가능
- **처리 용량**: 시간당 1,000+ 비디오 렌더링
- **동시 사용자**: 10,000+ 동시 접속 지원

### Global Expansion
- **CloudFront CDN**: 전 세계 엣지 로케이션 활용
- **Multi-Region**: 각 대륙별 리전 배포 가능
- **Global Accelerator**: 글로벌 트래픽 최적화

---

이 아키텍처는 Phase 2 스트리밍 파이프라인의 메모리 효율성을 활용하여 더 적은 비용으로 더 높은 처리량을 달성합니다. 특히 Celery 기반 분산 처리와 Redis 캐싱을 통해 확장성과 안정성을 동시에 확보했습니다.