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
COPY requirements.txt ./

# Install Python packages
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN pip3 install playwright && playwright install chromium

# Copy application code
COPY . /app/

# Set Python path
ENV PYTHONPATH=/app
ENV CUDA_VISIBLE_DEVICES=0
ENV DISPLAY=:99

# Create start script
RUN echo '#!/bin/bash\n\
Xvfb :99 -screen 0 1920x1080x24 &\n\
nvidia-smi\n\
python3 main.py --mode standalone --host 0.0.0.0 --port 8090' > /start.sh \
&& chmod +x /start.sh

# Create worker script
RUN echo '#!/bin/bash\n\
Xvfb :99 -screen 0 1920x1080x24 &\n\
nvidia-smi\n\
python3 main.py --mode worker' > /start-worker.sh \
&& chmod +x /start-worker.sh

# Expose port
EXPOSE 8090

# Default to standalone mode (can be overridden)
CMD ["/start.sh"]