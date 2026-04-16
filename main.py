import cv2
import time
import random
import threading
import queue
import torch
import easyocr
import numpy as np
import subprocess
import argparse
from collections import defaultdict, deque
from ultralytics import YOLO
from tracker import Tracker
from allowed_numbers import ALLOWED_NUMBERS

# ======================= 인자 파싱 =======================
parser = argparse.ArgumentParser()
parser.add_argument('--target', type=str, required=True,
                    help='Zoom target player number(s). e.g. "8" or "8,9,11"')
parser.add_argument('--output', type=str, required=True,
                    help='HLS output path. Use {num} for multi-target, e.g. "/usr/local/nginx/html/stream/final{num}.m3u8"')
parser.add_argument(
    '--audio-delay',
    type=float,
    default=-0.6,   # 기본 0.5초 지연
    help='Audio delay in seconds (positive=delay audio, negative=advance)'
)
args = parser.parse_args()

TARGET_NUMBERS = [t.strip() for t in str(args.target).split(',') if t.strip()]
OUT_TEMPLATE = args.output  # 다중이면 {num} 필수

# ======================= 입력 소스 =======================
VIDEO_PATHS = {
    "cam1": "Cam1_deint.mp4",
    "cam2": "Cam2_deint.mp4",
    "cam3": "Cam3_deint.mp4"
}

# ======================= 기본 설정 =======================
DETECTION_THRESHOLD = 0.6
FPS_TARGET = 30
OCR_CONFIDENCE_THRESHOLD = 0.6
OCR_FRAME_INTERVAL = 5
IMGSZ = 640

# ======================= 모델 및 전역 변수 =======================
torch.backends.cudnn.benchmark = True
model = YOLO("yolov8s.pt").to("cuda")
PRIMARY_SOURCE = next(iter(VIDEO_PATHS))  # cam1을 기본 풀샷 소스로 사용
OUT_W, OUT_H = 1920, 1080
AUDIO_PATH = "Audio.mp3"
reader = easyocr.Reader(['en'], gpu=True)

# 프레임 큐(디코더 → 추론)
frame_queues = {}

# OCR 파이프(공유)
ocr_input_queue = queue.Queue(maxsize=24)
ocr_output_queue = queue.Queue(maxsize=24)

# 트래커 및 라벨링
trackers = {}
id_to_number = defaultdict(dict)
ocr_buffers = defaultdict(lambda: defaultdict(lambda: deque(maxlen=5)))

# 소스별 마지막 bbox (공유)
last_known_bbox_global = defaultdict(lambda: None)

# 각 카메라의 최신 원본 프레임 저장(공유)
latest_full_frame = {name: None for name in VIDEO_PATHS}

# ======== 스냅샷 공유 (추론 → 셀렉터, 드레인 금지) ========
state_lock = {name: threading.Lock() for name in VIDEO_PATHS}
shared_state = {
    name: {"frame": None, "tracks": [], "tick": 0}
    for name in VIDEO_PATHS
}

# ======================= Frame Reader =======================
def frame_reader(name, cap):
    # 디코더 버퍼 최소화
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    period = 1.0 / FPS_TARGET
    next_t = time.monotonic()

    while cap.isOpened():
        # pace
        now = time.monotonic()
        if now < next_t:
            time.sleep(next_t - now)
        next_t += period

        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        if frame_queues[name].full():
            try:
                frame_queues[name].get_nowait()
            except queue.Empty:
                pass
        frame_queues[name].put(frame)

    cap.release()

# ======================= Inference Worker =======================
def inference_worker(name):
    frame_counter = 0
    while True:
        try:
            frame = frame_queues[name].get(timeout=1)
        except queue.Empty:
            continue

        try:
            frame_counter += 1
            results = model(frame, verbose=False, imgsz=IMGSZ)[0]

            detections = []
            for r in results.boxes.data.tolist():
                x1, y1, x2, y2, score, _ = r
                if score > DETECTION_THRESHOLD:
                    detections.append([int(x1), int(y1), int(x2), int(y2), score])

            trackers[name].update(frame, detections)

            # 최신 원본 프레임 갱신
            latest_full_frame[name] = frame

            # OCR 샘플링
            if frame_counter % OCR_FRAME_INTERVAL == 0:
                for track in trackers[name].tracks:
                    tid, bbox = track.track_id, track.bbox
                    if tid not in id_to_number[name]:
                        # 이미 같은 tid가 대기열에 너무 많이 쌓이지 않도록 큐 크기 제한으로 자연 제어
                        ocr_input_queue.put((name, frame, tid, bbox))

            # ---- 결과 스냅샷 공유 (드레인 X) ----
            with state_lock[name]:
                shared_state[name]["frame"] = frame
                shared_state[name]["tracks"] = [(t.track_id, t.bbox) for t in trackers[name].tracks]
                shared_state[name]["tick"] += 1

        except Exception as e:
            print(f"[{name}] ⚠️ Inference error: {e}")

