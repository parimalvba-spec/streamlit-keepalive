#!/usr/bin/env bash
set -e

pip install -r requirements.txt

# Install system dependencies that Chromium needs (Render allows this)
apt-get install -y \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    2>/dev/null || true

# Install only Chromium browser (no --with-deps to avoid root requirement)
python -m playwright install chromium
