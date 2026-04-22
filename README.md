# AI Player Tracking and Streaming System

This project implements a real-time player tracking and streaming system using YOLOv8, tracking, and OCR.

It detects players from multiple video sources, tracks them across frames, recognizes jersey numbers, and generates a zoomed stream focused on selected players.

---

## Overview

The system processes video input from multiple cameras and applies object detection, tracking, and OCR to identify players.  
Based on the selected player number, the system automatically crops and streams a focused view using HLS.

---

## Pipeline

Video Input (Multi Camera)  
→ YOLOv8 Detection (Player Bounding Box)  
→ Tracking (ID Assignment)  
→ OCR (Jersey Number Recognition)  
→ Target Player Selection  
→ Zoom and Cropping  
→ HLS Streaming (FFmpeg)

---

## Tech Stack

- Python
- OpenCV
- PyTorch
- YOLOv8 (Ultralytics)
- EasyOCR
- Tracking Algorithm (ByteTrack / DeepSort-based)
- FFmpeg

---

## Requirements

- Python 3.8 or higher
- CUDA (optional, for GPU acceleration)

Install dependencies:
