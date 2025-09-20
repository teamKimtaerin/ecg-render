#!/bin/bash

# ECG-Render startup script
# Usage: ./start.sh [server|worker|both]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default mode
MODE=${1:-both}

# Check Node.js
if ! command -v node &> /dev/null; then
    echo -e "${RED}Node.js is not installed${NC}"
    exit 1
fi

# Check Redis
if ! command -v redis-cli &> /dev/null; then
    echo -e "${YELLOW}Redis CLI not found. Make sure Redis is running${NC}"
else
    redis-cli ping > /dev/null 2>&1 || {
        echo -e "${RED}Redis is not running. Starting Redis...${NC}"
        redis-server --daemonize yes
        sleep 2
    }
    echo -e "${GREEN}Redis is running${NC}"
fi

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${RED}FFmpeg is not installed${NC}"
    exit 1
fi

# Check environment file
if [ ! -f .env ]; then
    echo -e "${YELLOW}No .env file found. Creating from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env with your configuration${NC}"
fi

# Build if needed
if [ ! -d "dist" ]; then
    echo -e "${YELLOW}Building application...${NC}"
    npm run build
fi

# Create directories
mkdir -p logs /tmp/ecg-render

# Function to start server
start_server() {
    echo -e "${GREEN}Starting API server...${NC}"
    npm start > logs/server.log 2>&1 &
    SERVER_PID=$!
    echo "Server PID: $SERVER_PID"
    echo $SERVER_PID > .server.pid
}

# Function to start worker
start_worker() {
    echo -e "${GREEN}Starting worker...${NC}"
    npm run start:worker > logs/worker.log 2>&1 &
    WORKER_PID=$!
    echo "Worker PID: $WORKER_PID"
    echo $WORKER_PID > .worker.pid
}

# Handle shutdown
shutdown() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    
    if [ -f .server.pid ]; then
        kill $(cat .server.pid) 2>/dev/null || true
        rm .server.pid
    fi
    
    if [ -f .worker.pid ]; then
        kill $(cat .worker.pid) 2>/dev/null || true
        rm .worker.pid
    fi
    
    echo -e "${GREEN}Shutdown complete${NC}"
    exit 0
}

trap shutdown INT TERM

# Start services based on mode
case $MODE in
    server)
        start_server
        echo -e "${GREEN}Server started. Check logs/server.log for details${NC}"
        ;;
    worker)
        start_worker
        echo -e "${GREEN}Worker started. Check logs/worker.log for details${NC}"
        ;;
    both)
        start_server
        sleep 2
        start_worker
        echo -e "${GREEN}Both server and worker started${NC}"
        echo -e "${GREEN}API: http://localhost:3000${NC}"
        echo -e "${GREEN}Logs: tail -f logs/*.log${NC}"
        ;;
    *)
        echo -e "${RED}Invalid mode: $MODE${NC}"
        echo "Usage: $0 [server|worker|both]"
        exit 1
        ;;
esac

# Wait for processes
if [ "$MODE" = "both" ]; then
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    wait
fi