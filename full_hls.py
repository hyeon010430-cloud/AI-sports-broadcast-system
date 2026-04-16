#!/usr/bin/env python3
import subprocess
import time
import shlex

# ===== 사용자 설정 =====
VIDEO_PATH = "FULL_deint.mp4"                     # 본편 비디오
AUDIO_PATH = "Audio.mp3"                          # 본편 오디오
HLS_PATH   = "/usr/local/nginx/html/stream/full.m3u8"

FPS = 30
BITRATE = "5M"
MAXRATE = "6M"
BUFSIZE = "10M"

# 인코더: NVENC가 불안하면 "libx264"로 바꿔 테스트
ENCODER = "h264_nvenc"        # 또는 "libx264"

# 싱크 보정(고정 오프셋) — 필요 없으면 0.0
# 예) ZOOM이 FULL보다 0.50초 늦다면 FULL을 +0.50초 늦추기 → 0.50 입력
VIDEO_DELAY_SEC = 0.0          # 비디오 시작 지연 (tpad)
AUDIO_DELAY_SEC = 0.0          # 오디오 시작 지연 (adelay, 좌/우 동일 ms)

# HLS 세그 길이/목록
HLS_TIME = "0.5"               # ← 0.5초로 변경
HLS_LIST_SIZE = "6"

# === 세그 길이에 맞춘 GOP/키프레임 간격 계산 ===
SEG_SEC = float(HLS_TIME)                  # 0.5
GOP = max(1, int(round(FPS * SEG_SEC)))    # 30fps → 15 프레임
KF_INT_SEC = SEG_SEC                       # 키프레임 간격(초)

def build_cmd() -> list:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",

        # 입력 0: 비디오 (반복 재생, 실시간으로 읽기)
        "-stream_loop", "-1",
        "-re",
        # 일부 ffmpeg 빌드에서 파일입력+이 옵션이 PTS를 꼬이게 해서 기본 OFF
        # "-use_wallclock_as_timestamps", "1",
        "-i", VIDEO_PATH,

        # 입력 1: 오디오 (반복 재생)
        "-stream_loop", "-1", "-i", AUDIO_PATH,

        # 매핑
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]

    # ===== 비디오 인코딩 =====
    if ENCODER == "h264_nvenc":
        vcodec = [
            "-c:v", "h264_nvenc",
            "-preset", "p6",
            "-tune", "ll",
            "-rc", "cbr", "-b:v", BITRATE, "-maxrate", MAXRATE, "-bufsize", BUFSIZE,
            "-rc-lookahead", "0",
            "-bf", "0",
            "-g", str(GOP),  # 세그 길이에 맞춘 GOP (예: 15)
            "-force_key_frames", f"expr:gte(t,n_forced*{KF_INT_SEC})",
        ]
    else:
        # libx264 안정 모드
        vcodec = [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-x264-params", f"keyint={GOP}:min-keyint={GOP}:no-scenecut=1",
            "-bf", "0",
            "-g", str(GOP),
            "-force_key_frames", f"expr:gte(t,n_forced*{KF_INT_SEC})",
            "-b:v", BITRATE,
        ]

    # 비디오 필터: 지연 보정(tpad) + 픽셀포맷
    vf_chain = []
    if VIDEO_DELAY_SEC and VIDEO_DELAY_SEC > 0:
        vf_chain.append(f"tpad=start_duration={VIDEO_DELAY_SEC}")
    vf_chain.append("format=yuv420p")

    vfilters = []
    if vf_chain:
        vfilters = ["-vf", ",".join(vf_chain)]

    # ===== 오디오 인코딩 =====
    acodec = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]

    # 오디오 필터: 지연 보정(adelay) + 드리프트 보정
    af_chain = []
    if AUDIO_DELAY_SEC and AUDIO_DELAY_SEC > 0:
        ms = int(round(AUDIO_DELAY_SEC * 1000))
        af_chain.append(f"adelay={ms}|{ms}")
    af_chain.append("aresample=async=1:first_pts=0")
    afilters = ["-af", ",".join(af_chain)]

    # PTS 안전장치
    pts_safety = ["-fflags", "+genpts"]

    # ===== HLS 출력 =====
    hls = [
        "-f", "hls",
        "-hls_time", HLS_TIME,   # 0.5초
        "-hls_list_size", HLS_LIST_SIZE,
        "-hls_flags", "delete_segments+omit_endlist+program_date_time+independent_segments",
        "-hls_allow_cache", "0",
        HLS_PATH
    ]

    cmd += vcodec + vfilters + acodec + afilters + pts_safety + hls
    return cmd

def start_full_hls():
    cmd = build_cmd()
    print("[FULL] ffmpeg cmd:")
    print("       " + " ".join(shlex.quote(x) for x in cmd))
    return subprocess.Popen(cmd)

def main():
    print(f"[FULL] ▶ HLS start: {VIDEO_PATH} + {AUDIO_PATH} → {HLS_PATH}")
    proc = start_full_hls()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[FULL] Stopping...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()

