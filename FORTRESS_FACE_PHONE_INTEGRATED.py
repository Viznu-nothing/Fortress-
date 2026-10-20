import tkinter as tk
import math
import threading
import os
import time
import random
import re
import requests

import sounddevice as sd
import speech_recognition as sr
import pyttsx3
import numpy as np
import cv2
import pickle

from openai import OpenAI
from datetime import datetime


# ============================================================
# FORTRESS — SECURITY INTELLIGENCE SYSTEM
# ============================================================

BG = "#02070C"
PANEL = "#06121A"
PANEL_DARK = "#081923"

CYAN = "#20D9FF"
CYAN_DIM = "#0A6D8A"
WHITE = "#EAF7FF"
MUTED = "#638391"
GREEN = "#16E6A0"
RED = "#FF5268"
LINE = "#123848"

BASE_W = 1600
BASE_H = 900

LAYERS = [
    ("01", "FACE VERIFICATION"),
    ("02", "PALM VERIFICATION"),
    ("03", "PHONE PIN"),
    ("04", "CARD VERIFICATION"),
]

state = {
    "FACE VERIFICATION": "PENDING",
    "PALM VERIFICATION": "LOCKED",
    "PHONE PIN": "LOCKED",
    "CARD VERIFICATION": "LOCKED",
    "VAULT": "LOCKED",
}

events = [
    (
        datetime.now().strftime("%H:%M:%S"),
        "SYSTEM",
        "INITIALIZED"
    )
]


# ============================================================
# VOICE SYSTEM
# ============================================================

MIC_DEVICE = None  # Auto-select a valid input device at runtime
SAMPLE_RATE = 16000

BLOCK_DURATION = 0.10
SILENCE_DURATION = 1.0
MAX_SPEECH_DURATION = 12.0
WAIT_FOR_SPEECH_TIMEOUT = 8.0

VOICE_THRESHOLD = 450
VOICE_RESTART_DELAY = 0.5


def choose_microphone_device():
    """Return a valid input device and channel count for sounddevice."""
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0]
        if default_input is not None and int(default_input) >= 0:
            info = sd.query_devices(int(default_input))
            if int(info.get("max_input_channels", 0)) >= 1:
                return int(default_input), 1

        for index, info in enumerate(devices):
            if int(info.get("max_input_channels", 0)) >= 1:
                return index, 1

        raise RuntimeError("No microphone/input device with at least one input channel was found.")
    except Exception as exc:
        raise RuntimeError(f"Could not select a valid microphone input device: {exc}") from exc

speech_recognizer = sr.Recognizer()

try:

    tts_engine = pyttsx3.init()

    tts_engine.setProperty(
        "rate",
        170
    )

    tts_engine.setProperty(
        "volume",
        1.0
    )

except Exception as error:

    print(
        "TTS initialization error:",
        error
    )

    tts_engine = None


# ============================================================
# NEXUS AI — OPENAI
# ============================================================

OPENAI_CLIENT = OpenAI()

CURRENT_FOLDER = os.path.dirname(
    os.path.abspath(__file__)
)

KNOWLEDGE_FILE = os.path.join(
    CURRENT_FOLDER,
    "NEXUS_KNOWLEDGE.txt"
)

if not os.path.exists(KNOWLEDGE_FILE):

    KNOWLEDGE_FILE = os.path.join(
        CURRENT_FOLDER,
        "NEXUS",
        "NEXUS_KNOWLEDGE.txt"
    )

try:

    with open(
        KNOWLEDGE_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        NEXUS_KNOWLEDGE = f.read()

except Exception:

    NEXUS_KNOWLEDGE = (
        "FORTRESS Knowledge Core is currently unavailable."
    )


# ============================================================
# FINAL SEQUENCE STAGE (hidden — never named in the interface)
# After the QR passes, 6 random symbols flash on the laptop for
# 3 seconds. Typing them on the phone is always rejected; only the
# spoken secret phrase unlocks the vault.
# ============================================================

SEQUENCE_SYMBOLS = "?@#&*%$!+=<>~^"
SEQUENCE_LENGTH = 6
SEQUENCE_FLASH_MS = 3000


def is_secret_phrase(text):
    """Match "i want to eat your pancreas", tolerant of speech-to-text noise."""
    words = re.sub(r"[^a-z ]", " ", text.lower()).split()
    joined = " ".join(words)
    return "pancreas" in words and ("eat your" in joined or "want to eat" in joined)


#
# YuNet       -> face detection
# MiniFASNet  -> presentation attack / spoof detection
# SFace       -> identity verification
# ============================================================

CONTROLLER_URL = "http://127.0.0.1:5001"

# Model files are looked up in these folders, in order.
FACE_MODEL_DIRS = [
    CURRENT_FOLDER,
    os.path.join(CURRENT_FOLDER, "NEXUS"),
    r"C:\Users\VISHNU VARDHAN\Downloads\NEXUS",
]

FACE_MODEL_FILES = {
    "yunet": "face_detection_yunet_2023mar.onnx",
    "antispoof": "2.7_80x80_MiniFASNetV2.onnx",
    "sface": "face_recognition_sface_2021dec.onnx",
    "enrolled": os.path.join("face_data", "enrolled_face.jpg"),
}

FACE_WINDOW_NAME = "FORTRESS // NEXUS FACE VERIFICATION"
FACE_CAMERA_INDEX = 0

FACE_CONFIDENCE = 0.65
IDENTITY_THRESHOLD = 0.363     # OpenCV's documented cosine threshold for SFace
REAL_THRESHOLD = 0.45          # MiniFASNet class 1 = REAL

LIVE_REQUIRED = 2
IDENTITY_REQUIRED = 2
SPOOF_REQUIRED = 3

# A live face that does not match the enrolled identity for this long
# is denied and counted as a failed attempt.
FACE_MISMATCH_SECONDS = 8

# OpenCV colors (BGR)
CV_CYAN = (30, 220, 255)
CV_GREEN = (40, 235, 150)
CV_RED = (60, 70, 255)
CV_WHITE = (235, 247, 255)
CV_GRAY = (100, 130, 145)
CV_BLACK = (5, 8, 12)


def notify_controller(path, payload=None):
    try:
        requests.post(CONTROLLER_URL + path, json=payload, timeout=1.5)
    except Exception:
        pass


def find_face_model_dir():
    for folder in FACE_MODEL_DIRS:
        if all(
            os.path.isfile(os.path.join(folder, name))
            for name in FACE_MODEL_FILES.values()
        ):
            return folder
    return None


def get_antispoof_crop(frame, face):
    """Expanded face region used by the 2.7x MiniFASNetV2 model."""
    x, y, w, h = (float(v) for v in face[:4])

    if w <= 1 or h <= 1:
        return None

    scale = 2.7
    cx, cy = x + w / 2.0, y + h / 2.0
    new_w, new_h = w * scale, h * scale

    x1 = max(0, int(cx - new_w / 2.0))
    y1 = max(0, int(cy - new_h / 2.0))
    x2 = min(frame.shape[1], int(cx + new_w / 2.0))
    y2 = min(frame.shape[0], int(cy + new_h / 2.0))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = frame[y1:y2, x1:x2]
    return crop if crop.size else None


def check_liveness(spoof_session, spoof_input_name, frame, face):
    crop = get_antispoof_crop(frame, face)

    if crop is None:
        return False, 0.0

    try:
        crop = cv2.resize(crop, (80, 80), interpolation=cv2.INTER_LINEAR)

        # MiniFASNet preprocessing: BGR, float32, 0-255, NCHW, NO /255
        blob = crop.astype(np.float32)
        blob = np.transpose(blob, (2, 0, 1))
        blob = np.expand_dims(blob, axis=0)

        scores = spoof_session.run(None, {spoof_input_name: blob})[0][0]

        # 0 = spoof, 1 = real, 2 = spoof
        label = int(np.argmax(scores))
        real_score = float(scores[1])

        return label == 1 and real_score >= REAL_THRESHOLD, real_score

    except Exception as error:
        print("Liveness inference error:", error)
        return False, 0.0


def cv_text(image, message, x, y, color=CV_WHITE, scale=0.7, thickness=2):
    cv2.putText(
        image, message, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
        scale, color, thickness, cv2.LINE_AA
    )


def draw_face_box(image, face, color):
    x, y, w, h = (int(v) for v in face[:4])
    cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)

    # Corner brackets
    length = 18
    for cx, cy, dx, dy in (
        (x, y, 1, 1), (x + w, y, -1, 1),
        (x, y + h, 1, -1), (x + w, y + h, -1, -1)
    ):
        cv2.line(image, (cx, cy), (cx + dx * length, cy), color, 3)
        cv2.line(image, (cx, cy), (cx, cy + dy * length), color, 3)


