"""
FORTRESS PALM SCAN — palmprint enrollment and verification (no hand landmarks)

Place your palm inside the scan box on screen. The scanner:
  1. checks the image is good enough (skin fills the box, sharp, well lit),
  2. finds palm-line features (ORB) and aligns your palm to each enrolled
     scan with a RANSAC homography, so position and angle can vary a little,
  3. compares the aligned palm lines with a Competitive Code (6 Gabor
     orientations per pixel), the classic palmprint matching method,
  4. needs most enrolled scans to agree, several checks in a row.

Requires only: opencv-python, numpy.   Run:  python FORTRESS_PALM_SCAN.py

HONEST LIMIT: a normal webcam sees palm *lines*, not palm *veins*. This is far
stricter than the old test, but it is not certified biometric security and a
good printed photo of a palm may fool it. Test it with several people.
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# ------------------------------------------------------------------
# Files
# ------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
ENROLLMENT_NAME = "fortress_palm_scan_enrollment.npz"


def data_folder():
    # A script opened straight from a download/temp folder should still
    # keep its enrollment in the project folder.
    if "AppData" in SCRIPT_DIR.parts:
        nexus = Path.home() / "Downloads" / "NEXUS"
        if nexus.is_dir():
            return nexus
    return SCRIPT_DIR


DATA_DIR = data_folder()
ENROLLMENT_PATH = DATA_DIR / ENROLLMENT_NAME

# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

CAMERA_INDEX = 0
WINDOW = "FORTRESS PALM SCAN"

ROI_SIZE = 256            # palm image size used for matching
CODE_SIZE = 128           # Competitive Code resolution
BOX_FRACTION = 0.55       # scan box side, as a share of the frame height

ENROLL_SAMPLES = 5

# Quality gate
MIN_SKIN_RATIO = 0.60     # share of the box that must look like skin
MIN_SHARPNESS = 40.0      # variance of Laplacian
MIN_BRIGHTNESS = 50
MAX_BRIGHTNESS = 210

# Alignment (ORB + RANSAC)
ORB = cv2.ORB_create(nfeatures=1000, scaleFactor=1.2, nlevels=6, edgeThreshold=15)
MATCHER = cv2.BFMatcher(cv2.NORM_HAMMING)
RATIO_TEST = 0.75
MIN_INLIERS = 25          # never accept fewer aligned features than this
RANSAC_PIXELS = 4.0

# Palm-line comparison (Competitive Code distance: 0 = identical, ~0.5 = random)
MAX_CODE_DISTANCE = 0.36  # never accept a larger distance than this
CODE_MARGIN = 0.05        # allowed above your own enrolled scans' distance

# Decision
SAMPLE_AGREEMENT = 0.6    # 3 of 5 enrolled scans must match
EVAL_INTERVAL = 0.25
REQUIRED_CONSECUTIVE = 5  # about 1.25 s of continuous matching

# ------------------------------------------------------------------
# Image processing
# ------------------------------------------------------------------

CLAHE = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))


def make_gabor_bank():
    kernels = []
    for k in range(6):
        kernel = cv2.getGaborKernel(
            (17, 17), sigma=3.5, theta=k * np.pi / 6, lambd=9.0, gamma=0.5, psi=0,
            ktype=cv2.CV_32F,
        )
        kernel -= kernel.mean()   # zero DC: ignore overall brightness
        kernels.append(kernel)
    return kernels


GABOR_BANK = make_gabor_bank()


def scan_box(frame):
    h, w = frame.shape[:2]
    side = int(h * BOX_FRACTION)
    x1, y1 = (w - side) // 2, (h - side) // 2
    return x1, y1, x1 + side, y1 + side


def skin_mask(bgr):
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return mask


def extract_palm(frame):
    """Palm image from the scan box, or (None, reason) if quality is too low."""
    x1, y1, x2, y2 = scan_box(frame)
    box = cv2.resize(frame[y1:y2, x1:x2], (ROI_SIZE, ROI_SIZE), interpolation=cv2.INTER_AREA)

    mask = skin_mask(box)
    skin_ratio = float(np.count_nonzero(mask)) / mask.size
    if skin_ratio < MIN_SKIN_RATIO:
        return None, None, "PLACE PALM TO FILL THE BOX"

    gray = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY)
    brightness = float(gray[mask > 0].mean())
    if brightness < MIN_BRIGHTNESS:
        return None, None, "TOO DARK — ADD LIGHT"
    if brightness > MAX_BRIGHTNESS:
        return None, None, "TOO BRIGHT — AVOID GLARE"

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F)[mask > 0].var())
    if sharpness < MIN_SHARPNESS:
        return None, None, "HOLD STILL — IMAGE BLURRED"

    return CLAHE.apply(gray), mask, None


def competitive_code(gray):
    """Per-pixel dominant palm-line orientation, 0..5."""
    small = cv2.resize(gray, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    responses = np.stack([cv2.filter2D(small, cv2.CV_32F, k) for k in GABOR_BANK])
    # Palm lines are dark, so the strongest line orientation gives the minimum response.
    return np.argmin(responses, axis=0).astype(np.uint8)


def code_distance(code_a, code_b, valid):
    """Angular distance between two codes over valid pixels: 0 same .. 1 opposite."""
    if np.count_nonzero(valid) < 0.4 * valid.size:
        return 1.0
    diff = np.abs(code_a.astype(np.int16) - code_b.astype(np.int16))
    diff = np.minimum(diff, 6 - diff)          # orientations wrap around
    return float(diff[valid].mean() / 3.0)


def features(gray, mask):
    keypoints, descriptors = ORB.detectAndCompute(gray, mask)
    return keypoints, descriptors


def mutual_matches(desc_a, desc_b):
    if desc_a is None or desc_b is None or len(desc_a) < 2 or len(desc_b) < 2:
        return []

    def ratio(query, train):
        out = {}
        for pair in MATCHER.knnMatch(query, train, k=2):
            if len(pair) == 2 and pair[0].distance < RATIO_TEST * pair[1].distance:
                out[pair[0].queryIdx] = pair[0].trainIdx
        return out

    forward = ratio(desc_a, desc_b)
    backward = ratio(desc_b, desc_a)
    return [(a, b) for a, b in forward.items() if backward.get(b) == a]


def sane_homography(H):
    """Reject alignments that squash, mirror or wildly distort the palm."""
    if H is None:
        return False
    A = H[:2, :2] / H[2, 2]
    det = float(np.linalg.det(A))
    perspective = float(np.abs(H[2, :2] / H[2, 2]).max())
    return 0.6 < det < 1.6 and perspective < 0.002


def compare(live, enrolled):
    """(inliers, code distance) of a live palm against one enrolled scan."""
    live_gray, live_mask, live_kp, live_desc = live
    ref_gray, ref_mask, ref_kp, ref_desc, ref_code = enrolled

    pairs = mutual_matches(live_desc, ref_desc)
    if len(pairs) < MIN_INLIERS:
        return len(pairs), 1.0

    src = np.float32([live_kp[a].pt for a, _ in pairs]).reshape(-1, 1, 2)
    dst = np.float32([ref_kp[b].pt for _, b in pairs]).reshape(-1, 1, 2)
    H, inlier_mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PIXELS)
    inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    if inliers < MIN_INLIERS or not sane_homography(H):
        return inliers, 1.0

    # Line up the live palm with the enrolled one, then compare palm lines
    # only where both images show palm skin.
    warped = cv2.warpPerspective(live_gray, H, (ROI_SIZE, ROI_SIZE))
    warped_mask = cv2.warpPerspective(live_mask, H, (ROI_SIZE, ROI_SIZE))
    both = cv2.bitwise_and(warped_mask, ref_mask)
    valid = cv2.resize(both, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_NEAREST) > 0
    valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0   # skip edges

    return inliers, code_distance(competitive_code(warped), ref_code, valid)


# ------------------------------------------------------------------
# Enrollment storage
# ------------------------------------------------------------------

def prepare_enrolled(grays, masks):
    enrolled = []
    for gray, mask in zip(grays, masks):
        kp, desc = features(gray, mask)
        enrolled.append((gray, mask, kp, desc, competitive_code(gray)))
    return enrolled


def calibrate(enrolled):
    """Thresholds from how well your own enrolled scans match each other."""
    inliers, distances = [], []
    for i in range(len(enrolled)):
        for j in range(len(enrolled)):
            if i == j:
                continue
            gray, mask, kp, desc, _ = enrolled[i]
            n, d = compare((gray, mask, kp, desc), enrolled[j])
            inliers.append(n)
            distances.append(d)
    good = [d for d in distances if d < 1.0]
    genuine_distance = float(np.median(good)) if good else 1.0
    distance_limit = min(MAX_CODE_DISTANCE, genuine_distance + CODE_MARGIN)
    inlier_limit = max(MIN_INLIERS, int(np.median(inliers) * 0.4)) if inliers else MIN_INLIERS
    return {
        "inlier_limit": inlier_limit,
        "distance_limit": distance_limit,
        "genuine_distance": genuine_distance,
        "pair_inliers": inliers,
        "pair_distances": distances,
    }


def save_enrollment(grays, masks):
    np.savez_compressed(ENROLLMENT_PATH, grays=np.stack(grays), masks=np.stack(masks))


def load_enrollment():
    with np.load(ENROLLMENT_PATH, allow_pickle=False) as data:
        return list(data["grays"]), list(data["masks"])


# ------------------------------------------------------------------
# Screen
# ------------------------------------------------------------------

def draw(frame, title, status, color, lines=(), progress=None):
    x1, y1, x2, y2 = scan_box(frame)
    shade = (frame * 0.35).astype(np.uint8)
    shade[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
    frame[:] = shade

    arm = (x2 - x1) // 6
    for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + dx * arm, cy), color, 3)
        cv2.line(frame, (cx, cy), (cx, cy + dy * arm), color, 3)

    # Sweeping scan line
    t = (np.sin(time.time() * 3) + 1) / 2
    y = int(y1 + t * (y2 - y1))
    cv2.line(frame, (x1 + 4, y), (x2 - 4, y), color, 1)

    cv2.putText(frame, title, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 217, 32), 2)
    cv2.putText(frame, status, (16, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (16, 92 + i * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1)

    h, w = frame.shape[:2]
    if progress is not None:
        cv2.rectangle(frame, (16, h - 30), (w - 16, h - 18), (90, 70, 20), 1)
        cv2.rectangle(frame, (18, h - 28), (18 + int((w - 36) * min(progress, 1.0)), h - 20), color, -1)
    cv2.putText(frame, "ESC = quit", (w - 120, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)


def open_camera():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        cap = cv2.VideoCapture(CAMERA_INDEX)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


# ------------------------------------------------------------------
# Enroll
# ------------------------------------------------------------------

CYAN = (255, 217, 32)
GREEN = (120, 230, 40)
ORANGE = (0, 150, 255)
RED = (70, 70, 255)


def enroll():
    print("ENROLL: hold the SAME hand, palm facing the camera, fingers together,")
    print("filling the box. Move it slightly between captures. 5 scans are taken.")
    cap = open_camera()
    if not cap.isOpened():
        print("Could not open webcam.")
        return False

    grays, masks = [], []
    good_since = None
    last_capture = 0.0
    try:
        while len(grays) < ENROLL_SAMPLES:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame unavailable.")
                return False
            frame = cv2.flip(frame, 1)
            gray, mask, problem = extract_palm(frame)
            now = time.time()

            if problem:
                good_since = None
                status, color = problem, ORANGE
            else:
                good_since = good_since or now
                held = now - good_since
                status, color = f"HOLD STILL... {max(0.0, 1.0 - held):.1f}s", CYAN
                if held >= 1.0 and now - last_capture >= 1.5:
                    # Each new scan must line up with the first one.
                    if grays:
                        first = prepare_enrolled(grays[:1], masks[:1])[0]
                        kp, desc = features(gray, mask)
                        n, d = compare((gray, mask, kp, desc), first)
                        if n < MIN_INLIERS or d > MAX_CODE_DISTANCE:
                            status, color = "SCAN DIDN'T MATCH FIRST ONE — SAME HAND, SAME POSE", RED
                            good_since = None
                            draw(frame, "FORTRESS PALM ENROLLMENT", status, color,
                                 [f"Scans: {len(grays)}/{ENROLL_SAMPLES}"], len(grays) / ENROLL_SAMPLES)
                            cv2.imshow(WINDOW, frame)
                            cv2.waitKey(700)
                            continue
                    grays.append(gray)
                    masks.append(mask)
                    last_capture = now
                    good_since = None
                    status, color = f"SCAN {len(grays)} CAPTURED", GREEN
                    print(f"  scan {len(grays)}/{ENROLL_SAMPLES} captured")

            draw(frame, "FORTRESS PALM ENROLLMENT", status, color,
                 [f"Scans: {len(grays)}/{ENROLL_SAMPLES}"], len(grays) / ENROLL_SAMPLES)
            cv2.imshow(WINDOW, frame)
            if cv2.waitKey(1) & 0xFF == 27:
                print("Enrollment cancelled.")
                return False
    finally:
        cap.release()
        cv2.destroyAllWindows()

    cal = calibrate(prepare_enrolled(grays, masks))
    print(f"Your scans vs each other: distance {cal['genuine_distance']:.3f}, "
          f"inliers {sorted(cal['pair_inliers'])}")
    if cal["genuine_distance"] > MAX_CODE_DISTANCE:
        print("Your 5 scans are not consistent enough. Enroll again with brighter,")
        print("even light, the palm flat and filling the box. Nothing was saved.")
        return False

    save_enrollment(grays, masks)
    print("Enrollment saved to", ENROLLMENT_PATH)
    return True


# ------------------------------------------------------------------
# Verify
# ------------------------------------------------------------------

def verify():
    if not ENROLLMENT_PATH.is_file():
        print("No enrollment found at", ENROLLMENT_PATH)
        print("Choose 1 to enroll first.")
        return None

    grays, masks = load_enrollment()
    enrolled = prepare_enrolled(grays, masks)
    cal = calibrate(enrolled)
    required = max(1, int(np.ceil(len(enrolled) * SAMPLE_AGREEMENT)))
    print(f"Thresholds: inliers >= {cal['inlier_limit']}, palm-line distance <= "
          f"{cal['distance_limit']:.3f} (your scans: {cal['genuine_distance']:.3f}), "
          f"{required} of {len(enrolled)} scans, {REQUIRED_CONSECUTIVE} checks in a row")

    cap = open_camera()
    if not cap.isOpened():
        print("Could not open webcam.")
        return None

    streak = 0
    last_eval = 0.0
    scores = []
    status, color = "PLACE PALM IN THE BOX", CYAN
    verified = False
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame unavailable.")
                return None
            frame = cv2.flip(frame, 1)
            now = time.time()

            if now - last_eval >= EVAL_INTERVAL:
                last_eval = now
                gray, mask, problem = extract_palm(frame)
                if problem:
                    streak, scores = 0, []
                    status, color = problem, ORANGE
                else:
                    kp, desc = features(gray, mask)
                    scores = [compare((gray, mask, kp, desc), ref) for ref in enrolled]
                    agreeing = sum(1 for n, d in scores
                                   if n >= cal["inlier_limit"] and d <= cal["distance_limit"])
                    streak = streak + 1 if agreeing >= required else 0
                    if streak >= REQUIRED_CONSECUTIVE:
                        verified = True
                        status, color = "PALM VERIFIED", GREEN
                    elif streak:
                        status, color = f"SCANNING {streak}/{REQUIRED_CONSECUTIVE}", CYAN
                    else:
                        status, color = "NO MATCH", RED

            lines = []
            if scores:
                lines.append("Aligned features: " + " ".join(str(n) for n, _ in scores)
                             + f"  (need {cal['inlier_limit']})")
                lines.append("Line distance: " + " ".join(f"{d:.2f}" if d < 1 else "--" for _, d in scores)
                             + f"  (need <= {cal['distance_limit']:.2f})")
            draw(frame, "FORTRESS PALM SCAN", status, color, lines, streak / REQUIRED_CONSECUTIVE)
            cv2.imshow(WINDOW, frame)

            if verified:
                cv2.waitKey(1200)
                print("PALM VERIFIED")
                return True
            if cv2.waitKey(1) & 0xFF == 27:
                print("Scan cancelled.")
                return False
    finally:
        cap.release()
        cv2.destroyAllWindows()


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    print("=" * 60)
    print("FORTRESS PALM SCAN (palmprint, no landmarks)")
    print("Enrollment file:", ENROLLMENT_PATH)
    print("=" * 60)
    print("1 = Enroll my palm")
    print("2 = Verify (scan)")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        if enroll():
            print("Now test with 2. Then let other people try: they should get NO MATCH.")
    else:
        verify()
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
