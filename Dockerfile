# GPU Render Server Docker Image
FROM nvidia/cuda:11.8-devel-ubuntu22.04

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    ffmpeg \
    wget \
    xvfb \
    chromium-browser \
    fonts-noto-cjk \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements-render.txt ./

# Install Python packages
RUN pip3 install --no-cache-dir -r requirements-render.txt

# Install Playwright browsers
RUN pip3 install playwright && playwright install chromium

# Copy application code
COPY services/render /app/services/render
COPY shared /app/shared

# Set Python path
ENV PYTHONPATH=/app
ENV CUDA_VISIBLE_DEVICES=0
ENV DISPLAY=:99

# Create start script
RUN echo '#!/bin/bash\n\
Xvfb :99 -screen 0 1920x1080x24 &\n\
nvidia-smi\n\
python3 services/render/server.py --host 0.0.0.0 --port 8090' > /start.sh \
&& chmod +x /start.sh

# Expose port
EXPOSE 8090

# Run server
CMD ["/start.sh"]