# ======================= OCR Worker =======================
def ocr_worker():
    while True:
        try:
            name, frame, tid, bbox = ocr_input_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            x1, x2 = max(0, min(x1, w)), max(0, min(x2, w))
            y1, y2 = max(0, min(y1, h)), max(0, min(y2, h))
            cropped = frame[y1:y2, x1:x2]

            results = reader.readtext(cropped)
            picked = None
            for (_, text, conf) in results:
                text = text.strip()
                if conf >= OCR_CONFIDENCE_THRESHOLD and text.isdigit() and text in ALLOWED_NUMBERS:
                    picked = text
                    break

            ocr_output_queue.put((name, tid, picked))
        except Exception as e:
            print(f"[OCR] ⚠️ {e}")
            ocr_output_queue.put((name, tid, None))

# ======================= OCR 결과 처리 =======================
def ocr_result_collector():
    while True:
        try:
            name, tid, number = ocr_output_queue.get(timeout=1)
        except queue.Empty:
            continue

        if number:
            # 동일 번호 중복 매핑 충돌 시 리셋
            conflicted = [k for k, v in id_to_number[name].items() if v == number and k != tid]
            if conflicted:
                for cid in conflicted + [tid]:
                    id_to_number[name].pop(cid, None)
                    ocr_buffers[name].pop(cid, None)
                continue

            ocr_buffers[name][tid].append(number)
            # 다수결
            most_common = max(set(ocr_buffers[name][tid]), key=ocr_buffers[name][tid].count)
            if ocr_buffers[name][tid].count(most_common) >= 3:
                id_to_number[name][tid] = most_common