def run_face_verification():
    """
    Run live face verification on the laptop webcam.
    Returns "VERIFIED", "MISMATCH" (wrong person), "CANCELLED" (ESC
    pressed) or "ERROR: <reason>".
    A detected spoof is reported to the controller as denied while the
    scan keeps going, matching face_verify_test.py.
    """
    import onnxruntime as ort

    model_dir = find_face_model_dir()

    if model_dir is None:
        return "ERROR: face models / face_data/enrolled_face.jpg not found"

    paths = {
        key: os.path.join(model_dir, name)
        for key, name in FACE_MODEL_FILES.items()
    }

    print()
    print("FORTRESS // NEXUS")
    print("Loading face security models from", model_dir)

    detector = cv2.FaceDetectorYN.create(
        paths["yunet"], "", (320, 320), FACE_CONFIDENCE, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF.create(paths["sface"], "")
    spoof_session = ort.InferenceSession(
        paths["antispoof"], providers=["CPUExecutionProvider"]
    )
    spoof_input_name = spoof_session.get_inputs()[0].name

    # Enrolled identity
    enrolled_image = cv2.imread(paths["enrolled"])

    if enrolled_image is None:
        return "ERROR: could not read enrolled_face.jpg"

    detector.setInputSize((enrolled_image.shape[1], enrolled_image.shape[0]))
    _, enrolled_faces = detector.detect(enrolled_image)

    if enrolled_faces is None or len(enrolled_faces) != 1:
        return "ERROR: enrolled_face.jpg must contain exactly one face"

    enrolled_feature = recognizer.feature(
        recognizer.alignCrop(enrolled_image, enrolled_faces[0])
    )

    print("Enrolled identity ready.")

    # Camera
    cap = cv2.VideoCapture(FACE_CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(FACE_CAMERA_INDEX)
    if not cap.isOpened():
        return "ERROR: could not open webcam"

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    SEARCHING, LIVENESS, IDENTITY, VERIFIED, DENIED = range(5)

    scan_state = SEARCHING
    live_count = spoof_count = identity_count = 0
    identity_score = liveness_score = 0.0
    denied_time = verified_time = None
    identity_start = None
    result = "CANCELLED"

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            # Wrong identity: close once the denial has been shown.
            if result == "MISMATCH" and time.time() - denied_time >= 1.5:
                break

            # Detection on the original frame, mirrored display
            detector.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = detector.detect(frame)

            display = cv2.flip(frame, 1)
            height, width = display.shape[:2]

            cv_text(display, "FORTRESS // NEXUS", 25, 38, CV_CYAN, 0.82, 2)
            cv_text(display, "LIVE IDENTITY VERIFICATION", 25, 65, CV_GRAY, 0.45, 1)

            # Multiple faces
            if faces is not None and len(faces) > 1:
                if scan_state != DENIED:
                    scan_state = DENIED
                    denied_time = time.time()

                cv_text(display, "TWO FACES DETECTED", 25, height - 70, CV_RED, 0.82, 2)
                cv_text(display, "ACCESS DENIED", 25, height - 38, CV_RED, 0.55, 1)
                cv2.imshow(FACE_WINDOW_NAME, display)

                if cv2.waitKey(1) & 0xFF == 27:
                    break
                continue

            # No face
            if faces is None or len(faces) == 0:
                if scan_state != VERIFIED:
                    scan_state = SEARCHING
                    live_count = spoof_count = identity_count = 0

                cv_text(display, "SEARCHING FOR FACE", 25, height - 50, CV_CYAN, 0.65, 2)
                cv2.imshow(FACE_WINDOW_NAME, display)

                if cv2.waitKey(1) & 0xFF == 27:
                    break
                continue

            # Exactly one face
            face = faces[0]

            if scan_state == VERIFIED:
                box_color = CV_GREEN
            elif scan_state == DENIED:
                box_color = CV_RED
            else:
                box_color = CV_CYAN

            draw_face_box(display, face, box_color)

            if scan_state == SEARCHING:
                live_count = spoof_count = identity_count = 0
                scan_state = LIVENESS

            # Live anti-spoof
            if scan_state == LIVENESS:
                is_real, liveness_score = check_liveness(
                    spoof_session, spoof_input_name, frame, face
                )

                if is_real:
                    live_count += 1
                    spoof_count = 0
                else:
                    spoof_count += 1
                    live_count = 0

                cv_text(display, "ANALYZING LIVE PRESENCE", 25, height - 78, CV_CYAN, 0.62, 2)
                cv_text(display, f"LIVENESS {liveness_score:.3f}", 25, height - 45,
                        CV_GREEN if is_real else CV_GRAY, 0.48, 1)

                if live_count >= LIVE_REQUIRED:
                    identity_count = 0
                    identity_start = time.time()
                    scan_state = IDENTITY

                elif spoof_count >= SPOOF_REQUIRED:
                    scan_state = DENIED
                    notify_controller("/face_denied", {"reason": "spoof detected"})
                    denied_time = time.time()

            # SFace identity
            if scan_state == IDENTITY:
                try:
                    live_feature = recognizer.feature(
                        recognizer.alignCrop(frame, face)
                    )
                    identity_score = float(
                        recognizer.match(
                            enrolled_feature, live_feature,
                            cv2.FaceRecognizerSF_FR_COSINE
                        )
                    )
                except Exception:
                    identity_score = 0.0

                if identity_score >= IDENTITY_THRESHOLD:
                    identity_count += 1
                else:
                    identity_count = 0

                cv_text(display, "VERIFYING IDENTITY", 25, height - 78, CV_CYAN, 0.62, 2)
                cv_text(display, f"IDENTITY MATCH {identity_score:.3f}", 25, height - 45,
                        CV_GREEN if identity_score >= IDENTITY_THRESHOLD else CV_GRAY, 0.48, 1)

                if identity_count >= IDENTITY_REQUIRED:
                    scan_state = VERIFIED
                    result = "VERIFIED"
                    notify_controller("/face_verified")
                    verified_time = time.time()

                elif time.time() - identity_start >= FACE_MISMATCH_SECONDS:
                    # Live person, wrong identity.
                    scan_state = DENIED
                    result = "MISMATCH"
                    denied_time = time.time()

            if scan_state == VERIFIED:
                cv_text(display, "IDENTITY VERIFIED", 25, height - 72, CV_GREEN, 0.88, 2)
                cv_text(display, "LIVE SUBJECT AUTHENTICATED", 25, height - 40, CV_GREEN, 0.50, 1)

            if scan_state == DENIED:
                cv_text(display, "ACCESS DENIED", 25, height - 72, CV_RED, 0.88, 2)
                cv_text(display, "LIVE IDENTITY NOT VERIFIED", 25, height - 40, CV_RED, 0.50, 1)

                # Keep denial visible briefly, then scan again
                # (or close the window after a wrong identity).
                if denied_time is not None and time.time() - denied_time >= 1.5:
                    if result == "MISMATCH":
                        break
                    scan_state = SEARCHING
                    denied_time = None

            # NEXUS status panel
            panel_x = width - 245
            cv2.rectangle(display, (panel_x, 18), (width - 18, 115), CV_BLACK, -1)
            cv2.rectangle(display, (panel_x, 18), (width - 18, 115), CV_CYAN, 1)
            cv_text(display, "NEXUS", panel_x + 20, 47, CV_CYAN, 0.58, 2)

            if scan_state == VERIFIED:
                status, status_color = "VERIFIED", CV_GREEN
            elif scan_state == DENIED:
                status, status_color = "DENIED", CV_RED
            else:
                status, status_color = "SCANNING", CV_CYAN

            cv_text(display, status, panel_x + 20, 82, status_color, 0.62, 2)

            cv2.imshow(FACE_WINDOW_NAME, display)

            if cv2.waitKey(1) & 0xFF == 27:
                break

            # Auto close after verification
            if scan_state == VERIFIED and time.time() - verified_time >= 1.0:
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()

    return result


# ============================================================
# PALM VERIFICATION — laptop webcam (from palm_verify_test.py)
# Uses hand_landmarker.task + face_data/enrolled_palm.pkl
# ============================================================

PALM_MODEL_FILE = "hand_landmarker.task"
PALM_PROFILE_FILE = os.path.join("face_data", "enrolled_palm.pkl")
PALM_WINDOW_NAME = "FORTRESS - Palm Verification"

MATCH_THRESHOLD = 0.985
REQUIRED_MATCHES = 8

# A palm that is in view but does not match for this long (in total)
# is denied and counted as a failed attempt.
PALM_MISMATCH_SECONDS = 8

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17)
]


def find_palm_files():
    # Same folders as the face models.
    for folder in FACE_MODEL_DIRS:
        model_path = os.path.join(folder, PALM_MODEL_FILE)
        profile_path = os.path.join(folder, PALM_PROFILE_FILE)
        if os.path.isfile(model_path) and os.path.isfile(profile_path):
            return model_path, profile_path
    return None, None


