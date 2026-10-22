"""
FORTRESS standalone palm verification TEST
Loads fortress_palm_enrollment.npz made by FORTRESS_PALM_ENROLL.py.
Shows whether a live palm resembles the enrolled samples.
This is an experimental ORB-texture comparison, NOT high-security biometric auth.
Requires: opencv-python, mediapipe (Tasks API), numpy.
Keep hand_landmarker.task and fortress_palm_enrollment.npz beside this script.

Stricter than the first version, which let other people's palms match:
  - only mutual (two-way) ratio-test matches are counted,
  - the match threshold is calibrated from the enrolled samples themselves,
  - most enrolled samples must match, not just the best one,
  - the match must hold for several checks in a row,
  - the hand must be an open palm, and the status resets when it leaves.
"""

import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
except Exception as exc:
    print("Could not import MediaPipe Tasks. Details:", exc)
    input("Press Enter to exit...")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "hand_landmarker.task"
ENROLLMENT_PATH = BASE_DIR / "fortress_palm_enrollment.npz"
ORB = cv2.ORB_create(nfeatures=500)
MATCHER = cv2.BFMatcher(cv2.NORM_HAMMING)

RATIO_TEST = 0.72

# Threshold = this share of the typical match count between your own
# enrolled samples, but never below MIN_GOOD_MATCHES.
CALIBRATION_SHARE = 0.60
MIN_GOOD_MATCHES = 15

# Share of enrolled samples that must reach the threshold (3 of 5).
SAMPLE_AGREEMENT = 0.60

# Checks in a row that must pass before DEMO MATCH is shown.
EVAL_INTERVAL = 0.25
REQUIRED_CONSECUTIVE = 4

PALM_IDS = [0, 5, 9, 13, 17]
FINGER_TIPS = [8, 12, 16, 20]
FINGER_MCPS = [5, 9, 13, 17]


def palm_crop(frame, landmarks):
    # Must stay identical to the enrollment crop, or genuine scores drop.
    h, w = frame.shape[:2]
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in PALM_IDS],
                   dtype=np.float32)
    center = pts.mean(axis=0)
    pts[:, 0] = center[0] + (pts[:, 0] - center[0]) * 1.55
    pts[:, 1] = center[1] + (pts[:, 1] - center[1]) * 1.55
    x1 = max(0, int(np.min(pts[:, 0])))
    y1 = max(0, int(np.min(pts[:, 1])))
    x2 = min(w, int(np.max(pts[:, 0])))
    y2 = min(h, int(np.max(pts[:, 1])))
    if x2 - x1 < 60 or y2 - y1 < 60:
        return None
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
    return cv2.equalizeHist(gray)


def is_open_palm(landmarks):
    """All four fingers stretched out: each tip is well beyond its knuckle."""
    wrist = landmarks[0]

    def dist(a, b):
        return math.hypot(a.x - b.x, a.y - b.y)

    for tip, mcp in zip(FINGER_TIPS, FINGER_MCPS):
        if dist(wrist, landmarks[tip]) < 1.35 * dist(wrist, landmarks[mcp]):
            return False
    return True


def ratio_matches(query, train):
    """{query index: train index} for matches passing the ratio test."""
    result = {}
    for pair in MATCHER.knnMatch(query, train, k=2):
        if len(pair) == 2 and pair[0].distance < RATIO_TEST * pair[1].distance:
            result[pair[0].queryIdx] = pair[0].trainIdx
    return result


def mutual_good_matches(desc_a, desc_b):
    """Matches that pass the ratio test in both directions and agree."""
    if desc_a is None or desc_b is None or len(desc_a) < 2 or len(desc_b) < 2:
        return 0
    forward = ratio_matches(desc_a, desc_b)
    backward = ratio_matches(desc_b, desc_a)
    return sum(1 for a, b in forward.items() if backward.get(b) == a)


def calibrate_threshold(enrolled):
    """Typical match count between your own enrolled samples -> threshold."""
    genuine = [
        mutual_good_matches(enrolled[i], enrolled[j])
        for i in range(len(enrolled))
        for j in range(i + 1, len(enrolled))
    ]
    genuine_level = float(np.median(genuine)) if genuine else 0.0
    threshold = max(MIN_GOOD_MATCHES, int(round(genuine_level * CALIBRATION_SHARE)))
    return threshold, genuine_level, genuine


