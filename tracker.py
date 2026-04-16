from yolox.tracker.byte_tracker import BYTETracker
from types import SimpleNamespace
import numpy as np

class TrackWrapper:
    def __init__(self, tid, bbox):
        self.track_id = tid
        self.bbox = bbox  # [x1, y1, x2, y2]

class Tracker:
    def __init__(self):
        args = SimpleNamespace(
            track_thresh=0.5,
            match_thresh=0.8,
            track_buffer=30,
            frame_rate=30,
            mot20=False  # MOT20 데이터셋 사용 시 True로 설정
        )
        self.tracker = BYTETracker(args)
        self.tracks = []

    def update(self, frame, detections):
        """
        detections: list of [x1, y1, x2, y2, score]
        frame: numpy array (H x W x C)
        """
        if len(detections) > 0:
            dets = np.array(detections, dtype=np.float32)
        else:
            dets = np.empty((0, 5), dtype=np.float32)

        online_targets = self.tracker.update(dets, frame.shape, frame.shape)
        self.tracks = [TrackWrapper(t.track_id, t.tlbr) for t in online_targets]

    def get_bbox(self, track_id):
        for track in self.tracks:
            if track.track_id == track_id:
                return track.bbox
        return None

