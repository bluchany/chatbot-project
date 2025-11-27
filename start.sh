#!/bin/bash

# 1. Redis 서버 백그라운드 실행 (메모리 전용 모드, 디스크 저장 끔)
# Hugging Face는 디스크 쓰기 권한이 까다로워서 인메모리로 돌리는 게 안전함
echo "🚀 Starting Redis Server..."
redis-server --save "" --appendonly no &

# Redis가 켜질 때까지 잠시 대기
sleep 2

# 2. Worker(파이썬 구조대) 백그라운드 실행
echo "🚀 Starting Chatbot Worker..."
python -u worker.py &

# 3. FastAPI 서버 포그라운드 실행 (메인 프로세스)
# Hugging Face는 반드시 7860 포트를 사용해야 함!
echo "🚀 Starting FastAPI Server..."
uvicorn main:app --host 0.0.0.0 --port 7860