def main():
    if not MODEL_PATH.is_file():
        print("Missing:", MODEL_PATH)
        print("Put hand_landmarker.task beside this script.")
        input("Press Enter to exit...")
        return
    if not ENROLLMENT_PATH.is_file():
        print("Missing enrollment file:", ENROLLMENT_PATH)
        print("Run FORTRESS_PALM_ENROLL.py first and capture all 5 samples.")
        input("Press Enter to exit...")
        return

    try:
        with np.load(ENROLLMENT_PATH, allow_pickle=False) as data:
            packed = data["descriptors"]
            counts = data["counts"]
            enrolled = [packed[i, :int(counts[i]), :].copy()
                        for i in range(len(counts))]
    except Exception as exc:
        print("Could not read enrollment file:", exc)
        input("Press Enter to exit...")
        return

    enrolled = [desc for desc in enrolled if len(desc) >= 2]
    if len(enrolled) < 2:
        print("Enrollment has fewer than 2 usable samples. Run FORTRESS_PALM_ENROLL.py again.")
        input("Press Enter to exit...")
        return

    threshold, genuine_level, genuine = calibrate_threshold(enrolled)
    required_samples = max(1, math.ceil(len(enrolled) * SAMPLE_AGREEMENT))

    print("FORTRESS PALM VERIFICATION TEST")
    print(f"Enrolled samples        : {len(enrolled)}")
    print(f"Sample-to-sample matches: {genuine} (median {genuine_level:.0f})")
    print(f"Match threshold         : {threshold} mutual matches")
    print(f"Samples that must agree : {required_samples} of {len(enrolled)}")
    if genuine_level < MIN_GOOD_MATCHES / CALIBRATION_SHARE:
        print("WARNING: your enrolled samples barely match each other. Re-enroll with the")
        print("palm flat, close to the camera, in bright even light, then test again.")
    print("Test your enrolled palm first, then test another consenting participant.")
    print("Keep the palm facing the camera in similar lighting and distance.")
    input("Press Enter to open webcam...")

    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.65,
        min_hand_presence_confidence=0.65,
        min_tracking_confidence=0.65,
    )
    try:
        landmarker = vision.HandLandmarker.create_from_options(options)
    except Exception as exc:
        print("Could not load hand landmark model:", exc)
        input("Press Enter to exit...")
        return

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        landmarker.close()
        print("Could not open webcam. Close other apps using it.")
        input("Press Enter to exit...")
        return

    last_scores = []
    last_eval = 0.0
    last_timestamp = 0
    streak = 0
    status = "SHOW OPEN PALM"
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            # MediaPipe VIDEO mode needs strictly increasing timestamps.
            timestamp = max(int(time.time() * 1000), last_timestamp + 1)
            last_timestamp = timestamp
            result = landmarker.detect_for_video(mp_image, timestamp)

            now = time.time()
            if not result.hand_landmarks:
                status, streak, last_scores = "SHOW OPEN PALM", 0, []
            elif not is_open_palm(result.hand_landmarks[0]):
                status, streak, last_scores = "OPEN YOUR HAND FULLY", 0, []
            elif now - last_eval >= EVAL_INTERVAL:
                last_eval = now
                crop = palm_crop(frame, result.hand_landmarks[0])
                if crop is None:
                    status, streak, last_scores = "MOVE PALM CLOSER", 0, []
                else:
                    _, live_desc = ORB.detectAndCompute(crop, None)
                    last_scores = [mutual_good_matches(live_desc, ref) for ref in enrolled]
                    agreeing = sum(1 for score in last_scores if score >= threshold)
                    streak = streak + 1 if agreeing >= required_samples else 0
                    if streak >= REQUIRED_CONSECUTIVE:
                        status = "DEMO MATCH"
                    elif streak > 0:
                        status = f"CHECKING {streak}/{REQUIRED_CONSECUTIVE}"
                    else:
                        status = "NO MATCH"

            color = (40, 210, 40) if status == "DEMO MATCH" else (0, 150, 255)
            cv2.putText(frame, "Status: " + status, (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
            agreeing = sum(1 for score in last_scores if score >= threshold)
            scores_text = " ".join(str(score) for score in last_scores) or "--"
            cv2.putText(frame, f"Sample scores: {scores_text}  (need {threshold})",
                        (12, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            cv2.putText(frame, f"Samples matching: {agreeing}/{len(enrolled)}  (need {required_samples})",
                        (12, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            cv2.putText(frame, "T = reset | ESC = quit", (12, 118),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
            cv2.imshow("FORTRESS Palm Verify TEST - not a security guarantee", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key in (ord("t"), ord("T")):
                last_scores = []
                streak = 0
                status = "SHOW OPEN PALM"
                last_eval = 0.0
    finally:
        cap.release()
        landmarker.close()
        cv2.destroyAllWindows()

    print("\nTest ended. This result is only a rough texture-similarity signal.")
    print("Record: enrolled palm should match consistently; other palms should not.")
    print("If another palm ever shows DEMO MATCH, raise CALIBRATION_SHARE (e.g. 0.75)")
    print("and test again. If it still matches, do not use this as an access-control gate.")
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
