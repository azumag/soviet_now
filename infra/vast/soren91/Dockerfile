FROM mcr.microsoft.com/playwright:v1.59.1-noble

ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:0 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=graphics,video,utility,display

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        jq \
        mesa-utils \
        openbox \
        pulseaudio \
        x11-utils \
        xserver-xorg-core \
    && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q 'h264_nvenc' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/soren91
COPY infra/vast/soren91/package.json infra/vast/soren91/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

COPY tools/soren91_gpu_runner.mjs ./tools/soren91_gpu_runner.mjs
COPY infra/vast/soren91/entrypoint.sh ./entrypoint.sh
RUN chmod 0755 ./entrypoint.sh

ENTRYPOINT ["/opt/soren91/entrypoint.sh"]
