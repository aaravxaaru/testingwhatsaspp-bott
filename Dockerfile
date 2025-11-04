# Python base
FROM python:3.12-slim

# Install Chrome + deps
RUN apt-get update && apt-get install -y \
    wget gnupg unzip \
    fonts-liberation libasound2 libatk-bridge2.0-0 libnss3 libx11-6 libxcomposite1 \
    libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libgtk-3-0 libxshmfence1 \
    ca-certificates --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Install Chromium (stable) and matching chromedriver via debs
RUN apt-get update && apt-get install -y chromium chromium-driver --no-install-recommends && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/bin/chromedriver

# Workdir
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY app.py ./

# Create session dir for Chrome profile
RUN mkdir -p /app/session
ENV CHROME_USER_DATA_DIR=/app/session

# Expose (Render ignores EXPOSE but ok)
EXPOSE 5000

# Start
CMD ["python", "app.py"]
