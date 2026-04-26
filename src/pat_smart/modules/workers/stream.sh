#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

RTSP_URL="rtsp://admin:smart2019@192.168.1.103:554/stream0"
RTMP_URL="rtmp://192.168.1.101/CCTVApp/CCTV-2C9F9E1F90627"
VIDEO_ENCODER="${VIDEO_ENCODER:-libx264}"
FPS="${FPS:-30}"
BITRATE="${BITRATE:-2000k}"
GOP_SIZE="${GOP_SIZE:-60}"

if [ -z "$RTSP_URL" ] || [ -z "$RTMP_URL" ]; then
    echo "Usage: "
    echo "  export RTSP_URL='rtsp://camera-ip:554/stream'"
    echo "  export RTMP_URL='rtmp://server/app/stream-key'"
    echo "  ./rtsp_to_rtmp.sh"
    exit 1
fi

echo "Starting RTSP to RTMP stream..."
echo "  RTSP: $RTSP_URL"
echo "  RTMP: $RTMP_URL"

ffmpeg -re -rtsp_transport tcp -i "$RTSP_URL" \
    -c:v $VIDEO_ENCODER -preset ultrafast \
    -b:v $BITRATE -maxrate $BITRATE \
    -g $GOP_SIZE -r $FPS \
    -c:a aac -b:a 128k \
    -f flv "$RTMP_URL"
