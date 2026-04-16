#!/usr/bin/env bash
set -euo pipefail

# ===== 사용자 설정 =====
WORKDIR="$HOME/YBE"
VENV="$HOME/venv"                          # venv는 홈 디렉토리 바로 아래
PY="$VENV/bin/python"                      # venv 파이썬을 직접 사용
FULL_PY="$WORKDIR/full_hls.py"             # full 파이프라인 파이썬 파일
ZOOM_PY="$WORKDIR/main.py"                 # zoom 파이프라인 파이썬 파일
CAM1_PY="$WORKDIR/cam1_hls.py"
CAM2_PY="$WORKDIR/cam2_hls.py"
CAM3_PY="$WORKDIR/cam3_hls.py"
HLS_DIR="/usr/local/nginx/html/stream"

TARGETS="8,9,11,19"                        # zoom 타겟들
OUT_TMPL="$HLS_DIR/final{num}.m3u8"        # zoom 출력 템플릿
AUDIO_DELAY="-0.60"                        # 필요 없으면 "" 로 비워도 됨

SEG=1                                      # hls_time=1s (세그먼트 경계 동시 시작)
LOG_DIR="$WORKDIR/logs"
mkdir -p "$HLS_DIR" "$LOG_DIR"

log(){ echo "[$(date '+%H:%M:%S')] $*"; }

# 다음 SEG초 경계까지 대기 (밀리초 정밀)
sleep_to_boundary(){
  local seg_ms=$((SEG * 1000))
  local now_ms
  now_ms=$(date +%s%3N)
  local remain=$(( (seg_ms - (now_ms % seg_ms)) % seg_ms ))
  "$PY" - <<'PY' "$remain"
import sys, time
ms=int(sys.argv[1]); time.sleep(ms/1000.0)
PY
}

cleanup(){
  log "Stopping children (trap)..."
  pkill -P $$ || true
}
trap cleanup EXIT

# ===== 실행 =====
cd "$WORKDIR"

# (선택) 이전 ffmpeg 정리
pkill -9 -x ffmpeg 2>/dev/null || true

log "Waiting to start at the next ${SEG}s boundary..."
sleep_to_boundary

log "Starting FULL and ZOOM almost simultaneously..."

# ZOOM 먼저 시작
ZOOM_CMD=( "$PY" "$ZOOM_PY" --target "$TARGETS" --output "$OUT_TMPL" )
if [[ -n "${AUDIO_DELAY}" ]]; then
  ZOOM_CMD+=( --audio-delay "$AUDIO_DELAY" )
fi
nohup "${ZOOM_CMD[@]}" \
  > "$LOG_DIR/zoom.out" 2>&1 &
ZOOM_PID=$!
sleep 1
nohup "$PY" "$CAM1_PY" > "$LOG_DIR/cam1.out" 2>&1 &
CAM1_PID=$!
nohup "$PY" "$CAM2_PY" > "$LOG_DIR/cam2.out" 2>&1 &
CAM2_PID=$!
nohup "$PY" "$CAM3_PY" > "$LOG_DIR/cam3.out" 2>&1 &
CAM3_PID=$!


# 3.5초 지연 후 FULL 시작
sleep 2.5
nohup "$PY" "$FULL_PY" \
  > "$LOG_DIR/full.out" 2>&1 &
FULL_PID=$!

# CAM1,2,3 동시 실행


log "FULL PID: $FULL_PID"
log "ZOOM PID: $ZOOM_PID"
log "CAM1 PID: $CAM1_PID"
log "CAM2 PID: $CAM2_PID"
log "CAM3 PID: $CAM3_PID"
log "Logs: $LOG_DIR/*.out"

# 간단 헬스체크: m3u8 생성 확인 (최대 20초)
deadline=$(( $(date +%s) + 20 ))
full_ok="no"; zoom_ok="no"
first_target="${TARGETS%%,*}"

while [[ $(date +%s) -lt $deadline ]]; do
  [[ -f "$HLS_DIR/full.m3u8" ]] && full_ok="yes"
  [[ -f "$HLS_DIR/final${first_target}.m3u8" ]] && zoom_ok="yes"
  if [[ "$full_ok" == "yes" && "$zoom_ok" == "yes" ]]; then
    break
  fi
  sleep 1
done

log "FULL HLS: $full_ok, ZOOM HLS: $zoom_ok"
if [[ "$full_ok" != "yes" || "$zoom_ok" != "yes" ]]; then
  log "⚠️  HLS not ready yet. Check logs above."
fi

# 포그라운드 유지(CTRL+C로 종료 가능)
wait

