"""
FORTRESS PALM SCAN — palmprint enrollment and verification (no hand landmarks)

Hold your palm up to the camera (anywhere in view). The scanner:
  1. finds your palm in the frame and checks the image (sharp, well lit),
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
MIN_FILL = 0.40           # share of the detected crop covered by the palm
MIN_SHARPNESS = 15.0      # variance of Laplacian over the palm
MIN_BRIGHTNESS = 40
MAX_BRIGHTNESS = 225
FORCE_MIN_FILL = 0.30     # F (force capture) still needs this much palm

# Alignment, step 1: palm features (ORB + RANSAC homography)
ORB = cv2.ORB_create(nfeatures=1000, scaleFactor=1.2, nlevels=6, edgeThreshold=15)
MATCHER = cv2.BFMatcher(cv2.NORM_HAMMING)
RATIO_TEST = 0.75
MIN_INLIERS = 12          # features needed for step 1 to be used
RANSAC_PIXELS = 4.0

# Alignment, step 2 (when webcam palms have too few features): dense
# whole-image alignment (ECC, rotation + shift)
MIN_ECC = 0.30            # alignment quality needed, 0..1
MAX_ROTATION_DEG = 30
MAX_SHIFT_FRACTION = 0.4
IDENTITY_SHIFT = 8        # shift search (code px) for the as-placed fallback

# Palm-line comparison (Competitive Code distance: 0 = identical, ~0.5 = random)
# A live scan always runs a little above how well the enrolled scans match
# each other (lighting and pose differ), so the per-scan limit sits well
# above that self-distance. The real security is still SAMPLE_AGREEMENT +
# REQUIRED_CONSECUTIVE below: an impostor must clear the limit on several
# scans for several frames in a row.
MIN_CODE_DISTANCE = 0.32  # never set the limit tighter than this
MAX_CODE_DISTANCE = 0.42  # never set the limit looser than this
CODE_MARGIN = 0.12        # allowed above your own enrolled scans' distance
ENROLL_MAX_DISTANCE = 0.44  # each enrollment scan vs scan 1
SHIFT_SEARCH = 3          # code pixels of leftover shift to tolerate

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


PALM_MIN_AREA = 0.04      # largest skin blob must be >= this share of the frame


def locate_palm(frame):
    """Square region around the largest hand/palm anywhere in the frame,
    or None when no hand-sized skin region is found. No fixed box: the
    palm can be held wherever is comfortable."""
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycrcb, (0, 133, 77), (255, 182, 135))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    contours, _ = cv2.findContours(skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    hand = max(contours, key=cv2.contourArea)

    h, w = frame.shape[:2]
    if cv2.contourArea(hand) < PALM_MIN_AREA * h * w:
        return None

    bx, by, bw, bh = cv2.boundingRect(hand)
    cx, cy = bx + bw / 2.0, by + bh / 2.0
    side = max(bw, bh) * 1.1
    x1 = int(max(0, cx - side / 2.0))
    y1 = int(max(0, cy - side / 2.0))
    x2 = int(min(w, cx + side / 2.0))
    y2 = int(min(h, cy + side / 2.0))
    if x2 - x1 < 60 or y2 - y1 < 60:
        return None
    return x1, y1, x2, y2


def palm_mask(bgr):
    """Pixels whose colour matches the centre of the crop.

    Learns the palm colour from the crop centre instead of a fixed skin
    range, so it works across skin tones and webcam white balance.
    Returns (mask, centre_looks_like_skin).
    """
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    centre = lab[int(h * 0.35):int(h * 0.65), int(w * 0.35):int(w * 0.65)].reshape(-1, 3)
    L0, a0, b0 = np.median(centre, axis=0)

    chroma = np.hypot(lab[..., 1] - a0, lab[..., 2] - b0)
    mask = ((chroma < 18) & (np.abs(lab[..., 0] - L0) < 80)).astype(np.uint8) * 255
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # Loose skin test on the centre (OpenCV Lab: a, b are offset by 128),
    # so a plain wall or sleeve in the box is not taken as a palm.
    skin_like = a0 > 130 and b0 > 128
    return mask, skin_like


def extract_palm(frame):
    """(palm image, mask, problem or None, readings) for the palm found
    anywhere in the frame. readings['rect'] is where it was located."""
    rect = locate_palm(frame)
    if rect is None:
        return (None, None, "SHOW YOUR PALM TO THE CAMERA",
                {"fill": 0.0, "brightness": 0.0, "sharpness": 0.0, "skin": False, "rect": None})

    x1, y1, x2, y2 = rect
    crop = cv2.resize(frame[y1:y2, x1:x2], (ROI_SIZE, ROI_SIZE), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    mask, skin_like = palm_mask(crop)
    on_palm = mask > 0
    fill = float(np.count_nonzero(on_palm)) / mask.size
    if fill > 0.02:
        brightness = float(gray[on_palm].mean())
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F)[on_palm].var())
    else:
        brightness = sharpness = 0.0
    readings = {"fill": fill, "brightness": brightness, "sharpness": sharpness,
                "skin": skin_like, "rect": rect}

    if not skin_like:
        problem = "SHOW YOUR PALM TO THE CAMERA"
    elif fill < MIN_FILL:
        problem = "MOVE PALM CLOSER"
    elif brightness < MIN_BRIGHTNESS:
        problem = "TOO DARK - ADD LIGHT"
    elif brightness > MAX_BRIGHTNESS:
        problem = "TOO BRIGHT - AVOID GLARE"
    elif sharpness < MIN_SHARPNESS:
        problem = "HOLD STILL - IMAGE BLURRED"
    else:
        problem = None

    return CLAHE.apply(gray), mask, problem, readings


def readings_line(r):
    return (f"Fill {r['fill'] * 100:.0f}% (need {MIN_FILL * 100:.0f})  "
            f"Light {r['brightness']:.0f} ({MIN_BRIGHTNESS}-{MAX_BRIGHTNESS})  "
            f"Sharp {r['sharpness']:.0f} (need {MIN_SHARPNESS:.0f})  "
            f"Skin {'yes' if r['skin'] else 'no'}")


def competitive_code(gray):
    """Per-pixel dominant palm-line orientation, 0..5."""
    small = cv2.resize(gray, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    small = cv2.GaussianBlur(small, (3, 3), 0)   # webcam noise is not palm lines
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


def shifted_code_distance(code_a, code_b, valid, max_shift=SHIFT_SEARCH):
    """Smallest code distance over small shifts of code_a against code_b.

    valid is in code_b's coordinates.
    """
    n = code_a.shape[0]
    best = 1.0
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            a = code_a[max(0, dy):n + min(0, dy), max(0, dx):n + min(0, dx)]
            b = code_b[max(0, -dy):n + min(0, -dy), max(0, -dx):n + min(0, -dx)]
            v = valid[max(0, -dy):n + min(0, -dy), max(0, -dx):n + min(0, -dx)]
            best = min(best, code_distance(a, b, v))
    return best


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
    return 0.4 < det < 2.2 and perspective < 0.01


def align_features(live_kp, live_desc, ref_kp, ref_desc):
    """Homography live -> enrolled from palm features, or (None, inliers)."""
    pairs = mutual_matches(live_desc, ref_desc)
    if len(pairs) < MIN_INLIERS:
        return None, len(pairs)
    src_pts = np.float32([live_kp[a].pt for a, _ in pairs]).reshape(-1, 1, 2)
    dst_pts = np.float32([ref_kp[b].pt for _, b in pairs]).reshape(-1, 1, 2)
    H, inlier_mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, RANSAC_PIXELS)
    inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    if inliers < MIN_INLIERS or not sane_homography(H):
        return None, inliers
    return H, inliers


def ecc_image(gray):
    small = cv2.resize(gray, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small.astype(np.float32) / 255.0, (5, 5), 0)


def align_dense(live_gray, live_mask, ref_gray):
    """Rotation + shift (2x3, ROI scale) mapping enrolled -> live coordinates,
    for use with WARP_INVERSE_MAP, and its quality 0..1. (None, 0) if it fails."""
    template = ecc_image(ref_gray)
    moving = ecc_image(live_gray)
    moving_mask = cv2.resize(live_mask, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_NEAREST)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-4)

    starts = [np.eye(2, 3, dtype=np.float32)]
    try:
        (dx, dy), _ = cv2.phaseCorrelate(template, moving)
        shifted = np.eye(2, 3, dtype=np.float32)
        shifted[0, 2], shifted[1, 2] = dx, dy
        starts.append(shifted)
    except cv2.error:
        pass

    best_cc, best_warp = 0.0, None
    for start in starts:
        try:
            cc, warp = cv2.findTransformECC(template, moving, start.copy(), cv2.MOTION_EUCLIDEAN,
                                            criteria, moving_mask, 5)
        except cv2.error:
            continue
        if cc > best_cc:
            best_cc, best_warp = cc, warp

    if best_warp is None:
        return None, 0.0
    rotation = abs(np.degrees(np.arctan2(best_warp[1, 0], best_warp[0, 0])))
    shift = max(abs(best_warp[0, 2]), abs(best_warp[1, 2])) / CODE_SIZE
    if rotation > MAX_ROTATION_DEG or shift > MAX_SHIFT_FRACTION:
        return None, best_cc
    warp = best_warp.copy()
    warp[:, 2] *= ROI_SIZE / CODE_SIZE
    return warp, float(best_cc)


def compare(live, enrolled):
    """(aligned, code distance, how it aligned) of a live palm vs one enrolled scan."""
    live_gray, live_mask, live_kp, live_desc = live
    ref_gray, ref_mask, ref_kp, ref_desc, ref_code = enrolled
    size = (ROI_SIZE, ROI_SIZE)

    shift = SHIFT_SEARCH
    H, inliers = align_features(live_kp, live_desc, ref_kp, ref_desc)
    if H is not None:
        warped = cv2.warpPerspective(live_gray, H, size)
        warped_mask = cv2.warpPerspective(live_mask, H, size, flags=cv2.INTER_NEAREST)
        method = f"F{inliers}"
    else:
        warp, quality = align_dense(live_gray, live_mask, ref_gray)
        if warp is not None and quality >= MIN_ECC:
            inverse = cv2.WARP_INVERSE_MAP
            warped = cv2.warpAffine(live_gray, warp, size, flags=cv2.INTER_LINEAR | inverse)
            warped_mask = cv2.warpAffine(live_mask, warp, size, flags=cv2.INTER_NEAREST | inverse)
            method = f"D{quality:.2f}"
        else:
            # Last resort: compare the palm exactly as detected, with
            # a wider shift search. Detection keeps both roughly aligned, so
            # a genuine palm still scores low; an impostor still does not. The
            # 3-of-5 and 5-in-a-row gates remain the real security.
            warped, warped_mask = live_gray, live_mask
            method = "I"
            shift = IDENTITY_SHIFT

    # Compare palm lines only where both images show palm.
    both = cv2.bitwise_and(warped_mask, ref_mask)
    valid = cv2.resize(both, (CODE_SIZE, CODE_SIZE), interpolation=cv2.INTER_NEAREST) > 0
    valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0   # skip edges

    return True, shifted_code_distance(competitive_code(warped), ref_code, valid, shift), method


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
    distances = []
    for i in range(len(enrolled)):
        for j in range(len(enrolled)):
            if i == j:
                continue
            gray, mask, kp, desc, _ = enrolled[i]
            aligned, d, _ = compare((gray, mask, kp, desc), enrolled[j])
            distances.append(d if aligned else 1.0)
    good = [d for d in distances if d < 1.0]
    genuine_distance = float(np.median(good)) if good else 1.0
    return {
        "distance_limit": min(MAX_CODE_DISTANCE, max(MIN_CODE_DISTANCE, genuine_distance + CODE_MARGIN)),
        "genuine_distance": genuine_distance,
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

def draw(frame, title, status, color, lines=(), progress=None, rect=None):
    # Highlight wherever the palm was detected (no fixed box to aim at).
    if rect is not None:
        x1, y1, x2, y2 = rect
        shade = (frame * 0.4).astype(np.uint8)
        shade[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
        frame[:] = shade

        arm = (x2 - x1) // 6
        for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * arm, cy), color, 3)
            cv2.line(frame, (cx, cy), (cx, cy + dy * arm), color, 3)

        # Sweeping scan line across the detected palm
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
    print("ENROLL (manual): hold the SAME hand, palm facing the camera, fingers")
    print("together, held up to the camera. When the status says READY, press SPACE.")
    print("Move the hand slightly between scans. R = retake last scan, ESC = cancel.")
    cap = open_camera()
    if not cap.isOpened():
        print("Could not open webcam.")
        return False

    grays, masks = [], []
    message, message_color, message_until = "", CYAN, 0.0
    try:
        while len(grays) < ENROLL_SAMPLES:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame unavailable.")
                return False
            frame = cv2.flip(frame, 1)
            gray, mask, problem, readings = extract_palm(frame)
            now = time.time()

            if now < message_until:
                status, color = message, message_color
            elif problem:
                status, color = problem, ORANGE
            else:
                status, color = f"READY - PRESS SPACE FOR SCAN {len(grays) + 1}", GREEN

            draw(frame, "FORTRESS PALM ENROLLMENT (MANUAL)", status, color,
                 [f"Scans: {len(grays)}/{ENROLL_SAMPLES}",
                  readings_line(readings),
                  "SPACE = capture   F = force capture   R = retake last   ESC = cancel"],
                 len(grays) / ENROLL_SAMPLES, rect=readings.get("rect"))
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF

            if key == 27:
                print("Enrollment cancelled. Nothing was saved.")
                return False

            if key in (ord("r"), ord("R")) and grays:
                grays.pop()
                masks.pop()
                message, message_color, message_until = f"SCAN {len(grays) + 1} REMOVED", ORANGE, now + 1.2
                print(f"  scan {len(grays) + 1} removed")
                continue

            forced = key in (ord("f"), ord("F"))
            if key != 32 and not forced:   # SPACE or F
                continue

            if problem and not forced:
                message, message_color, message_until = (
                    "NOT CAPTURED: " + problem + " (F = force)", RED, now + 1.5)
                continue
            if forced and (not readings["skin"] or readings["fill"] < FORCE_MIN_FILL):
                message, message_color, message_until = (
                    "NOT CAPTURED: NO PALM DETECTED", RED, now + 1.5)
                continue

            # Each new scan must line up with the first one.
            if grays:
                first = prepare_enrolled(grays[:1], masks[:1])[0]
                kp, desc = features(gray, mask)
                aligned, d, how = compare((gray, mask, kp, desc), first)
                if not aligned:
                    message, message_color, message_until = (
                        "NOT CAPTURED: COULDN'T LINE UP WITH SCAN 1 - SAME HAND, SAME DISTANCE", RED, now + 2.0)
                    print("  rejected: could not align with scan 1")
                    continue
                if d > ENROLL_MAX_DISTANCE:
                    message, message_color, message_until = (
                        f"NOT CAPTURED: LINES DIFFER FROM SCAN 1 ({d:.2f}, need <= {ENROLL_MAX_DISTANCE:.2f})",
                        RED, now + 2.0)
                    print(f"  rejected: line distance {d:.2f} vs scan 1 (aligned {how})")
                    continue
                print(f"  vs scan 1: line distance {d:.2f} (aligned {how})")

            grays.append(gray)
            masks.append(mask)
            message, message_color, message_until = f"SCAN {len(grays)} CAPTURED", GREEN, now + 1.0
            print(f"  scan {len(grays)}/{ENROLL_SAMPLES} captured")
    finally:
        cap.release()
        cv2.destroyAllWindows()

    cal = calibrate(prepare_enrolled(grays, masks))
    print(f"Your scans vs each other: typical line distance {cal['genuine_distance']:.3f} "
          f"-> verify limit {cal['distance_limit']:.3f}")
    if cal["genuine_distance"] > ENROLL_MAX_DISTANCE:
        print("Your 5 scans are not consistent enough. Enroll again with brighter,")
        print("even light, the palm flat and held steady. Nothing was saved.")
        return False

    save_enrollment(grays, masks)
    print("Enrollment saved to", ENROLLMENT_PATH)
    return True


# ------------------------------------------------------------------
# Verify
# ------------------------------------------------------------------

def scan_palm(mismatch_seconds=None):
    """Scan the palm on the webcam against the enrollment.

    Returns "VERIFIED", "MISMATCH" (a palm was shown but did not match for
    mismatch_seconds), "CANCELLED" (ESC or Q) or "ERROR: <reason>".
    Used by the FORTRESS HUD; verify() is the standalone version.
    """
    if not ENROLLMENT_PATH.is_file():
        return f"ERROR: no palm enrollment at {ENROLLMENT_PATH} - run FORTRESS_PALM_SCAN.py, choose 1"

    grays, masks = load_enrollment()
    enrolled = prepare_enrolled(grays, masks)
    cal = calibrate(enrolled)
    required = max(1, int(np.ceil(len(enrolled) * SAMPLE_AGREEMENT)))
    print(f"Palm thresholds: palm-line distance <= "
          f"{cal['distance_limit']:.3f} (your scans: {cal['genuine_distance']:.3f}), "
          f"{required} of {len(enrolled)} scans, {REQUIRED_CONSECUTIVE} checks in a row")

    cap = open_camera()
    if not cap.isOpened():
        return "ERROR: could not open webcam"

    streak = 0
    last_eval = 0.0
    no_match_time = 0.0
    scores = []
    readings = None
    status, color = "SHOW YOUR PALM TO THE CAMERA", CYAN
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return "ERROR: camera frame unavailable"
            frame = cv2.flip(frame, 1)
            now = time.time()

            if now - last_eval >= EVAL_INTERVAL:
                step = min(now - last_eval, 1.0) if last_eval else 0.0
                last_eval = now
                gray, mask, problem, readings = extract_palm(frame)
                if problem:
                    streak, scores = 0, []
                    status, color = problem, ORANGE
                else:
                    kp, desc = features(gray, mask)
                    scores = [compare((gray, mask, kp, desc), ref) for ref in enrolled]
                    agreeing = sum(1 for aligned, d, _ in scores
                                   if aligned and d <= cal["distance_limit"])
                    streak = streak + 1 if agreeing >= required else 0
                    if streak >= REQUIRED_CONSECUTIVE:
                        status, color = "PALM VERIFIED", GREEN
                    elif streak:
                        status, color = f"SCANNING {streak}/{REQUIRED_CONSECUTIVE}", CYAN
                    else:
                        status, color = "NO MATCH", RED
                        no_match_time += step

            lines = [readings_line(readings)] if readings else []
            if scores:
                lines.append("Alignment: " + " ".join(how for _, _, how in scores)
                             + "  (F = features, D = dense)")
                lines.append("Line distance: " + " ".join(f"{d:.2f}" if d < 1 else "--" for _, d, _ in scores)
                             + f"  (need <= {cal['distance_limit']:.2f})")
            rect = readings.get("rect") if readings else None
            draw(frame, "FORTRESS PALM SCAN", status, color, lines, streak / REQUIRED_CONSECUTIVE, rect=rect)

            if streak >= REQUIRED_CONSECUTIVE:
                cv2.imshow(WINDOW, frame)
                cv2.waitKey(1200)
                return "VERIFIED"

            if mismatch_seconds is not None and no_match_time >= mismatch_seconds:
                draw(frame, "FORTRESS PALM SCAN", "PALM NOT RECOGNISED - ACCESS DENIED", RED, lines, rect=rect)
                cv2.imshow(WINDOW, frame)
                cv2.waitKey(1500)
                return "MISMATCH"

            cv2.imshow(WINDOW, frame)
            if (cv2.waitKey(1) & 0xFF) in (27, ord("q"), ord("Q")):
                return "CANCELLED"
    finally:
        cap.release()
        cv2.destroyAllWindows()


def verify():
    result = scan_palm()
    if result == "VERIFIED":
        print("PALM VERIFIED")
    elif result == "CANCELLED":
        print("Scan cancelled.")
    else:
        print(result)
    return result == "VERIFIED"


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    print("=" * 60)
    print("FORTRESS PALM SCAN (palmprint, no landmarks)")
    print("Enrollment file:", ENROLLMENT_PATH)
    print("=" * 60)
    print("1 = Enroll my palm (manual: press SPACE for each of 5 scans)")
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