# ======================= 헬퍼들 =======================
def safe_crop_resize_16x9(frame, bbox, out_w=1920, out_h=1080, ref_h=480):
    try:
        x1, y1, x2, y2 = map(int, bbox)
        H, W = frame.shape[:2]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        crop_h = ref_h
        crop_w = int(crop_h * (16/9))
        left = max(0, min(W - crop_w, cx - crop_w // 2))
        top  = max(0, min(H - crop_h, cy - crop_h // 2))
        crop = frame[top:top+crop_h, left:left+crop_w]
        if crop.size == 0:
            return None
        return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    except Exception:
        return None

def fallback_from_latest(source_name, last_known_bbox_dict, out_w=1920, out_h=1080):
    base = latest_full_frame.get(source_name)
    if base is None:
        return None
    fb = None
    if last_known_bbox_dict.get(source_name) is not None:
        fb = safe_crop_resize_16x9(base, last_known_bbox_dict[source_name], out_w, out_h)
    if fb is None:
        fb = cv2.resize(base, (out_w, out_h), interpolation=cv2.INTER_AREA)
    return fb

def start_hls_writer(hls_path, fps=FPS_TARGET, audio_path=None, loop_audio=True, audio_delay_sec=0.0, hls_time=0.5):
    """
    hls_time(초)을 자유롭게 설정 가능(예: 0.5).
    세그 경계에 정확히 키프레임이 오도록 GOP과 force_key_frames를 hls_time에 맞춰 자동 계산.
    """
    # === hls_time 기반 GOP 계산 (예: fps=30, hls_time=0.5 → GOP=15) ===
    gop = max(1, int(round(fps * float(hls_time))))

    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-y',

        # ▶ 입력 #0: raw BGR 비디오 (stdin)
        '-thread_queue_size', '1024',
        '-use_wallclock_as_timestamps', '1',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-s', '1920x1080',
        '-r', str(fps),
        '-i', '-',  # stdin
    ]

    # ▶ 입력 #1: 오디오(파일)
    audio_input_added = False
    if audio_path:
        if loop_audio:
            cmd += ['-stream_loop', '-1']
        # delay < 0 : 오디오 앞당김(음수 itsoffset)
        if audio_delay_sec < 0:
            cmd += ['-itsoffset', str(audio_delay_sec)]
        cmd += ['-thread_queue_size', '1024', '-i', audio_path]
        audio_input_added = True

    # 타임스탬프 정렬
    cmd += ['-copyts', '-start_at_zero']

    # ▶ 매핑 & 코덱
    if audio_input_added:
        cmd += [
            '-map', '0:v:0',
            '-map', '1:a:0',

            # 비디오 인코딩
            '-c:v', 'h264_nvenc',
            '-preset', 'p6',
            '-tune', 'll',
            '-rc', 'cbr', '-b:v', '5M', '-maxrate', '6M', '-bufsize', '10M',
            '-rc-lookahead', '0',
            '-bf', '0',
            '-g', str(gop),                                # GOP = fps * hls_time
            '-force_key_frames', f'expr:gte(t,n_forced*{hls_time})',
            '-vf', 'format=yuv420p',
            '-fps_mode', 'cfr',

            # 오디오 인코딩 + 드리프트 보정
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '48000',
            '-ac', '2',
        ]

        # delay > 0 : 오디오 뒤로 밀기(무음 삽입)
        if audio_delay_sec > 0:
            ms = int(round(audio_delay_sec * 1000))
            cmd += ['-af', f'adelay={ms}|{ms},aresample=async=1:first_pts=0']
        else:
            cmd += ['-af', 'aresample=async=1:first_pts=0']

        cmd += ['-fflags', '+genpts']
    else:
        cmd += [
            '-an',
            '-c:v', 'h264_nvenc',
            '-preset', 'p6',
            '-tune', 'll',
            '-rc', 'cbr', '-b:v', '5M', '-maxrate', '6M', '-bufsize', '10M',
            '-rc-lookahead', '0',
            '-bf', '0',
            '-g', str(gop),
            '-force_key_frames', f'expr:gte(t,n_forced*{hls_time})',
            '-vf', 'format=yuv420p',
            '-fps_mode', 'cfr',
            '-fflags', '+genpts',
        ]

    # ▶ HLS 출력 (hls_time 가변, epoch 기반 번호, 독립 세그먼트, PDT 포함)
    cmd += [
        '-f', 'hls',
        '-hls_time', str(hls_time),                       # 예: 0.5
        '-hls_list_size', '6',
        '-hls_flags', 'delete_segments+omit_endlist+program_date_time+independent_segments',
        '-hls_start_number_source', 'epoch',
        '-hls_allow_cache', '0',
        hls_path
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0)
    return proc




# ======================= 타겟별 파이프라인 =======================
def run_target_pipeline(target_number: str, output_path: str):
    """
    각 타겟 번호별로 selector+pacer+writer를 독립 실행.
    공유 리소스: latest_full_frame, id_to_number, shared_state
    """
    writer_queue = queue.Queue(maxsize=120)
    last_frame_lock = threading.Lock()
    last_zoomed_frame = {'frame': None}
    last_known_bbox = defaultdict(lambda: None)  # 타겟별 독립 상태

    def writer_thread():
    #    🔊 오디오 합성해서 HLS로
        proc = start_hls_writer(output_path, fps=FPS_TARGET, audio_path=AUDIO_PATH, loop_audio=True, audio_delay_sec=args.audio_delay,)
        try:
            while True:
                frame = writer_queue.get()
                if not frame.flags['C_CONTIGUOUS']:
                    frame = np.ascontiguousarray(frame)
                proc.stdin.write(memoryview(frame))
        except (BrokenPipeError, ValueError) as e:
            print(f"[{target_number}] ❌ FFmpeg write error:", e)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()



    def selector_thread():
        nonlocal last_zoomed_frame
    
        state = {
            'source': list(VIDEO_PATHS.keys())[0],
            'frame': None,
            'grace': False,
            'grace_start': None,
            'last_switch_time': time.monotonic(),
            'next_source': None,
            'next_frame': None,
        }

        last_seen_tick = {name: -1 for name in VIDEO_PATHS}
        last_target_seen_time = time.monotonic()  # 마지막 타깃 발견 시각

        while True:
            now = time.monotonic()
            same_source_duration = now - state['last_switch_time']
            found_target_in_another = None
            candidate_frame = None
            seen_this_iter = False  # 이번 루프에서 타깃을 봤는지 여부

            # --- 각 소스의 최신 스냅샷 읽기 ---
            for name in VIDEO_PATHS:
                with state_lock[name]:
                    snap = shared_state[name].copy()
                tick = snap["tick"]
                frame_snap = snap["frame"]
                tracks_snap = snap["tracks"]  # (tid, bbox) 리스트
    
                if tick == last_seen_tick[name] or frame_snap is None:
                    continue  # 새 프레임 없음
                last_seen_tick[name] = tick
    
                # 타깃 탐색
                hit = None
                for tid, bbox in tracks_snap:
                    label = id_to_number[name].get(tid)
                    if label == target_number:
                        hit = (tid, bbox)
                        break
    
                if hit is not None:
                    seen_this_iter = True
                    last_target_seen_time = now
    
                    tid, bbox = hit
                    zoomed = safe_crop_resize_16x9(frame_snap, bbox)
                    if zoomed is None:
                        zoomed = cv2.resize(frame_snap, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
    
                    last_known_bbox[name] = bbox
                    last_known_bbox_global[name] = bbox  # 다른 타겟의 fallback에도 도움
    
                    if name == state['source']:
                        state['frame'] = zoomed
                        state['grace'] = False
                        state['grace_start'] = None
                    else:
                        found_target_in_another = name
                        candidate_frame = zoomed
                        state['next_source'] = name
                        state['next_frame'] = zoomed
                        if not state['grace']:
                            state['grace'] = True
                            state['grace_start'] = now
    
            # --- 현재 소스에서 이번 루프 타깃 미발견 시 → fallback ---
            if latest_full_frame.get(state['source']) is not None:
                fallback = fallback_from_latest(state['source'], last_known_bbox)
                if fallback is None:
                    fallback = fallback_from_latest(state['source'], last_known_bbox_global)
                if fallback is not None:
                    state['frame'] = fallback
    
            # --- 타깃 미검출 1초 경과 시 cam1 풀샷 ---
            if not seen_this_iter and (now - last_target_seen_time) >= 1.0:
                base = latest_full_frame.get(PRIMARY_SOURCE)
                if base is not None:
                    wide = cv2.resize(base, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
                    state['frame'] = wide
                    if state['source'] != PRIMARY_SOURCE:
                        state['source'] = PRIMARY_SOURCE
                        state['last_switch_time'] = now
                    state['grace'] = False
                    state['grace_start'] = None
    
            # --- 유예 1s 만료 시 전환 ---
            if state['grace'] and (time.monotonic() - state['grace_start'] >= 1.0):
                state['source'] = state['next_source']
                state['frame']  = state['next_frame']
                state['last_switch_time'] = time.monotonic()
                state['grace'] = False
                state['grace_start'] = None
                print(f"[{target_number}] 🔁 유예 후 전환: {state['source']}")
    
            # --- 10초 이상 같은 소스 유지 → 즉시 전환 ---
            if same_source_duration >= 10.0 and found_target_in_another and found_target_in_another != state['source']:
                state['source'] = found_target_in_another
                state['frame']  = candidate_frame
                state['last_switch_time'] = time.monotonic()
                state['grace'] = False
                state['grace_start'] = None
                print(f"[{target_number}] ⏱️ 10초 유지 → {found_target_in_another}로 전환")
    
            # --- 최신 프레임 공유 ---
            if state['frame'] is not None:
                with last_frame_lock:
                    last_zoomed_frame['frame'] = state['frame']
    
            time.sleep(0.001)  # 바쁜 루프 방지


    def pacer_thread():
        target_period = 1.0 / FPS_TARGET
        next_t = time.monotonic()
        last_sent = None
        while True:
            now = time.monotonic()
            if now < next_t:
                time.sleep(next_t - now)
            next_t += target_period

            frame = last_sent
            with last_frame_lock:
                if last_zoomed_frame['frame'] is not None:
                    frame = last_zoomed_frame['frame']
            if frame is None:
                continue
            last_sent = frame

            try:
                if not writer_queue.full():
                    writer_queue.put_nowait(frame)
            except queue.Full:
                pass

    threading.Thread(target=writer_thread,   daemon=True).start()
    threading.Thread(target=selector_thread, daemon=True).start()
    threading.Thread(target=pacer_thread,    daemon=True).start()

# ======================= 메인 =======================
def main():
    # 입력 검증: 다중 타겟인데 {num} 없으면 에러
    if len(TARGET_NUMBERS) > 1 and '{num}' not in OUT_TEMPLATE:
        raise ValueError('Multiple targets detected but --output has no "{num}" placeholder.')

    # 비디오 파이프라인 공통부 기동 (디코딩/추론은 1회)
    for name, path in VIDEO_PATHS.items():
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            print(f"[{name}] ❌ Failed to open video.")
            continue
        frame_queues[name] = queue.Queue(maxsize=8)
        trackers[name] = Tracker()
        threading.Thread(target=frame_reader, args=(name, cap), daemon=True).start()
        threading.Thread(target=inference_worker, args=(name,), daemon=True).start()

    # OCR 파이프
    threading.Thread(target=ocr_worker, daemon=True).start()
    threading.Thread(target=ocr_result_collector, daemon=True).start()

    # 타겟별 출력 파이프라인 기동 (writer/selector/pacer만 분기)
    for t in TARGET_NUMBERS:
        out_path = OUT_TEMPLATE.format(num=t) if '{num}' in OUT_TEMPLATE else OUT_TEMPLATE
        print(f"▶️ Target {t} → {out_path}")
        run_target_pipeline(t, out_path)

    # 생존 유지
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()

