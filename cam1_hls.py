#!/usr/bin/env python3
import subprocess, shlex, time

VIDEO_PATH = "Cam1_deint.mp4"
AUDIO_PATH = "Audio.mp3"
HLS_PATH   = "/usr/local/nginx/html/stream/cam1.m3u8"

FPS = 30
BITRATE = "4M"
MAXRATE = "5M"
BUFSIZE = "8M"
ENCODER = "h264_nvenc"   # 필요시 "libx264" 로 교체
HLS_TIME = "1"
HLS_LIST_SIZE = "6"

def build_cmd():
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-stream_loop", "-1", "-re", "-i", VIDEO_PATH,
        "-stream_loop", "-1", "-i", AUDIO_PATH,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", ENCODER,
        "-preset", "p6", "-tune", "ll",
        "-b:v", BITRATE, "-maxrate", MAXRATE, "-bufsize", BUFSIZE,
        "-g", str(FPS), "-force_key_frames", f"expr:gte(t,n_forced*1)",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", "aresample=async=1:first_pts=0",
        "-f", "hls",
        "-hls_time", HLS_TIME, "-hls_list_size", HLS_LIST_SIZE,
        "-hls_flags", "delete_segments+omit_endlist+program_date_time+independent_segments",
        HLS_PATH
    ]
    return cmd

def main():
    cmd = build_cmd()
    print("[CAM1] Starting ffmpeg:", " ".join(shlex.quote(x) for x in cmd))
    proc = subprocess.Popen(cmd)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