def run_palm_verification():
    """
    Run live palm verification on the laptop webcam.
    Returns "VERIFIED", "MISMATCH" (wrong palm), "CANCELLED" (Q pressed)
    or "ERROR: <reason>".
    """
    import mediapipe as mp

    model_path, profile_path = find_palm_files()

    if model_path is None:
        return "ERROR: hand_landmarker.task / face_data/enrolled_palm.pkl not found"

    # Load enrolled biometric profile
    with open(profile_path, "rb") as f:
        profile = pickle.load(f)

    enrolled_template = np.asarray(profile["template"], dtype=np.float32)

    # MediaPipe Tasks API
    options = mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.65,
        min_hand_presence_confidence=0.65,
        min_tracking_confidence=0.65
    )

    landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)

    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        landmarker.close()
        return "ERROR: could not open webcam"

    print()
    print("=" * 60)
    print("FORTRESS — PALM BIOMETRIC VERIFICATION")
    print("=" * 60)
    print("Show the SAME palm that was enrolled. Press Q to quit.")

    start_time = time.time()
    scores = []
    consecutive_matches = 0
    verified = False
    result_text = "CANCELLED"
    mismatch_seconds = 0.0
    last_frame_time = time.time()

    try:
        while True:
            ret, frame = camera.read()

            if not ret:
                result_text = "ERROR: camera frame unavailable"
                break

            now = time.time()
            frame_dt, last_frame_time = now - last_frame_time, now

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int((time.time() - start_time) * 1000)

            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            status = "SEARCHING FOR PALM"
            score_text = "MATCH: --"

            if result.hand_landmarks:
                hand = result.hand_landmarks[0]

                # Extract 21 landmarks, normalize relative to wrist and scale
                points = np.asarray(
                    [[lm.x, lm.y, lm.z] for lm in hand],
                    dtype=np.float32
                )
                points -= points[0].copy()
                scale = np.linalg.norm(points[9])

                if scale > 1e-6:
                    points /= scale
                    live_vector = points.flatten()
                    norm = np.linalg.norm(live_vector)

                    if norm > 1e-6:
                        live_vector = live_vector / norm

                        # Cosine similarity
                        similarity = float(np.dot(enrolled_template, live_vector))
                        similarity = max(-1.0, min(1.0, similarity))

                        scores.append(similarity)
                        if len(scores) > 10:
                            scores.pop(0)

                        average_score = float(np.mean(scores))
                        score_text = f"MATCH: {average_score:.4f}"

                        if average_score >= MATCH_THRESHOLD:
                            consecutive_matches += 1
                            status = f"PALM MATCHING {consecutive_matches}/{REQUIRED_MATCHES}"
                        else:
                            consecutive_matches = 0
                            status = "PALM DETECTED — NO MATCH"
                            mismatch_seconds += frame_dt

                        if consecutive_matches >= REQUIRED_MATCHES:
                            verified = True
                            status = "PALM VERIFIED"

                    # Draw landmarks
                    h, w = frame.shape[:2]

                    for lm in hand:
                        cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 4, (0, 220, 255), -1)

                    for a, b in HAND_CONNECTIONS:
                        cv2.line(
                            frame,
                            (int(hand[a].x * w), int(hand[a].y * h)),
                            (int(hand[b].x * w), int(hand[b].y * h)),
                            (0, 180, 255),
                            2
                        )

            else:
                consecutive_matches = 0
                scores.clear()
                status = "NO PALM DETECTED"

            cv2.putText(frame, "FORTRESS PALM VERIFICATION", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
            cv2.putText(frame, status, (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(frame, score_text, (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 220, 230), 2)
            cv2.putText(frame, "Press Q to exit", (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 180, 190), 2)

            cv2.imshow(PALM_WINDOW_NAME, frame)

            # Stop after successful verification
            if verified:
                print("PALM VERIFIED — live palm matches enrolled biometric profile.")
                result_text = "VERIFIED"
                cv2.waitKey(1500)
                break

            if mismatch_seconds >= PALM_MISMATCH_SECONDS:
                print("PALM NOT RECOGNISED — access denied.")
                cv2.putText(frame, "PALM NOT RECOGNISED", (20, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 70, 255), 2)
                cv2.imshow(PALM_WINDOW_NAME, frame)
                cv2.waitKey(1500)
                result_text = "MISMATCH"
                break

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Palm verification cancelled.")
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()
        landmarker.close()

    return result_text


# ============================================================
# QR CARD VERIFICATION — laptop webcam, demo-only
# The QR is NOT decoded or checked. The scan animation always
# completes; a QR in view just makes the progress run faster.
# ============================================================

QR_WINDOW_NAME = "FORTRESS - QR Verification"
DEMO_CARD_OWNER = "VISHNU VARDHAN"
DEMO_CARD_ID = "FRT-V001"


def draw_qr_scan_overlay(frame, progress, status, qr_points, now):
    h, w = frame.shape[:2]
    cyan, green, white = (255, 217, 32), (160, 230, 22), (255, 247, 234)

    # Dim everything outside the target box.
    box = int(min(w, h) * 0.62)
    x1, y1 = (w - box) // 2, (h - box) // 2
    x2, y2 = x1 + box, y1 + box
    shade = (frame * 0.35).astype(np.uint8)
    shade[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
    frame[:] = shade

    # Corner brackets.
    arm = box // 7
    for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + dx * arm, cy), cyan, 3)
        cv2.line(frame, (cx, cy), (cx, cy + dy * arm), cyan, 3)

    # Sweeping scan line with a fading trail.
    sweep = (math.sin(now * 3.0) + 1) / 2
    line_y = int(y1 + sweep * box)
    for i in range(12):
        alpha = 1.0 - i / 12
        ty = line_y - i * 3 if math.cos(now * 3.0) > 0 else line_y + i * 3
        if y1 <= ty <= y2:
            color = tuple(int(c * alpha) for c in cyan)
            cv2.line(frame, (x1 + 4, ty), (x2 - 4, ty), color, 1)
    cv2.line(frame, (x1 + 4, line_y), (x2 - 4, line_y), white, 2)

    if qr_points is not None:
        pts = qr_points.reshape(-1, 2).astype(np.int32)
        cv2.polylines(frame, [pts], True, green, 3)
        cv2.putText(frame, "QR LOCKED", (int(pts[:, 0].min()), int(pts[:, 1].min()) - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, green, 2)

    # Header, status and progress bar.
    cv2.putText(frame, "FORTRESS QR VERIFICATION", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cyan, 2)
    cv2.putText(frame, status, (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.65, white, 2)
    bar_x1, bar_x2, bar_y = 20, w - 20, h - 40
    cv2.rectangle(frame, (bar_x1, bar_y), (bar_x2, bar_y + 14), (72, 56, 18), 1)
    fill = bar_x1 + int((bar_x2 - bar_x1) * min(progress, 1.0))
    cv2.rectangle(frame, (bar_x1 + 2, bar_y + 2), (max(bar_x1 + 2, fill - 2), bar_y + 12), cyan, -1)
    cv2.putText(frame, f"{int(min(progress, 1.0) * 100)}%", (bar_x2 - 50, bar_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, white, 1)
    cv2.putText(frame, "Press Q to cancel", (20, h - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (190, 180, 150), 1)


def run_qr_verification():
    """
    Show the QR scanning animation on the laptop webcam.
    Returns "VERIFIED", "CANCELLED" (Q pressed) or "ERROR: <reason>".
    """
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        return "ERROR: could not open webcam"

    detector = cv2.QRCodeDetector()
    stages = [
        (0.00, "SEARCHING FOR FORTRESS QR..."),
        (0.25, "QR PATTERN ACQUIRED"),
        (0.50, "DECODING SECURE PAYLOAD..."),
        (0.75, "MATCHING ENROLLED CARD DATABASE..."),
    ]
    progress = 0.0
    qr_points = None
    last = time.time()

    try:
        while True:
            ret, frame = camera.read()
            if not ret:
                return "ERROR: camera frame unavailable"

            now = time.time()
            dt, last = now - last, now

            qr_points = None
            try:
                found, points = detector.detect(frame)
                if found and points is not None:
                    qr_points = points
            except cv2.error:
                pass

            # ~3 s with a QR in view, ~8 s without one; it always finishes.
            progress += dt * (0.33 if qr_points is not None else 0.12)
            status = [text for start, text in stages if progress >= start][-1]

            if progress >= 1.0:
                end = time.time() + 1.8
                while time.time() < end:
                    ret, frame = camera.read()
                    if not ret:
                        break
                    draw_qr_scan_overlay(frame, 1.0, "QR VERIFIED", qr_points, time.time())
                    h, w = frame.shape[:2]
                    cv2.putText(frame, "ACCESS GRANTED", (w // 2 - 150, h // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (160, 230, 22), 3)
                    cv2.putText(frame, f"{DEMO_CARD_ID} // {DEMO_CARD_OWNER}", (w // 2 - 150, h // 2 + 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 247, 234), 2)
                    cv2.imshow(QR_WINDOW_NAME, frame)
                    cv2.waitKey(30)
                return "VERIFIED"

            draw_qr_scan_overlay(frame, progress, status, qr_points, now)
            cv2.imshow(QR_WINDOW_NAME, frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                return "CANCELLED"

    finally:
        camera.release()
        cv2.destroyAllWindows()


class FortressHUD:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "FORTRESS: SECURITY INTELLIGENCE SYSTEM"
        )

        self.root.configure(
            bg=BG
        )

        # ====================================================
        # FULLSCREEN
        # ====================================================

        self.fullscreen = True

        self.root.attributes(
            "-fullscreen",
            True
        )

        self.root.bind(
            "<Escape>",
            self.exit_fullscreen
        )

        self.root.bind(
            "<F11>",
            self.toggle_fullscreen
        )

        # ====================================================
        # CANVAS
        # ====================================================

        self.canvas = tk.Canvas(
            root,
            bg=BG,
            highlightthickness=0,
            bd=0
        )

        self.canvas.pack(
            fill="both",
            expand=True
        )

        # ====================================================
        # SCALING
        # ====================================================

        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        # ====================================================
        # ANIMATION
        # ====================================================

        self.rotation = 0.0
        self.pulse = 0.0

        self.rings = []
        self.core_id = None

        self.clock_id = None
        self.date_id = None

        # ====================================================
        # CHAT
        # ====================================================

        self.chat_display = None
        self.chat_entry = None
        self.chat_entry_window = None

        # ====================================================
        # VOICE CONTROL
        # ====================================================

        self.voice_enabled = True
        self.voice_busy = False
        self.voice_thread_started = False

        # ====================================================
        # FACE VERIFICATION (laptop webcam)
        # ====================================================

        self.face_scan_running = False
        self.palm_scan_running = False
        self.qr_scan_running = False
        self.sequence_active = False
        self.sequence_window = None

        # Intrusion backups reported by the controller
        self.backups_seen = 0
        self.security_alert = None

        self.root.bind(
            "<F2>",
            self.start_face_verification
        )

        # ====================================================
        # BUILD
        # ====================================================

        self.ready = False
        self.rebuild_job = None

        self.root.after(
            250,
            self.initial_build
        )

    # ========================================================
    # FULLSCREEN
    # ========================================================

    def exit_fullscreen(
        self,
        event=None
    ):

        self.fullscreen = False

        self.root.attributes(
            "-fullscreen",
            False
        )

        try:

            self.root.state(
                "zoomed"
            )

        except Exception:
            pass

        self.request_rebuild()

    def toggle_fullscreen(
        self,
        event=None
    ):

        self.fullscreen = not self.fullscreen

        self.root.attributes(
            "-fullscreen",
            self.fullscreen
        )

        if not self.fullscreen:

            try:

                self.root.state(
                    "zoomed"
                )

            except Exception:
                pass

        self.request_rebuild()

    # ========================================================
    # COORDINATES
    # ========================================================

    def X(self, x):

        return (
            self.offset_x
            + x * self.scale
        )

    def Y(self, y):

        return (
            self.offset_y
            + y * self.scale
        )

    # ========================================================
    # INITIAL BUILD
    # ========================================================

    def initial_build(self):

        self.build_interface()

        self.ready = True

        self.animate()

        self.root.after(
            1500,
            self.start_voice_system
        )

        # Poll the existing phone controller without blocking the HUD.
        self.root.after(700, self.poll_phone_face_state)

    # ========================================================
    # REQUEST REBUILD
    # ========================================================

    def request_rebuild(self):

        if self.rebuild_job:

            try:

                self.root.after_cancel(
                    self.rebuild_job
                )

            except Exception:
                pass

        self.rebuild_job = self.root.after(
            100,
            self.build_interface
        )

    # ========================================================
    # BUILD INTERFACE
    # ========================================================

    def build_interface(self):

        self.rebuild_job = None

        self.canvas.delete(
            "all"
        )

        self.rings = []
        self.core_id = None
        self.clock_id = None
        self.date_id = None

        self.chat_display = None
        self.chat_entry = None
        self.chat_entry_window = None

        self.canvas.update_idletasks()

        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()

        if width < 200 or height < 200:

            self.root.after(
                100,
                self.build_interface
            )

            return

        # ====================================================
        # PROPORTIONAL FIT
        # ====================================================

        margin = 12

        self.scale = min(
            (width - margin * 2) / BASE_W,
            (height - margin * 2) / BASE_H
        )

        scaled_w = BASE_W * self.scale
        scaled_h = BASE_H * self.scale

        self.offset_x = (
            width - scaled_w
        ) / 2

        self.offset_y = (
            height - scaled_h
        ) / 2

        # ====================================================
        # DRAW
        # ====================================================

        self.draw_background()
        self.draw_main_frame()
        self.draw_header()
        self.draw_authentication()
        self.draw_nexus()
        self.draw_security()
        self.draw_event_log()
        self.draw_footer()

        # ====================================================
        # SCALE ONCE
        # ====================================================

        self.canvas.scale(
            "all",
            0,
            0,
            self.scale,
            self.scale
        )

        self.canvas.move(
            "all",
            self.offset_x,
            self.offset_y
        )

        self.root.after_idle(
            self.fix_chat_window_scale
        )

    # ========================================================
    # DRAWING HELPERS
    # ========================================================

    def polygon(
        self,
        points,
        fill=None,
        outline=None,
        width=1
    ):

        return self.canvas.create_polygon(
            points,
            fill=fill,
            outline=outline,
            width=width
        )

    def line(
        self,
        x1,
        y1,
        x2,
        y2,
        fill=LINE,
        width=1
    ):

        return self.canvas.create_line(
            x1,
            y1,
            x2,
            y2,
            fill=fill,
            width=width
        )

    def rectangle(
        self,
        x1,
        y1,
        x2,
        y2,
        fill=PANEL,
        outline=LINE,
        width=1
    ):

        return self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            fill=fill,
            outline=outline,
            width=width
        )

    def text(
        self,
        x,
        y,
        value,
        size=10,
        fill=WHITE,
        anchor="center",
        bold=False
    ):

        return self.canvas.create_text(
            x,
            y,
            text=value,
            fill=fill,
            anchor=anchor,
            font=(
                "Segoe UI",
                size,
                "bold" if bold else "normal"
            )
        )

    # ========================================================
    # BACKGROUND
    # ========================================================

    def draw_background(self):

        self.rectangle(
            0,
            0,
            BASE_W,
            BASE_H,
            fill=BG,
            outline=BG
        )

        for x in range(
            0,
            BASE_W + 1,
            80
        ):

            self.line(
                x,
                0,
                x,
                BASE_H,
                fill="#06131B"
            )

        for y in range(
            0,
            BASE_H + 1,
            80
        ):

            self.line(
                0,
                y,
                BASE_W,
                y,
                fill="#06131B"
            )

    # ========================================================
    # CONNECTED OUTER FRAME
    # ========================================================

    def draw_main_frame(self):

        outer = [
            45, 25,
            1555, 25,
            1575, 45,
            1575, 855,
            1555, 875,
            45, 875,
            25, 855,
            25, 45
        ]

        self.polygon(
            outer,
            fill="#030A10",
            outline=CYAN_DIM,
            width=2
        )

        inner = [
            52, 32,
            1548, 32,
            1568, 52,
            1568, 848,
            1548, 868,
            52, 868,
            32, 848,
            32, 52
        ]

        self.polygon(
            inner,
            fill="",
            outline="#0B3342",
            width=1
        )

        self.line(
            45,
            120,
            1555,
            120,
            fill=LINE
        )

        self.line(
            45,
            700,
            1555,
            700,
            fill=LINE
        )

        self.line(
            445,
            135,
            445,
            685,
            fill=LINE
        )

        self.line(
            1155,
            135,
            1155,
            685,
            fill=LINE
        )

        self.line(
            25,
            45,
            85,
            45,
            fill=CYAN_DIM,
            width=2
        )

        self.line(
            1515,
            45,
            1575,
            45,
            fill=CYAN_DIM,
            width=2
        )

        self.line(
            25,
            855,
            85,
            855,
            fill=CYAN_DIM,
            width=2
        )

        self.line(
            1515,
            855,
            1575,
            855,
            fill=CYAN_DIM,
            width=2
        )

    # ========================================================
    # HEADER
    # ========================================================

    def draw_header(self):

        self.text(
            65,
            62,
            "FORTRESS",
            size=23,
            fill=WHITE,
            anchor="w",
            bold=True
        )

        self.text(
            65,
            91,
            "SECURITY INTELLIGENCE SYSTEM",
            size=11,
            fill=MUTED,
            anchor="w",
            bold=True
        )

        self.rectangle(
            625,
            38,
            975,
            103,
            fill=PANEL_DARK,
            outline=LINE
        )

        self.text(
            800,
            52,
            "YOUR SECURITY · OUR PRIORITY",
            size=8,
            fill=MUTED,
            bold=True
        )

        self.clock_id = self.text(
            800,
            78,
            datetime.now().strftime(
                "%H:%M:%S"
            ),
            size=18,
            fill=CYAN,
            bold=True
        )

        self.canvas.create_oval(
            1290,
            48,
            1301,
            59,
            fill=GREEN,
            outline=GREEN
        )

        self.text(
            1312,
            53,
            "SYSTEM ONLINE",
            size=9,
            fill=GREEN,
            anchor="w",
            bold=True
        )

        self.date_id = self.text(
            1535,
            80,
            datetime.now().strftime(
                "%d %B %Y"
            ),
            size=8,
            fill=MUTED,
            anchor="e"
        )

    # ========================================================
    # AUTHENTICATION
    # ========================================================

    def draw_authentication(self):

        left = 65
        right = 415

        self.text(
            left,
            150,
            "AUTHENTICATION",
            size=13,
            fill=CYAN,
            anchor="w",
            bold=True
        )

        self.text(
            right,
            150,
            "04 LAYERS",
            size=8,
            fill=MUTED,
            anchor="e",
            bold=True
        )

        card_y = [
            175,
            295,
            415,
            535
        ]

        for index, (
            number,
            name
        ) in enumerate(LAYERS):

            self.draw_auth_card(
                left,
                card_y[index],
                right,
                card_y[index] + 92,
                number,
                name
            )

    def draw_auth_card(
        self,
        x1,
        y1,
        x2,
        y2,
        number,
        name
    ):

        current = state[name]

        if current == "PASSED":

            color = GREEN

        elif current in ("PENDING", "SCANNING"):

            color = CYAN

        elif current == "FAILED":

            color = "#FF5268"

        else:

            color = MUTED

        cut = 10

        points = [
            x1 + cut, y1,
            x2 - cut, y1,
            x2, y1 + cut,
            x2, y2 - cut,
            x2 - cut, y2,
            x1 + cut, y2,
            x1, y2 - cut,
            x1, y1 + cut
        ]

        self.polygon(
            points,
            fill=PANEL,
            outline=LINE,
            width=1
        )

        self.text(
            x1 + 20,
            y1 + 25,
            number,
            size=9,
            fill=color,
            anchor="w",
            bold=True
        )

        self.text(
            x1 + 62,
            y1 + 25,
            name,
            size=9,
            fill=WHITE,
            anchor="w",
            bold=True
        )

        self.text(
            x1 + 62,
            y1 + 57,
            current,
            size=8,
            fill=color,
            anchor="w"
        )

        self.canvas.create_oval(
            x2 - 31,
            y1 + 40,
            x2 - 20,
            y1 + 51,
            fill=color,
            outline=color
        )

        # Clickable VERIFY QR button once the phone PIN has passed.
        if name == "CARD VERIFICATION" and current in ("PENDING", "FAILED"):

            button = self.canvas.create_rectangle(
                x2 - 150,
                y1 + 14,
                x2 - 45,
                y1 + 38,
                fill="#0A2733",
                outline=CYAN,
                width=1,
                tags=("verify_qr",)
            )

            label = self.canvas.create_text(
                x2 - 97,
                y1 + 26,
                text="VERIFY QR",
                fill=CYAN,
                font=("Segoe UI", 8, "bold"),
                tags=("verify_qr",)
            )

            self.canvas.tag_bind(
                "verify_qr",
                "<Button-1>",
                self.on_verify_qr_click
            )

            self.canvas.tag_bind(
                "verify_qr",
                "<Enter>",
                lambda event: self.canvas.configure(cursor="hand2")
            )

            self.canvas.tag_bind(
                "verify_qr",
                "<Leave>",
                lambda event: self.canvas.configure(cursor="")
            )

    # ========================================================
    # NEXUS
    # ========================================================

    def draw_nexus(self):

        cx = 800
        cy = 425

        self.text(
            cx,
            150,
            "NEXUS INTELLIGENCE",
            size=13,
            fill=CYAN,
            bold=True
        )

        self.line(
            475,
            cy,
            1125,
            cy,
            fill="#09202B"
        )

        self.line(
            cx,
            175,
            cx,
            665,
            fill="#09202B"
        )

        points = []

        for x in range(
            560,
            1041,
            6
        ):

            distance = abs(
                x - cx
            )

            amplitude = max(
                3,
                28 - distance / 12
            )

            y = (
                cy
                + math.sin(
                    x * 0.13
                ) * amplitude
            )

            points.extend(
                [x, y]
            )

        self.canvas.create_line(
            *points,
            fill="#0B5368",
            width=2,
            smooth=True
        )

        self.create_ring(
            cx,
            cy,
            170,
            1.0
        )

        self.create_ring(
            cx,
            cy,
            132,
            -1.0
        )

        self.create_ring(
            cx,
            cy,
            96,
            1.0
        )

        self.canvas.create_oval(
            cx - 64,
            cy - 64,
            cx + 64,
            cy + 64,
            fill="#04121B",
            outline=CYAN_DIM,
            width=2
        )

        self.canvas.create_oval(
            cx - 44,
            cy - 44,
            cx + 44,
            cy + 44,
            fill="#061A25",
            outline="#124F63",
            width=1
        )

        self.core_id = self.canvas.create_oval(
            cx - 8,
            cy - 8,
            cx + 8,
            cy + 8,
            fill=CYAN,
            outline=""
        )

        self.text(
            cx,
            cy - 5,
            "NEXUS",
            size=18,
            fill=WHITE,
            bold=True
        )

        self.text(
            cx,
            cy + 25,
            "INTELLIGENCE CORE",
            size=7,
            fill=CYAN,
            bold=True
        )

        self.text(
            cx,
            635,
            "NEXUS INTELLIGENCE CORE",
            size=10,
            fill=CYAN,
            bold=True
        )

        self.text(
            cx,
            660,
            "MONITORING FORTRESS",
            size=8,
            fill=MUTED
        )

        self.draw_ai_chat()

    # ========================================================
    # NEXUS AI CHAT
    # ========================================================

    def draw_ai_chat(self):

        left = 500
        right = 1100

        top = 680
        bottom = 860

        cut = 10

        points = [
            left + cut, top,
            right - cut, top,
            right, top + cut,
            right, bottom - cut,
            right - cut, bottom,
            left + cut, bottom,
            left, bottom - cut,
            left, top + cut
        ]

        self.polygon(
            points,
            fill=PANEL_DARK,
            outline=CYAN_DIM,
            width=1
        )

        self.text(
            left + 20,
            top + 17,
            "NEXUS AI",
            size=9,
            fill=CYAN,
            anchor="w",
            bold=True
        )

        self.text(
            right - 20,
            top + 17,
            "ONLINE",
            size=7,
            fill=GREEN,
            anchor="e",
            bold=True
        )

        chat_frame = tk.Frame(
            self.root,
            bg=PANEL_DARK
        )

        self.chat_display = tk.Text(
            chat_frame,
            bg="#040C12",
            fg=WHITE,
            insertbackground=CYAN,
            relief="flat",
            bd=0,
            wrap="word",
            font=("Segoe UI", 8),
            padx=8,
            pady=5,
            height=5
        )

        self.chat_display.pack(
            fill="both",
            expand=True
        )

        self.chat_display.insert(
            "end",
            "NEXUS: FORTRESS intelligence core online.\n"
        )

        self.chat_display.configure(
            state="disabled"
        )

        self.chat_entry = tk.Entry(
            chat_frame,
            bg="#06141D",
            fg=WHITE,
            insertbackground=CYAN,
            relief="flat",
            bd=0,
            font=("Segoe UI", 8)
        )

        self.chat_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(8, 4),
            pady=6,
            ipady=4
        )

        self.chat_entry.bind(
            "<Return>",
            self.send_chat
        )

        send_button = tk.Button(
            chat_frame,
            text="SEND",
            command=self.send_chat,
            bg="#0A2733",
            fg=CYAN,
            activebackground="#103A4A",
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            font=("Segoe UI", 7, "bold"),
            padx=12
        )

        send_button.pack(
            side="right",
            padx=(2, 8),
            pady=6
        )

        self.chat_entry_window = (
            self.canvas.create_window(
                left + 15,
                top + 38,
                anchor="nw",
                window=chat_frame,
                width=right - left - 30,
                height=125
            )
        )

    # ========================================================
    # FIX CHAT WINDOW AFTER SCALING
    # ========================================================

    def fix_chat_window_scale(self):

        if not self.chat_entry_window:

            return

        self.canvas.coords(
            self.chat_entry_window,
            self.X(515),
            self.Y(718)
        )

        self.canvas.itemconfig(
            self.chat_entry_window,
            width=max(
                300,
                int(
                    (1100 - 515)
                    * self.scale
                )
            ),
            height=max(
                80,
                int(
                    125
                    * self.scale
                )
            )
        )

    # ========================================================
    # RINGS
    # ========================================================

    def create_ring(
        self,
        cx,
        cy,
        radius,
        direction
    ):

        segments = 12
        gap = 10

        ring = []

        for i in range(
            segments
        ):

            start = (
                i
                * (
                    360
                    / segments
                )
            )

            extent = (
                360
                / segments
            ) - gap

            item = self.canvas.create_arc(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                start=start,
                extent=extent,
                style=tk.ARC,
                outline=CYAN_DIM,
                width=2
            )

            ring.append(
                (
                    item,
                    start,
                    extent,
                    direction
                )
            )

        self.rings.append(
            ring
        )

    # ========================================================
    # SECURITY STATE
    # ========================================================

    def draw_security(self):

        left = 1185
        right = 1535

        self.text(
            left,
            150,
            "SECURITY STATE",
            size=13,
            fill=CYAN,
            anchor="w",
            bold=True
        )

        cut = 12

        points = [
            left + cut, 175,
            right - cut, 175,
            right, 187,
            right, 675,
            right - cut, 687,
            left + cut, 687,
            left, 675,
            left, 187
        ]

        self.polygon(
            points,
            fill=PANEL,
            outline=LINE,
            width=1
        )

        self.text(
            left + 25,
            207,
            "OVERALL STATUS",
            size=8,
            fill=MUTED,
            anchor="w"
        )

        alert = self.security_alert

        self.text(
            left + 25,
            248,
            "ALERT" if alert else "SECURE",
            size=26,
            fill=RED if alert else GREEN,
            anchor="w",
            bold=True
        )

        self.line(
            left + 25,
            275,
            right - 25,
            275,
            fill=LINE
        )

        self.text(
            left + 25,
            305,
            "THREAT LEVEL",
            size=8,
            fill=MUTED,
            anchor="w"
        )

        gx = left + 82
        gy = 365

        self.canvas.create_oval(
            gx - 46,
            gy - 46,
            gx + 46,
            gy + 46,
            outline=CYAN_DIM,
            width=3
        )

        self.canvas.create_arc(
            gx - 46,
            gy - 46,
            gx + 46,
            gy + 46,
            start=135 if not alert else -45,
            extent=90 if not alert else 270,
            style=tk.ARC,
            outline=RED if alert else GREEN,
            width=5
        )

        self.text(
            gx,
            gy - 4,
            "HIGH" if alert else "LOW",
            size=13,
            fill=RED if alert else GREEN,
            bold=True
        )

        self.text(
            gx,
            gy + 18,
            "THREAT",
            size=7,
            fill=MUTED,
            bold=True
        )

        self.text(
            left + 170,
            330,
            "PHYSICAL VAULT",
            size=8,
            fill=MUTED,
            anchor="w"
        )

        self.text(
            left + 170,
            360,
            "AUTHORIZED" if state["VAULT"] == "AUTHORIZED" else "LOCKED",
            size=15,
            fill=GREEN if state["VAULT"] == "AUTHORIZED" else CYAN,
            anchor="w",
            bold=True
        )

        self.line(
            left + 170,
            390,
            right - 25,
            390,
            fill=LINE
        )

        self.text(
            left + 25,
            425,
            "NEXUS ANALYSIS",
            size=8,
            fill=MUTED,
            anchor="w",
            bold=True
        )

        if alert:
            analysis = [
                ("Unauthorized access detected.", RED),
                ("Protected data backed up:" if alert.get("ok") else "Data backup FAILED.", WHITE),
                (alert.get("file", alert.get("error", ""))[:30], MUTED),
                (alert.get("trigger", "").title()[:30], MUTED),
            ]
        else:
            analysis = [
                ("FORTRESS is secure.", WHITE),
                ("Awaiting the next", MUTED),
                ("authorization event.", MUTED),
            ]

        for index, (line_text, line_color) in enumerate(analysis):

            self.text(
                left + 25,
                462 + index * 25,
                line_text,
                size=9,
                fill=line_color,
                anchor="w"
            )

    # ========================================================
    # EVENT LOG
    # ========================================================

    def draw_event_log(self):

        left = 65
        right = 415

        top = 710
        bottom = 850

        cut = 10

        points = [
            left + cut, top,
            right - cut, top,
            right, top + cut,
            right, bottom - cut,
            right - cut, bottom,
            left + cut, bottom,
            left, bottom - cut,
            left, top + cut
        ]

        self.polygon(
            points,
            fill=PANEL,
            outline=LINE,
            width=1
        )

        self.text(
            left + 18,
            top + 25,
            "TIME",
            size=7,
            fill=MUTED,
            anchor="w",
            bold=True
        )

        self.text(
            left + 90,
            top + 25,
            "EVENT",
            size=7,
            fill=MUTED,
            anchor="w",
            bold=True
        )

        self.text(
            right - 18,
            top + 25,
            "STATUS",
            size=7,
            fill=MUTED,
            anchor="e",
            bold=True
        )

        y = top + 57

        for timestamp, event_name, status in events[-3:]:

            self.text(
                left + 18,
                y,
                timestamp,
                size=7,
                fill=CYAN,
                anchor="w"
            )

            self.text(
                left + 90,
                y,
                event_name[:22],
                size=7,
                fill=WHITE,
                anchor="w"
            )

            status_color = (
                GREEN
                if status in (
                    "PASSED",
                    "INITIALIZED",
                    "AUTHORIZED"
                )
                else RED
                if status in (
                    "FAILED",
                    "BACKED UP",
                    "BACKUP FAILED"
                )
                else MUTED
            )

            self.text(
                right - 18,
                y,
                status,
                size=7,
                fill=status_color,
                anchor="e",
                bold=True
            )

            y += 27

    # ========================================================
    # FOOTER
    # ========================================================

    def draw_footer(self):

        self.text(
            1535,
            805,
            "FORTRESS — FOR THE FUTURE OF INDIA",
            size=10,
            fill=WHITE,
            anchor="e",
            bold=True
        )

        self.text(
            1535,
            832,
            "SECURITY INTELLIGENCE SYSTEM",
            size=7,
            fill=MUTED,
            anchor="e"
        )

    # ========================================================
    # AI CHAT
    # ========================================================

    def send_chat(
        self,
        event=None
    ):

        if not self.chat_entry:

            return

        message = (
            self.chat_entry
            .get()
            .strip()
        )

        if not message:

            return

        self.chat_entry.delete(
            0,
            "end"
        )

        if self.check_secret_phrase(message):

            threading.Thread(
                target=self.unlock_vault,
                daemon=True
            ).start()

            return

        self.add_chat_message(
            "YOU",
            message,
            WHITE
        )

        threading.Thread(
            target=self.ask_openai,
            args=(message,),
            daemon=True
        ).start()

    def add_chat_message(
        self,
        speaker,
        message,
        color
    ):

        if not self.chat_display:

            return

        def update():

            if not self.chat_display:

                return

            self.chat_display.configure(
                state="normal"
            )

            self.chat_display.insert(
                "end",
                f"{speaker}: {message}\n",
                speaker
            )

            self.chat_display.tag_config(
                speaker,
                foreground=color
            )

            self.chat_display.see(
                "end"
            )

            self.chat_display.configure(
                state="disabled"
            )

        self.root.after(
            0,
            update
        )

    # ========================================================
    # NEXUS PROMPT
    # ========================================================

    def build_nexus_prompt(
        self,
        message
    ):

        current_state = f"""
FACE VERIFICATION: {state["FACE VERIFICATION"]}
PALM VERIFICATION: {state["PALM VERIFICATION"]}
PHONE PIN: {state["PHONE PIN"]}
CARD VERIFICATION: {state["CARD VERIFICATION"]}
PHYSICAL VAULT: {state["VAULT"]}
"""

        recent_events = "\n".join(
            f"{timestamp} | {event_name} | {status}"
            for timestamp, event_name, status
            in events[-10:]
        )

        prompt = f"""
You are NEXUS, the intelligence and communication layer
of the FORTRESS security intelligence system.

Use the supplied FORTRESS Knowledge Core as your permanent
project knowledge.

IMPORTANT RULES:

1. Be concise and professional.
2. Answer naturally for a live technical demonstration.
3. Normally answer in 1 to 3 sentences.
4. For simple questions, prefer one concise sentence.
5. Never invent FORTRESS features.
6. Never invent security events.
7. Never invent sensor readings.
8. Never claim an authentication layer passed unless the
   current live state confirms PASSED.
9. Never claim the vault is unlocked unless the FORTRESS
   controller confirms authorization.
10. The deterministic FORTRESS controller has authority
    over physical security.
11. NEXUS does not independently authorize physical access.
12. Memory Ghost is a hidden security layer and is not a
    visible phone authentication option.
13. If information is unavailable, say it is unavailable.
14. Do not mention the AI model, API, prompt, or Knowledge
    Core unless specifically asked.
15. Do not provide unnecessary technical explanations.
16. Do not repeat the user's question.
17. Do not use emojis unless the user specifically asks.

FORTRESS KNOWLEDGE CORE:
{NEXUS_KNOWLEDGE}

CURRENT LIVE FORTRESS STATE:
{current_state}

RECENT CONFIRMED EVENTS:
{recent_events}

USER QUESTION:
{message}

Respond as NEXUS.
"""

        return prompt

    # ========================================================
    # NEXUS RESPONSE
    # ========================================================

    def generate_nexus_response(
        self,
        message
    ):

        prompt = self.build_nexus_prompt(
            message
        )

        try:

            response = OPENAI_CLIENT.responses.create(
                model="gpt-6-luna",
                input=prompt,
                max_output_tokens=120
            )

            answer = response.output_text.strip()

            if not answer:

                answer = (
                    "NEXUS could not generate a response."
                )

            return answer

        except Exception as error:

            print(
                "NEXUS OpenAI error:",
                error
            )

            return (
                "NEXUS AI connection unavailable."
            )

    # ========================================================
    # NEXUS — TEXT CHAT
    # ========================================================

    def ask_openai(
        self,
        message
    ):

        answer = self.generate_nexus_response(
            message
        )

        self.add_chat_message(
            "NEXUS",
            answer,
            CYAN
        )

    # ========================================================
    # VOICE SYSTEM
    # ========================================================

    def start_voice_system(self):

        if not self.voice_enabled:

            return

        if self.voice_thread_started:

            return

        self.voice_thread_started = True

        print(
            "NEXUS VOICE: ONLINE"
        )

        threading.Thread(
            target=self.voice_loop,
            daemon=True
        ).start()

    # ========================================================
    # CONTINUOUS VOICE LOOP
    # ========================================================

    def voice_loop(self):

        # NEXUS speaks first when the interface opens.
        # This happens inside the voice thread so Tkinter remains responsive.
        try:

            self.voice_busy = True

            startup_message = (
                "NEXUS online. "
                "FORTRESS security intelligence system active. "
                "Voice interface ready."
            )

            print(
                "NEXUS:",
                startup_message
            )

            self.add_chat_message(
                "NEXUS",
                startup_message,
                CYAN
            )

            self.speak_answer(
                startup_message
            )

            time.sleep(
                VOICE_RESTART_DELAY
            )

            self.voice_busy = False

        except Exception as error:

            print(
                "NEXUS startup voice error:",
                error
            )

            self.voice_busy = False

        while self.voice_enabled:

            try:

                if self.voice_busy:

                    time.sleep(
                        0.2
                    )

                    continue

                spoken_text = (
                    self.listen_until_silence()
                )

                if not spoken_text:

                    continue

                print(
                    "YOU SAID:",
                    spoken_text
                )

                if self.check_secret_phrase(spoken_text):

                    self.voice_busy = True
                    self.unlock_vault()
                    time.sleep(VOICE_RESTART_DELAY)
                    self.voice_busy = False

                    continue

                self.add_chat_message(
                    "YOU",
                    spoken_text,
                    WHITE
                )

                self.voice_busy = True

                answer = (
                    self.generate_nexus_response(
                        spoken_text
                    )
                )

                self.add_chat_message(
                    "NEXUS",
                    answer,
                    CYAN
                )

                self.speak_answer(
                    answer
                )

                time.sleep(
                    VOICE_RESTART_DELAY
                )

                self.voice_busy = False

            except Exception as error:

                print(
                    "VOICE SYSTEM ERROR:",
                    error
                )

                self.voice_busy = False

                time.sleep(
                    1
                )

    # ========================================================
    # LISTEN UNTIL SILENCE
    # ========================================================

    def listen_until_silence(self):

        audio_blocks = []

        speech_started = False
        silence_time = 0.0
        speech_start_time = None
        wait_time = 0.0

        block_size = int(
            SAMPLE_RATE
            * BLOCK_DURATION
        )

        # ----------------------------------------------------
        # CALIBRATE THE CURRENT ROOM FIRST
        # ----------------------------------------------------
        # The old fixed threshold of 450 could be too high
        # for normal speech. We measure the microphone's
        # background level and then set a speech threshold.
        # ----------------------------------------------------

        try:

            mic_device, mic_channels = choose_microphone_device()
            mic_info = sd.query_devices(mic_device)
            print(
                "MIC INPUT:",
                f"device={mic_device}",
                f"name={mic_info.get('name', 'Unknown')}",
                f"channels={mic_channels}",
                f"sample_rate={SAMPLE_RATE}"
            )

            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=mic_channels,
                dtype="int16",
                device=mic_device,
                blocksize=block_size
            ) as stream:

                calibration_blocks = []

                for _ in range(5):

                    data, overflowed = (
                        stream.read(
                            block_size
                        )
                    )

                    if overflowed:
                        continue

                    calibration_block = (
                        np.asarray(
                            data
                        ).flatten()
                    )

                    if len(calibration_block) > 0:

                        calibration_blocks.append(
                            calibration_block
                        )

                if calibration_blocks:

                    calibration_audio = np.concatenate(
                        calibration_blocks
                    )

                    noise_rms = float(
                        np.sqrt(
                            np.mean(
                                np.square(
                                    calibration_audio.astype(
                                        np.float32
                                    )
                                )
                            )
                        )
                    )

                else:

                    noise_rms = 100.0

                start_threshold = max(
                    250.0,
                    noise_rms * 2.2
                )

                silence_threshold = max(
                    160.0,
                    start_threshold * 0.60
                )

                print(
                    "MIC:",
                    f"noise={noise_rms:.0f}",
                    f"start={start_threshold:.0f}",
                    f"silence={silence_threshold:.0f}"
                )

                # ------------------------------------------------
                # WAIT FOR THE USER TO ACTUALLY START SPEAKING
                # ------------------------------------------------

                while self.voice_enabled:

                    data, overflowed = (
                        stream.read(
                            block_size
                        )
                    )

                    if overflowed:

                        print(
                            "Microphone buffer overflow."
                        )

                    block = (
                        np.asarray(
                            data
                        ).flatten()
                    )

                    if len(block) == 0:

                        continue

                    rms = float(
                        np.sqrt(
                            np.mean(
                                np.square(
                                    block.astype(
                                        np.float32
                                    )
                                )
                            )
                        )
                    )

                    wait_time += BLOCK_DURATION

                    if rms >= start_threshold:

                        speech_started = True

                        speech_start_time = (
                            time.time()
                        )

                        silence_time = 0.0

                        audio_blocks.append(
                            block.copy()
                        )

                        break

                    if (
                        wait_time
                        >= WAIT_FOR_SPEECH_TIMEOUT
                    ):

                        return None

                # ------------------------------------------------
                # USER IS SPEAKING — COLLECT UNTIL SILENCE
                # ------------------------------------------------

                while (
                    self.voice_enabled
                    and speech_started
                ):

                    data, overflowed = (
                        stream.read(
                            block_size
                        )
                    )

                    if overflowed:

                        print(
                            "Microphone buffer overflow."
                        )

                    block = (
                        np.asarray(
                            data
                        ).flatten()
                    )

                    if len(block) == 0:

                        continue

                    audio_blocks.append(
                        block.copy()
                    )

                    rms = float(
                        np.sqrt(
                            np.mean(
                                np.square(
                                    block.astype(
                                        np.float32
                                    )
                                )
                            )
                        )
                    )

                    if rms >= silence_threshold:

                        silence_time = 0.0

                    else:

                        silence_time += (
                            BLOCK_DURATION
                        )

                    elapsed = (
                        time.time()
                        - speech_start_time
                    )

                    if (
                        silence_time
                        >= SILENCE_DURATION
                    ):

                        break

                    if (
                        elapsed
                        >= MAX_SPEECH_DURATION
                    ):

                        break

        except Exception as error:

            print(
                "Microphone error:",
                error
            )

            time.sleep(
                1
            )

            return None

        if not audio_blocks:

            return None

        audio_data = np.concatenate(
            audio_blocks
        )

        raw_audio = (
            audio_data
            .astype(
                np.int16
            )
            .tobytes()
        )

        audio = sr.AudioData(
            raw_audio,
            SAMPLE_RATE,
            2
        )

        print(
            "Processing speech..."
        )

        try:

            text = (
                speech_recognizer
                .recognize_google(
                    audio
                )
            )

            text = text.strip()

            if text:

                print(
                    "YOU SAID:",
                    text
                )

                return text

            return None

        except sr.UnknownValueError:

            print(
                "NEXUS could not understand the speech."
            )

            return None

        except sr.RequestError as error:

            print(
                "Speech recognition error:",
                error
            )

            return None

        except Exception as error:

            print(
                "Speech conversion error:",
                error
            )

            return None

    # ========================================================
    # NEXUS TEXT TO SPEECH
    # ========================================================

    def speak_answer(
        self,
        answer
    ):

        if not answer:
            return

        # Windows native speech: speaks the complete answer synchronously.
        try:
            import subprocess

            powershell_script = '''
Add-Type -AssemblyName System.Speech
$text = [Console]::In.ReadToEnd()
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$synth.Volume = 100
$synth.Speak($text)
$synth.Dispose()
'''

            print("NEXUS SPEAKING FULL RESPONSE:")
            print(answer)

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    powershell_script
                ],
                input=answer,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120
            )

            if result.returncode != 0:
                print("Windows TTS error:", result.stderr.strip())

        except subprocess.TimeoutExpired:
            print("Windows TTS timed out.")

        except Exception as error:
            print("TTS error:", error)

    # ========================================================
    # ANIMATION
    # ========================================================

    def animate(self):

        if not self.ready:

            return

        self.rotation += 1.25
        self.pulse += 0.08

        self.update_rings()
        self.update_core()
        self.update_clock()

        self.root.after(
            35,
            self.animate
        )

    def update_rings(self):

        for ring in self.rings:

            for (
                item,
                start,
                extent,
                direction
            ) in ring:

                self.canvas.itemconfig(
                    item,
                    start=(
                        start
                        + self.rotation
                        * direction
                    ),
                    extent=extent
                )

    def update_core(self):

        if self.core_id is None:

            return

        cx = 800
        cy = 425

        radius = (
            8
            + (
                math.sin(
                    self.pulse
                ) + 1
            ) * 2.5
        )

        self.canvas.coords(
            self.core_id,
            self.X(cx - radius),
            self.Y(cy - radius),
            self.X(cx + radius),
            self.Y(cy + radius)
        )

    def update_clock(self):

        if self.clock_id:

            self.canvas.itemconfig(
                self.clock_id,
                text=datetime.now().strftime(
                    "%H:%M:%S"
                )
            )

        if self.date_id:

            self.canvas.itemconfig(
                self.date_id,
                text=datetime.now().strftime(
                    "%d %B %Y"
                )
            )

    # ========================================================
    # PHONE <-> LAPTOP FACE STATUS BRIDGE
    # ========================================================

    def poll_phone_face_state(self):
        """Mirror the phone controller's face result into the HUD."""
        try:
            response = requests.get("http://127.0.0.1:5001/state", timeout=0.35)
            if response.ok:
                phone_state = response.json()
                face_status = str(phone_state.get("face", "")).upper()
                palm_status = str(phone_state.get("palm", "")).upper()
                # The phone controller calls the PIN field "password".
                # Accept its verified result and mirror it into the HUD.
                pin_status = str(
                    phone_state.get("password", phone_state.get("phone_pin", ""))
                ).upper()
                # Card verification is performed on the phone: the phone
                # captures a card photo and reports security_code=VERIFIED.
                card_status = str(
                    phone_state.get("security_code", "")
                ).upper()

                # Phone tapped VERIFY FACE: run the face scan on this laptop.
                if face_status == "SCANNING" and not self.face_scan_running:
                    self.start_face_verification()

                # Phone tapped SCAN PALM: run the palm scan on this laptop.
                if palm_status == "SCANNING" and not self.palm_scan_running:
                    self.start_palm_verification()

                # Phone tapped VERIFY QR: run the QR scan on this laptop.
                if card_status == "SCANNING" and not self.qr_scan_running:
                    self.start_qr_verification()

                # Mirror real controller results into the laptop HUD.
                if face_status in ("VERIFIED", "PASSED") and state.get("FACE VERIFICATION") != "PASSED":
                    self.face_pass()
                elif face_status in ("FAILED", "DENIED") and state.get("FACE VERIFICATION") != "FAILED":
                    state["FACE VERIFICATION"] = "FAILED"
                    state["PALM VERIFICATION"] = "LOCKED"
                    state["PHONE PIN"] = "LOCKED"
                    state["CARD VERIFICATION"] = "LOCKED"
                    events.append((datetime.now().strftime("%H:%M:%S"), "FACE VERIFICATION", "FAILED"))
                    self.request_rebuild()

                # FIX: previously the HUD polled face status only. The palm
                # verifier posts /palm_verified to the phone controller, but
                # the HUD never read that result, so it stayed PENDING.
                if palm_status in ("VERIFIED", "PASSED") and state.get("PALM VERIFICATION") != "PASSED":
                    self.palm_pass()
                elif palm_status in ("FAILED", "DENIED") and state.get("PALM VERIFICATION") != "FAILED":
                    state["PALM VERIFICATION"] = "FAILED"
                    state["PHONE PIN"] = "LOCKED"
                    state["CARD VERIFICATION"] = "LOCKED"
                    events.append((datetime.now().strftime("%H:%M:%S"), "PALM VERIFICATION", "FAILED"))
                    self.request_rebuild()

                # PIN is verified on the phone; show PASSED on the laptop.
                if pin_status in ("VERIFIED", "PASSED") and state.get("PHONE PIN") != "PASSED":
                    self.password_pass()
                elif pin_status in ("LOCKED", "FAILED", "DENIED", "ACTIVE") and state.get("PHONE PIN") not in ("PASSED", "PENDING"):
                    state["PHONE PIN"] = "READY" if pin_status == "ACTIVE" else "LOCKED"

                # Mirror the phone-camera card result into the laptop HUD.
                # The phone controller currently treats a captured photo as
                # the competition-demo card approval; it does not identify
                # or authenticate the printed card itself.
                if card_status in ("VERIFIED", "PASSED") and state.get("CARD VERIFICATION") != "PASSED":
                    self.code_pass()
                elif card_status in ("FAILED", "DENIED") and state.get("CARD VERIFICATION") != "FAILED":
                    state["CARD VERIFICATION"] = "FAILED"
                    events.append((datetime.now().strftime("%H:%M:%S"), "CARD VERIFICATION", "FAILED"))
                    self.request_rebuild()

                vault_status = str(phone_state.get("vault", "")).upper()
                if vault_status == "AUTHORIZED" and state["VAULT"] != "AUTHORIZED":
                    self.vault_authorized()
                elif vault_status == "LOCKED" and state["VAULT"] == "AUTHORIZED":
                    state["VAULT"] = "LOCKED"
                    self.request_rebuild()

            backup_response = requests.get(CONTROLLER_URL + "/backup_events", timeout=0.35)
            if backup_response.ok:
                backup_list = backup_response.json().get("events", [])
                for backup_event in backup_list[self.backups_seen:]:
                    self.on_backup_event(backup_event)
                self.backups_seen = len(backup_list)
        except (requests.RequestException, ValueError, TypeError):
            # Phone controller may not be started yet; keep the HUD and NEXUS running.
            pass
        finally:
            try:
                self.root.after(700, self.poll_phone_face_state)
            except tk.TclError:
                pass

    # ========================================================
    # FACE VERIFICATION — LAPTOP WEBCAM
    # ========================================================

    def start_face_verification(
        self,
        event=None
    ):

        if self.face_scan_running:
            return

        self.face_scan_running = True

        state["FACE VERIFICATION"] = "SCANNING"
        events.append((datetime.now().strftime("%H:%M:%S"), "FACE VERIFICATION", "SCANNING"))
        self.request_rebuild()

        threading.Thread(
            target=self.face_verification_worker,
            daemon=True
        ).start()

    def face_verification_worker(self):

        try:
            result = run_face_verification()
        except Exception as error:
            result = f"ERROR: {type(error).__name__}: {error}"

        print("FACE VERIFICATION RESULT:", result)

        if result == "VERIFIED":
            self.root.after(0, self.face_verification_done, True)
        else:
            # Wrong face, cancelled (ESC) or error: tell the phone so it is
            # not stuck on SCANNING. The controller counts it as a failure.
            reason = {
                "MISMATCH": "face did not match enrolled identity",
                "CANCELLED": "face scan cancelled",
            }.get(result, result)
            notify_controller("/face_denied", {"reason": reason})
            self.root.after(0, self.face_verification_done, False)

    def face_verification_done(
        self,
        verified
    ):

        self.face_scan_running = False

        if verified:
            if state.get("FACE VERIFICATION") != "PASSED":
                self.face_pass()
        elif state.get("FACE VERIFICATION") != "FAILED":
            state["FACE VERIFICATION"] = "FAILED"
            state["PALM VERIFICATION"] = "LOCKED"
            state["PHONE PIN"] = "LOCKED"
            state["CARD VERIFICATION"] = "LOCKED"
            events.append((datetime.now().strftime("%H:%M:%S"), "FACE VERIFICATION", "FAILED"))
            self.request_rebuild()

    # ========================================================
    # PALM VERIFICATION — LAPTOP WEBCAM
    # ========================================================

    def start_palm_verification(self):

        if self.palm_scan_running or self.face_scan_running:
            return

        self.palm_scan_running = True

        state["PALM VERIFICATION"] = "SCANNING"
        events.append((datetime.now().strftime("%H:%M:%S"), "PALM VERIFICATION", "SCANNING"))
        self.request_rebuild()

        threading.Thread(
            target=self.palm_verification_worker,
            daemon=True
        ).start()

    def palm_verification_worker(self):

        try:
            result = run_palm_verification()
        except Exception as error:
            result = f"ERROR: {type(error).__name__}: {error}"

        print("PALM VERIFICATION RESULT:", result)

        if result == "VERIFIED":
            notify_controller("/palm_verified")
        else:
            message = {
                "CANCELLED": "PALM VERIFICATION CANCELLED",
                "MISMATCH": "ACCESS DENIED // PALM NOT RECOGNISED",
            }.get(result, f"PALM SCAN {result.upper()}")
            try:
                requests.post(
                    CONTROLLER_URL + "/palm_denied",
                    json={"message": message},
                    timeout=1.5
                )
            except Exception:
                pass

        self.root.after(0, self.palm_verification_done, result == "VERIFIED")

    def palm_verification_done(
        self,
        verified
    ):

        self.palm_scan_running = False

        if verified:
            if state.get("PALM VERIFICATION") != "PASSED":
                self.palm_pass()
        elif state.get("PALM VERIFICATION") != "FAILED":
            state["PALM VERIFICATION"] = "FAILED"
            state["PHONE PIN"] = "LOCKED"
            state["CARD VERIFICATION"] = "LOCKED"
            events.append((datetime.now().strftime("%H:%M:%S"), "PALM VERIFICATION", "FAILED"))
            self.request_rebuild()

    # ========================================================
    # QR VERIFICATION — LAPTOP WEBCAM (demo-only)
    # ========================================================

    def on_verify_qr_click(
        self,
        event=None
    ):

        # Let the phone/controller know the scan started, then run it here.
        try:
            requests.post(CONTROLLER_URL + "/card_request", timeout=1.5)
        except Exception:
            pass

        self.start_qr_verification()

    def start_qr_verification(self):

        if self.qr_scan_running or self.face_scan_running or self.palm_scan_running:
            return

        self.qr_scan_running = True

        state["CARD VERIFICATION"] = "SCANNING"
        events.append((datetime.now().strftime("%H:%M:%S"), "QR VERIFICATION", "SCANNING"))
        self.request_rebuild()

        threading.Thread(
            target=self.qr_verification_worker,
            daemon=True
        ).start()

    def qr_verification_worker(self):

        try:
            result = run_qr_verification()
        except Exception as error:
            result = f"ERROR: {type(error).__name__}: {error}"

        print("QR VERIFICATION RESULT:", result)

        if result == "VERIFIED":
            notify_controller("/card_verified")
        else:
            message = (
                "QR VERIFICATION CANCELLED"
                if result == "CANCELLED"
                else f"QR SCAN {result.upper()}"
            )
            try:
                requests.post(
                    CONTROLLER_URL + "/card_denied",
                    json={"message": message},
                    timeout=1.5
                )
            except Exception:
                pass

        self.root.after(0, self.qr_verification_done, result == "VERIFIED")

    def qr_verification_done(
        self,
        verified
    ):

        self.qr_scan_running = False

        if verified:
            if state.get("CARD VERIFICATION") != "PASSED":
                self.code_pass()
        elif state.get("CARD VERIFICATION") != "FAILED":
            state["CARD VERIFICATION"] = "FAILED"
            events.append((datetime.now().strftime("%H:%M:%S"), "QR VERIFICATION", "FAILED"))
            self.request_rebuild()

    # ========================================================
    # FINAL SEQUENCE STAGE (hidden)
    # ========================================================

    def flash_sequence(self):

        if self.sequence_window is not None:
            return

        rng = random.SystemRandom()
        sequence = "".join(
            rng.choice(SEQUENCE_SYMBOLS)
            for _ in range(SEQUENCE_LENGTH)
        )

        self.sequence_active = True

        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.configure(bg=CYAN_DIM)
        window.attributes("-topmost", True)

        width = max(420, int(900 * self.scale))
        height = max(180, int(300 * self.scale))
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")

        inner = tk.Frame(window, bg="#030A10")
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        tk.Label(
            inner,
            text="  ".join(sequence),
            bg="#030A10",
            fg=CYAN,
            font=("Consolas", max(36, int(78 * self.scale)), "bold")
        ).pack(expand=True)

        self.sequence_window = window

        self.root.after(SEQUENCE_FLASH_MS, self.hide_sequence)

    def hide_sequence(self):

        if self.sequence_window is not None:
            try:
                self.sequence_window.destroy()
            except tk.TclError:
                pass

        # Symbols are gone: now the phone shows the 6 character boxes.
        threading.Thread(
            target=notify_controller,
            args=("/sequence_ready",),
            daemon=True
        ).start()

    def check_secret_phrase(
        self,
        text
    ):

        return self.sequence_active and is_secret_phrase(text)

    def unlock_vault(self):
        """Runs off the Tk thread: tell the controller, update the HUD, answer."""

        self.sequence_active = False

        try:
            requests.post(CONTROLLER_URL + "/sequence_unlock", timeout=1.5)
        except Exception:
            pass

        self.root.after(0, self.vault_authorized)

        self.add_chat_message(
            "NEXUS",
            "Access authorized.",
            CYAN
        )

        self.speak_answer(
            "Access authorized."
        )

    def vault_authorized(self):

        self.sequence_active = False

        if state["VAULT"] == "AUTHORIZED":
            return

        state["VAULT"] = "AUTHORIZED"
        self.security_alert = None

        events.append((datetime.now().strftime("%H:%M:%S"), "PHYSICAL VAULT", "AUTHORIZED"))
        self.request_rebuild()

    # ========================================================
    # INTRUSION BACKUP ALERT (laptop only)
    # ========================================================

    def on_backup_event(
        self,
        backup_event
    ):

        self.security_alert = backup_event
        ok = backup_event.get("ok")
        trigger = backup_event.get("trigger", "UNAUTHORIZED ACCESS")

        events.append((
            datetime.now().strftime("%H:%M:%S"),
            "UNAUTHORIZED ACCESS",
            "BACKED UP" if ok else "BACKUP FAILED"
        ))

        if ok:
            message = (
                f"ALERT: unauthorized access detected ({trigger.lower()}). "
                f"Protected data backed up to {backup_event.get('file')}."
            )
        else:
            message = (
                f"ALERT: unauthorized access detected ({trigger.lower()}). "
                f"Data backup failed: {backup_event.get('error')}"
            )

        self.add_chat_message("NEXUS", message, RED)
        self.request_rebuild()
        self.show_backup_alert(backup_event)

    def show_backup_alert(
        self,
        backup_event
    ):

        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.configure(bg=RED)
        window.attributes("-topmost", True)

        width = max(420, int(760 * self.scale))
        height = max(150, int(210 * self.scale))
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + int(self.offset_y + 140 * self.scale)
        window.geometry(f"{width}x{height}+{x}+{y}")

        inner = tk.Frame(window, bg="#1A0509")
        inner.pack(fill="both", expand=True, padx=3, pady=3)

        tk.Label(
            inner,
            text="UNAUTHORIZED ACCESS DETECTED",
            bg="#1A0509",
            fg=RED,
            font=("Segoe UI", max(14, int(24 * self.scale)), "bold")
        ).pack(pady=(int(28 * self.scale), 6))

        if backup_event.get("ok"):
            detail = f"DATA BACKED UP  //  {backup_event.get('file')}"
        else:
            detail = f"DATA BACKUP FAILED  //  {backup_event.get('error')}"

        tk.Label(
            inner,
            text=detail,
            bg="#1A0509",
            fg=WHITE,
            font=("Segoe UI", max(9, int(12 * self.scale)), "bold")
        ).pack()

        tk.Label(
            inner,
            text=backup_event.get("trigger", ""),
            bg="#1A0509",
            fg=MUTED,
            font=("Segoe UI", max(8, int(10 * self.scale)))
        ).pack(pady=(4, 0))

        self.root.after(5000, window.destroy)

    # ========================================================
    # AUTHENTICATION STATE
    # ========================================================

    def face_pass(self):

        state["FACE VERIFICATION"] = "PASSED"
        state["PALM VERIFICATION"] = "READY"
        state["PHONE PIN"] = "LOCKED"
        state["CARD VERIFICATION"] = "LOCKED"
        state["VAULT"] = "LOCKED"

        self.sequence_active = False
        self.sequence_window = None

        events.append((datetime.now().strftime("%H:%M:%S"), "FACE VERIFICATION", "PASSED"))
        self.request_rebuild()

    def palm_pass(self):

        state["PALM VERIFICATION"] = "PASSED"
        state["PHONE PIN"] = "READY"
        state["CARD VERIFICATION"] = "LOCKED"

        events.append((datetime.now().strftime("%H:%M:%S"), "PALM VERIFICATION", "PASSED"))
        self.request_rebuild()

    def password_pass(self):

        state["PHONE PIN"] = "PASSED"
        state["CARD VERIFICATION"] = "PENDING"

        events.append((datetime.now().strftime("%H:%M:%S"), "PHONE PIN", "PASSED"))
        self.request_rebuild()

    def code_pass(self):

        state["CARD VERIFICATION"] = "PASSED"

        events.append((datetime.now().strftime("%H:%M:%S"), "CARD VERIFICATION", "PASSED"))
        self.request_rebuild()

        # Flash the sequence once the HUD has redrawn.
        self.root.after(600, self.flash_sequence)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = FortressHUD(
        root
    )

    root.mainloop()
