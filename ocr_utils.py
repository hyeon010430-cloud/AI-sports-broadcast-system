# ocr_utils.py

import cv2
from paddleocr import PaddleOCR

ocr_model = PaddleOCR(use_angle_cls=False, lang='en', use_gpu=True)

def recognize_number(frame, bbox, conf_threshold=0.6):
    x1, y1, x2, y2 = map(int, bbox)
    h, w = frame.shape[:2]
    x1, x2 = max(0, min(x1, w)), max(0, min(x2, w))
    y1, y2 = max(0, min(y1, h)), max(0, min(y2, h))
    cropped = frame[y1:y2, x1:x2]

    result = ocr_model.ocr(cropped, cls=False)[0]
    for line in result:
        text, conf = line[1]
        if conf >= conf_threshold and text.strip().isdigit() and 0 < int(text) <= 99:
            return text.strip()
    return None

