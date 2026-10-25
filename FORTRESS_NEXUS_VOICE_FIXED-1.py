import tkinter as tk
import math
import threading
import os
import subprocess
import time
import random
import re
import requests

import sounddevice as sd
import speech_recognition as sr
import pyttsx3
import numpy as np
import cv2

from google import genai
from google.genai import types as genai_types
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
# NEXUS AI — GOOGLE GEMINI
#
# API key: set the GEMINI_API_KEY environment variable, or put the
# key alone in a file called gemini_key.txt next to this script.
#
# Gemini model names change often, so NEXUS tries these in order and
# keeps the first one the key can use. Set GEMINI_MODEL to force one.
# ============================================================

GEMINI_MODELS = [
    # Try several commonly available Flash models; availability varies by API key.
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
]

# Room for the model's own thinking plus a short spoken answer.
GEMINI_MAX_OUTPUT_TOKENS = 1024

_gemini_client = None
_gemini_model = os.environ.get("GEMINI_MODEL") or None


def _clean_key(raw):
    # Google AI Studio keys look like AIza + 35 url-safe chars. If the file
    # has a label, a note, or several lines, pull the real key out of it.
    match = re.search(r"AIza[0-9A-Za-z_\-]{35}", raw)
    if match:
        return match.group(0)

    # Otherwise take the first non-empty line and strip quotes / NAME= prefix.
    key = ""
    for line in raw.splitlines():
        if line.strip():
            key = line.strip()
            break
    if len(key) >= 2 and key[0] in "\"'" and key[-1] in "\"'":
        key = key[1:-1].strip()
    if key.upper().startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
        key = key.split("=", 1)[1].strip().strip("\"'")
    return key


def _mask_key(key):
    if not key:
        return "(empty)"
    if len(key) <= 10:
        return f"{len(key)} chars"
    return f"{key[:6]}...{key[-4:]} ({len(key)} chars)"


# Optional: paste your free key from https://aistudio.google.com/apikey
# between the quotes. Leaving it blank is fine (NEXUS then uses the key
# file, the environment variable, or built-in offline answers).
INLINE_GEMINI_KEY = ""


def get_gemini_key(verbose=False):
    # 0) An inline key pasted into this file wins, if it looks real.
    if INLINE_GEMINI_KEY and INLINE_GEMINI_KEY.strip() and "your key" not in INLINE_GEMINI_KEY.lower():
        key = _clean_key(INLINE_GEMINI_KEY)
        if key:
            if verbose:
                print("NEXUS AI: Gemini key loaded from INLINE_GEMINI_KEY ->", _mask_key(key))
            return key

    # Prefer a project key file over a possibly-stale environment variable.
    here = os.path.dirname(os.path.abspath(__file__))
    folders = [here, os.getcwd(), os.path.join(here, "NEXUS")]
    # Windows hides extensions, so "gemini_key.txt" often becomes
    # gemini_key.txt.txt; accept the usual variants.
    names = ["gemini_key.txt", "gemini_key.txt.txt", "gemini_key",
             "GEMINI_API_KEY.txt", "api_key.txt"]

    checked = []
    for folder in folders:
        for name in names:
            path = os.path.join(folder, name)
            checked.append(path)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        key = _clean_key(f.read())
                except Exception as exc:
                    if verbose:
                        print("NEXUS AI: could not read", path, "-", exc)
                    continue
                if key:
                    if verbose:
                        print("NEXUS AI: Gemini key loaded from", path, "->", _mask_key(key))
                        if not key.startswith("AIza"):
                            print("NEXUS AI: WARNING - a Google AI Studio key normally"
                                  " starts with 'AIza'. This key may be wrong.")
                    return key
                elif verbose:
                    print("NEXUS AI:", path, "is empty")

    env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env and env.strip():
        if verbose:
            print("NEXUS AI: Gemini key loaded from environment variable")
        return _clean_key(env)

    if verbose:
        print("NEXUS AI: NO GEMINI KEY FOUND. Looked for a key file here:")
        for path in checked:
            print("   ", path)
        print("Create 'gemini_key.txt' next to this script with your key inside"
              " (make sure it is not named gemini_key.txt.txt).")
    return None


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        key = get_gemini_key(verbose=True)
        if not key:
            raise RuntimeError("NO_GEMINI_KEY")
        # Give slow networks more time before "not responding". Fall back if
        # this google-genai version doesn't accept http_options.
        try:
            _gemini_client = genai.Client(
                api_key=key,
                http_options=genai_types.HttpOptions(timeout=30000),
            )
        except Exception:
            _gemini_client = genai.Client(api_key=key)
    return _gemini_client


def _is_transient_error(error):
    """Temporary problem worth a quick retry (overloaded, timeout, 5xx)."""
    text = str(error).lower()
    code = getattr(error, "code", None)
    if isinstance(code, int) and code >= 500:
        return True
    return any(w in text for w in (
        "overloaded", "unavailable", "try again", "timeout", "timed out",
        "deadline", "temporarily", "503", "502", "500", "connection reset"))


def discover_gemini_models(client):
    """Model ids this key can actually use for generateContent (flash first)."""
    try:
        names = []
        for model in client.models.list():
            name = (getattr(model, "name", "") or "").split("/")[-1]
            if not name:
                continue
            methods = (getattr(model, "supported_actions", None)
                       or getattr(model, "supported_generation_methods", None) or [])
            if methods and not any("generatecontent" in str(m).lower() for m in methods):
                continue
            names.append(name)
        flash = [n for n in names if "flash" in n.lower()]
        pro = [n for n in names if "pro" in n.lower() and n not in flash]
        rest = [n for n in names if n not in flash and n not in pro]
        return flash + pro + rest
    except Exception as exc:
        print("NEXUS AI: could not list Gemini models -", exc)
        return []


def ask_gemini(prompt):
    """Try configured Gemini model, then known fallbacks if it fails."""
    global _gemini_model
    client = get_gemini_client()
    config = genai_types.GenerateContentConfig(
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        temperature=0.4,
    )

    # A forced/cached model can become unavailable. Try it first, then the
    # known list, then whatever models this key actually exposes.
    models = []
    if _gemini_model:
        models.append(_gemini_model)
    models.extend(model for model in GEMINI_MODELS if model not in models)
    if not _gemini_model:
        for name in discover_gemini_models(client):
            if name not in models:
                models.append(name)
    last_error = None

    for model in models:
        # Retry a model up to 3 times when the server is momentarily
        # overloaded or slow ("not responding / try again later").
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                answer = (getattr(response, "text", None) or "").strip()
                if not answer:
                    last_error = RuntimeError("Gemini returned an empty response")
                    break
                _gemini_model = model
                print("NEXUS AI: using Gemini model", model)
                return answer
            except Exception as error:
                last_error = error
                print("NEXUS AI model failed:", model, type(error).__name__, error)
                if _is_transient_error(error) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break   # move on to the next model

    _gemini_model = None
    if last_error:
        raise last_error
    raise RuntimeError("No Gemini models are configured")

CURRENT_FOLDER = os.path.dirname(
    os.path.abspath(__file__)
)

# Every NEXUS_KNOWLEDGE*.txt file is loaded, in name order, e.g.
#   NEXUS_KNOWLEDGE.txt           (modules 1-15)
#   NEXUS_KNOWLEDGE_ADVANCED.txt  (modules 16-30)
# from this folder, or from a NEXUS subfolder if none are here.

import glob
import sys
from pathlib import Path


def load_nexus_knowledge():
    for folder in (CURRENT_FOLDER, os.path.join(CURRENT_FOLDER, "NEXUS")):
        paths = sorted(glob.glob(os.path.join(folder, "NEXUS_KNOWLEDGE*.txt")))
        if not paths:
            continue

        parts = []
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
                print("NEXUS KNOWLEDGE LOADED:", os.path.basename(path))
            except Exception as error:
                print("NEXUS KNOWLEDGE ERROR:", os.path.basename(path), error)

        if parts:
            return "\n\n".join(parts)

    print("NEXUS KNOWLEDGE: no NEXUS_KNOWLEDGE*.txt file found")
    return "FORTRESS Knowledge Core is currently unavailable."


NEXUS_KNOWLEDGE = load_nexus_knowledge()

# Report the Gemini key situation at startup so a missing key is obvious.
# NEXUS still runs without a key: it falls back to built-in offline answers.
if get_gemini_key(verbose=True) is None:
    print("NEXUS AI: no Gemini key - NEXUS will use built-in OFFLINE answers."
          " Add gemini_key.txt (or set INLINE_GEMINI_KEY) for full AI.")


def offline_nexus_answer(message):
    """Answer from live FORTRESS state when Gemini is unavailable, so NEXUS
    never goes silent. Handles status and per-layer questions; keeps it short."""
    msg = (message or "").lower()

    face = state.get("FACE VERIFICATION", "LOCKED")
    palm = state.get("PALM VERIFICATION", "LOCKED")
    pin = state.get("PHONE PIN", "LOCKED")
    card = state.get("CARD VERIFICATION", "LOCKED")
    vault = state.get("VAULT", "LOCKED")
    done = {"PASSED", "VERIFIED"}

    if any(w in msg for w in ("vault", "unlock", "access", "authorise", "authorize", "open")):
        if vault == "AUTHORIZED":
            return "The vault is authorized. All verification layers passed."
        return "The vault is locked. All verification layers must pass before access is authorized."

    if any(w in msg for w in ("status", "state", "next", "which", "stage", "left", "remaining", "pending", "progress")):
        order = [("face", face), ("palm", palm), ("phone PIN", pin), ("card", card)]
        pending = [name for name, s in order if s not in done]
        finished = [name for name, s in order if s in done]
        if not pending:
            tail = "Vault authorized." if vault == "AUTHORIZED" else "Awaiting final authorization."
            return "All four layers are verified. " + tail
        return f"Completed: {', '.join(finished) if finished else 'none'}. Next step: {pending[0]} verification."

    if "face" in msg:
        return f"Face verification is {face}."
    if "palm" in msg:
        return f"Palm verification is {palm}."
    if "pin" in msg or "password" in msg:
        return f"Phone PIN is {pin}."
    if "card" in msg or "qr" in msg:
        return f"Card verification is {card}."

    if any(w in msg for w in ("who are you", "what are you", "your name", "about nexus")):
        return "I am NEXUS, the intelligence layer of the FORTRESS security system."
    if "help" in msg or "what can you" in msg:
        return ("I monitor FORTRESS: face, palm, phone PIN and card verification, and the"
                " vault. Ask me about any layer or the current status.")
    if any(w in msg for w in ("hi", "hello", "hey", "good morning", "good evening", "good afternoon")):
        return "NEXUS online. FORTRESS security intelligence system active."

    return ("NEXUS is in offline mode. I can report FORTRESS status and the verification"
            " layers. Add a Grok key for full conversational answers.")


# ============================================================
# NEXUS AI — GROK (xAI)
#
# Grok's API is OpenAI-style, so only `requests` is needed. It requires a
# PAID xAI key (xai-...) with credit from https://console.x.ai .
# Put the key in grok_key.txt next to this script, or set XAI_API_KEY,
# or paste it into INLINE_GROK_KEY below. If Grok is unavailable, NEXUS
# falls back to the built-in offline answers.
# ============================================================

GROK_API_URL = (os.environ.get("XAI_API_URL") or "https://api.x.ai/v1").rstrip("/")
INLINE_GROK_KEY = ""
GROK_MODELS = [
    "grok-2-latest", "grok-2-1212", "grok-3", "grok-3-mini", "grok-beta", "grok-4",
]
GROK_MAX_TOKENS = 300
_grok_model = os.environ.get("GROK_MODEL") or None


def _extract_grok_key(raw):
    match = re.search(r"xai-[A-Za-z0-9\-_]{20,}", raw)
    if match:
        return match.group(0)
    for line in raw.splitlines():
        if line.strip():
            key = line.strip().strip("\"'")
            if key.upper().startswith(("XAI_API_KEY=", "GROK_API_KEY=")):
                key = key.split("=", 1)[1].strip().strip("\"'")
            return key
    return ""


def get_grok_key(verbose=False):
    if INLINE_GROK_KEY and INLINE_GROK_KEY.strip() and "your key" not in INLINE_GROK_KEY.lower():
        key = _extract_grok_key(INLINE_GROK_KEY)
        if key:
            if verbose:
                print("NEXUS AI: Grok key loaded from INLINE_GROK_KEY ->", _mask_key(key))
            return key

    here = os.path.dirname(os.path.abspath(__file__))
    folders = [here, os.getcwd(), os.path.join(here, "NEXUS")]
    names = ["grok_key.txt", "grok_key.txt.txt", "grok_key",
             "xai_key.txt", "XAI_API_KEY.txt"]
    checked = []
    for folder in folders:
        for name in names:
            path = os.path.join(folder, name)
            checked.append(path)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        key = _extract_grok_key(f.read())
                except Exception as exc:
                    if verbose:
                        print("NEXUS AI: could not read", path, "-", exc)
                    continue
                if key:
                    if verbose:
                        print("NEXUS AI: Grok key loaded from", path, "->", _mask_key(key))
                        if not key.startswith("xai-"):
                            print("NEXUS AI: WARNING - an xAI key normally starts with 'xai-'.")
                    return key

    env = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    if env and env.strip():
        if verbose:
            print("NEXUS AI: Grok key loaded from environment variable")
        return _extract_grok_key(env)

    if verbose:
        print("NEXUS AI: NO GROK KEY FOUND. Looked for a key file here:")
        for path in checked:
            print("   ", path)
        print("Create a key at https://console.x.ai and put it in grok_key.txt"
              " next to this script.")
    return None


def discover_grok_models(key):
    try:
        response = requests.get(
            GROK_API_URL + "/models",
            headers={"Authorization": "Bearer " + key},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("data", data.get("models", []))
        return [row.get("id") or row.get("name") for row in rows if (row.get("id") or row.get("name"))]
    except Exception as exc:
        print("NEXUS AI: could not list Grok models -", exc)
        return []


_grok_announced = False


def ask_grok(prompt):
    """Answer with Grok (xAI). Tries the model list, then the key's own models."""
    global _grok_model, _grok_announced
    key = get_grok_key()
    if not key:
        raise RuntimeError("NO_GROK_KEY")

    if _grok_model:
        models = [_grok_model]
    else:
        models = list(GROK_MODELS)
        for name in discover_grok_models(key):
            if name not in models:
                models.append(name)

    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    last_error = None
    for model in models:
        for attempt in range(3):
            try:
                response = requests.post(
                    GROK_API_URL + "/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": GROK_MAX_TOKENS,
                        "temperature": 0.4,
                    },
                    timeout=60,
                )
            except Exception as error:
                last_error = error
                print("NEXUS AI Grok request failed:", model, type(error).__name__, error)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

            if response.status_code in (401, 403):
                raise RuntimeError("GROK_KEY_REJECTED: " + response.text[:150])
            if response.status_code == 429:
                last_error = RuntimeError("GROK_RATE_LIMIT")
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break
            if response.status_code == 404 or (
                response.status_code == 400 and "model" in response.text.lower()
            ):
                last_error = RuntimeError(f"model {model} not available")
                break   # try the next model
            if response.status_code >= 500:
                last_error = RuntimeError(f"Grok server error {response.status_code}")
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

            response.raise_for_status()
            answer = (response.json()["choices"][0]["message"]["content"] or "").strip()
            if not _grok_model:
                _grok_model = model
                _grok_announced = True
                print("NEXUS AI: using Grok model", model)
            return answer

    raise last_error if last_error else RuntimeError("no Grok model available")


def describe_grok_error(error):
    text = str(error).lower()
    if str(error) == "NO_GROK_KEY":
        return "no Grok key. Put your xai-... key in grok_key.txt next to this script."
    if "grok_key_rejected" in text or "invalid api key" in text or "unauthorized" in text:
        return "the Grok (xAI) key was rejected. Check grok_key.txt / your xAI billing."
    if "grok_rate_limit" in text or "429" in text or "rate limit" in text:
        return "Grok rate limit or no credit. Check your xAI account balance."
    if any(w in text for w in ("connect", "timed out", "timeout", "getaddrinfo", "network")):
        return "no internet connection to Grok."
    if "no grok model" in text or "model" in text and "not available" in text:
        return "no Grok model is available for this key."
    return type(error).__name__ + ": " + str(error)[:90]


# Report the Grok key situation at startup.
if get_grok_key(verbose=True) is None:
    print("NEXUS AI: no Grok key - NEXUS will use built-in OFFLINE answers."
          " Add grok_key.txt (xai-... key) for full AI.")


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
# EMBEDDED PALM SCANNER (palmprint, MediaPipe hand location)
# Enroll with:  python FORTRESS_NEXUS.py enroll
# ============================================================

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


PALM_MIN_AREA = 0.04      # largest skin blob must be >= this share of the frame (fallback)

# The hand is located with MediaPipe so only the hand is scanned, never the
# face or a warm-coloured wall. hand_landmarker.task is looked up here.
HAND_MODEL_DIRS = [
    SCRIPT_DIR,
    DATA_DIR,
    Path.home() / "Downloads" / "NEXUS",
    Path.home() / "Downloads",
    Path.home() / "Desktop" / "NEXUS",
    Path.home() / "Documents" / "NEXUS",
]

_hand_landmarker = None
_hand_landmarker_tried = False


def get_hand_landmarker():
    """Lazy MediaPipe hand detector, or None if unavailable."""
    global _hand_landmarker, _hand_landmarker_tried
    if _hand_landmarker_tried:
        return _hand_landmarker
    _hand_landmarker_tried = True
    try:
        import mediapipe as mp
        model = next((d / "hand_landmarker.task" for d in HAND_MODEL_DIRS
                      if (d / "hand_landmarker.task").is_file()), None)
        if model is None:
            print("Palm: hand_landmarker.task not found; using colour fallback.")
            return None
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
        )
        _hand_landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        print("Palm: hand detector ready (", model.name, ")")
    except Exception as exc:
        print("Palm: hand detector unavailable (", exc, "); using colour fallback.")
        _hand_landmarker = None
    return _hand_landmarker


def locate_hand_mediapipe(frame):
    """Square region around the detected hand, or None. Only the hand."""
    landmarker = get_hand_landmarker()
    if landmarker is None:
        return None
    try:
        import mediapipe as mp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    except Exception:
        return None
    if not result.hand_landmarks:
        return None

    h, w = frame.shape[:2]
    xs = [p.x * w for p in result.hand_landmarks[0]]
    ys = [p.y * h for p in result.hand_landmarks[0]]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    side = max(max(xs) - min(xs), max(ys) - min(ys)) * 1.25
    x1 = int(max(0, cx - side / 2.0))
    y1 = int(max(0, cy - side / 2.0))
    x2 = int(min(w, cx + side / 2.0))
    y2 = int(min(h, cy + side / 2.0))
    if x2 - x1 < 60 or y2 - y1 < 60:
        return None
    return x1, y1, x2, y2


def locate_palm_skin(frame):
    """Fallback: largest skin blob. Used only when the hand detector is
    unavailable; weak when the background or face is skin-coloured."""
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


def locate_palm(frame):
    """(rect, confirmed_hand). The hand detector finds only the hand; the
    colour fallback is used only if the detector is unavailable."""
    rect = locate_hand_mediapipe(frame)
    if rect is not None:
        return rect, True
    return locate_palm_skin(frame), False


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
    rect, confirmed = locate_palm(frame)
    if rect is None:
        return (None, None, "SHOW YOUR PALM TO THE CAMERA",
                {"fill": 0.0, "brightness": 0.0, "sharpness": 0.0, "skin": False, "rect": None})

    x1, y1, x2, y2 = rect
    crop = cv2.resize(frame[y1:y2, x1:x2], (ROI_SIZE, ROI_SIZE), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    mask, skin_like = palm_mask(crop)
    # The hand detector already confirmed this is a hand, so don't second-guess
    # it on skin colour (lighting/white balance can fool the colour test).
    if confirmed:
        skin_like = True
        if mask is None or int(np.count_nonzero(mask)) < 0.15 * ROI_SIZE * ROI_SIZE:
            mask = np.full((ROI_SIZE, ROI_SIZE), 255, np.uint8)
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
    elif not confirmed and fill < MIN_FILL:
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
    # Retry: right after the face scan the webcam can still be held for a
    # moment, so a single open would fail and the palm step would "fail"
    # before its window ever appeared.
    for attempt in range(4):
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            cap = cv2.VideoCapture(CAMERA_INDEX)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            return cap
        cap.release()
        time.sleep(0.4)
    return cap


# ------------------------------------------------------------------
# Enroll
# ------------------------------------------------------------------

PCYAN = (255, 217, 32)
PGREEN = (120, 230, 40)
PORANGE = (0, 150, 255)
PRED = (70, 70, 255)


def enroll():
    print("ENROLL (manual): hold the SAME hand, palm facing the camera, fingers")
    print("together, held up to the camera. When the status says READY, press SPACE.")
    print("Move the hand slightly between scans. R = retake last scan, ESC = cancel.")
    cap = open_camera()
    if not cap.isOpened():
        print("Could not open webcam.")
        return False

    grays, masks = [], []
    message, message_color, message_until = "", PCYAN, 0.0
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
                status, color = problem, PORANGE
            else:
                status, color = f"READY - PRESS SPACE FOR SCAN {len(grays) + 1}", PGREEN

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
                message, message_color, message_until = f"SCAN {len(grays) + 1} REMOVED", PORANGE, now + 1.2
                print(f"  scan {len(grays) + 1} removed")
                continue

            forced = key in (ord("f"), ord("F"))
            if key != 32 and not forced:   # SPACE or F
                continue

            if problem and not forced:
                message, message_color, message_until = (
                    "NOT CAPTURED: " + problem + " (F = force)", PRED, now + 1.5)
                continue
            if forced and (not readings["skin"] or readings["fill"] < FORCE_MIN_FILL):
                message, message_color, message_until = (
                    "NOT CAPTURED: NO PALM DETECTED", PRED, now + 1.5)
                continue

            # Each new scan must line up with the first one.
            if grays:
                first = prepare_enrolled(grays[:1], masks[:1])[0]
                kp, desc = features(gray, mask)
                aligned, d, how = compare((gray, mask, kp, desc), first)
                if not aligned:
                    message, message_color, message_until = (
                        "NOT CAPTURED: COULDN'T LINE UP WITH SCAN 1 - SAME HAND, SAME DISTANCE", PRED, now + 2.0)
                    print("  rejected: could not align with scan 1")
                    continue
                if d > ENROLL_MAX_DISTANCE:
                    message, message_color, message_until = (
                        f"NOT CAPTURED: LINES DIFFER FROM SCAN 1 ({d:.2f}, need <= {ENROLL_MAX_DISTANCE:.2f})",
                        PRED, now + 2.0)
                    print(f"  rejected: line distance {d:.2f} vs scan 1 (aligned {how})")
                    continue
                print(f"  vs scan 1: line distance {d:.2f} (aligned {how})")

            grays.append(gray)
            masks.append(mask)
            message, message_color, message_until = f"SCAN {len(grays)} CAPTURED", PGREEN, now + 1.0
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
        return f"ERROR: no palm enrollment at {ENROLLMENT_PATH} - run: python FORTRESS_NEXUS.py enroll"

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
    status, color = "SHOW YOUR PALM TO THE CAMERA", PCYAN
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
                    status, color = problem, PORANGE
                else:
                    kp, desc = features(gray, mask)
                    scores = [compare((gray, mask, kp, desc), ref) for ref in enrolled]
                    agreeing = sum(1 for aligned, d, _ in scores
                                   if aligned and d <= cal["distance_limit"])
                    streak = streak + 1 if agreeing >= required else 0
                    if streak >= REQUIRED_CONSECUTIVE:
                        status, color = "PALM VERIFIED", PGREEN
                    elif streak:
                        status, color = f"SCANNING {streak}/{REQUIRED_CONSECUTIVE}", PCYAN
                    else:
                        status, color = "NO MATCH", PRED
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
                draw(frame, "FORTRESS PALM SCAN", "PALM NOT RECOGNISED - ACCESS DENIED", PRED, lines, rect=rect)
                cv2.imshow(WINDOW, frame)
                cv2.waitKey(1500)
                return "MISMATCH"

            cv2.imshow(WINDOW, frame)
            if (cv2.waitKey(1) & 0xFF) in (27, ord("q"), ord("Q")):
                return "CANCELLED"
    finally:
        cap.release()
        cv2.destroyAllWindows()


# ============================================================
# PALM VERIFICATION — laptop webcam palmprint scan (embedded above)
# Enrollment file: fortress_palm_scan_enrollment.npz (next to this file).
# Enroll with:  python FORTRESS_NEXUS.py enroll
# ============================================================

# A palm that is shown but does not match for this long (in total)
# is denied and counted as a failed attempt.
PALM_MISMATCH_SECONDS = 8


def run_palm_verification():
    """
    Run the embedded palmprint scan on the laptop webcam.
    Returns "VERIFIED", "MISMATCH" (wrong palm), "CANCELLED" (ESC/Q pressed)
    or "ERROR: <reason>".
    """
    try:
        return scan_palm(mismatch_seconds=PALM_MISMATCH_SECONDS)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


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


def describe_ai_error(error):
    """Short, plain reason for a Gemini failure, shown in the NEXUS chat."""
    text = str(error).lower()
    code = getattr(error, "code", None)

    if str(error) == "NO_GEMINI_KEY":
        return "no Gemini API key. Put it in gemini_key.txt next to the HUD file."
    if "api key not valid" in text or "api_key_invalid" in text or code == 401:
        return "the Gemini API key was rejected. Check gemini_key.txt."
    if code == 403:
        return "this Gemini API key is not allowed to use the model."
    if code == 429:
        return "Gemini quota used up. Wait a minute or check the API plan."
    if code == 404:
        return "no Gemini model in the list is available for this key."
    if code is not None and code >= 500:
        return "Gemini is having problems right now. Try again shortly."
    if any(word in text for word in ("connect", "timed out", "timeout", "getaddrinfo", "network")):
        return "no internet connection to Gemini."

    return type(error).__name__ + ": " + str(error)[:80]


def _load_backup_email_settings():
    """Read optional KEY=VALUE settings from fortress_email.env beside this script."""
    settings = {
        "FORTRESS_GMAIL_SENDER": os.environ.get("FORTRESS_GMAIL_SENDER", "").strip(),
        "FORTRESS_GMAIL_APP_PASSWORD": os.environ.get("FORTRESS_GMAIL_APP_PASSWORD", "").strip(),
        "FORTRESS_BACKUP_RECIPIENT": os.environ.get("FORTRESS_BACKUP_RECIPIENT", "").strip(),
    }
    config_path = os.path.join(CURRENT_FOLDER, "fortress_email.env")
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8-sig") as config_file:
            for line in config_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip("\"'")
                if key in settings and value:
                    settings[key] = value
    return settings


def send_backup_email(backup_event):
    """Email an existing backup artifact; never treat a missing file as success."""
    settings = _load_backup_email_settings()
    sender = settings["FORTRESS_GMAIL_SENDER"]
    app_password = settings["FORTRESS_GMAIL_APP_PASSWORD"]
    recipient = settings["FORTRESS_BACKUP_RECIPIENT"]

    if not sender or not app_password or not recipient:
        raise RuntimeError(
            "Email is not configured. Set FORTRESS_GMAIL_SENDER, "
            "FORTRESS_GMAIL_APP_PASSWORD, and FORTRESS_BACKUP_RECIPIENT."
        )

    candidate = backup_event.get("path") or backup_event.get("file")
    if not candidate:
        raise RuntimeError("Backup event did not include a file path.")
    path = os.path.abspath(os.path.expanduser(str(candidate)))
    if not os.path.isfile(path):
        raise RuntimeError(f"Backup file does not exist: {path}")

    msg = EmailMessage()
    msg["Subject"] = f"FORTRESS security backup — {backup_event.get('trigger', 'Security event')}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        "FORTRESS created a security backup.\\n"
        f"Trigger: {backup_event.get('trigger', 'Security event')}\\n"
        f"Created: {datetime.now().isoformat(timespec='seconds')}\\n"
        f"Attached file: {os.path.basename(path)}\\n"
    )

    mime_type, _ = mimetypes.guess_type(path)
    main_type, sub_type = (mime_type or "application/octet-stream").split("/", 1)
    with open(path, "rb") as attachment:
        msg.add_attachment(
            attachment.read(),
            maintype=main_type,
            subtype=sub_type,
            filename=os.path.basename(path),
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(sender, app_password)
        smtp.send_message(msg)

    return recipient


def recycle_summary(backup_event):
    """One line on what happened to the original files."""
    moved = backup_event.get("recycled") or []
    failed = backup_event.get("recycle_failed") or []
    error = backup_event.get("recycle_error")

    if error:
        return "Originals left in place: " + error[:80]
    if not moved and not failed:
        return "protected_data was empty, nothing to move."

    text = f"{len(moved)} original item(s) moved to the Recycle Bin."
    if failed:
        text += f" Could not move: {', '.join(failed)[:60]}."
    return text


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

        # F3 opens the folder with the latest intrusion backup.
        self.latest_backup = None

        self.root.bind(
            "<F3>",
            self.open_latest_backup
        )

        # F4 opens the Recycle Bin, where moved originals can be restored.
        self.root.bind(
            "<F4>",
            self.open_recycle_bin
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
                    "AUTHORIZED",
                    "SENT"
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
            target=self.ask_nexus,
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

        # Try Gemini first; if it fails for any reason (no key, rejected,
        # overloaded, no internet), fall back to built-in offline answers so
        # NEXUS always responds during the demo.
        try:

            answer = ask_grok(
                prompt
            )

            if answer:
                return answer

        except Exception as error:

            print(
                "NEXUS Grok error:",
                type(error).__name__,
                error
            )

        return offline_nexus_answer(message)

    # ========================================================
    # NEXUS — TEXT CHAT
    # ========================================================

    def ask_nexus(
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

        self.root.after(0, self.palm_verification_done, result)

    def palm_verification_done(
        self,
        result
    ):

        self.palm_scan_running = False

        if result == "VERIFIED":
            if state.get("PALM VERIFICATION") != "PASSED":
                self.palm_pass()
            return

        # A technical problem (no enrollment, webcam busy) is not an intruder;
        # show the reason so it can be fixed, and let the user try again.
        if isinstance(result, str) and result.startswith("ERROR"):
            self.add_chat_message(
                "NEXUS",
                "Palm scan could not run: " + result[6:].lstrip(": ").strip()
                + " Fix it and tap the palm button again.",
                RED
            )

        if state.get("PALM VERIFICATION") != "FAILED":
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

    def _send_backup_email_worker(self, backup_event):
        try:
            recipient = send_backup_email(backup_event)
            result = {
                "kind": "email",
                "ok": True,
                "file": os.path.basename(str(backup_event.get("path") or backup_event.get("file") or "")),
                "to": recipient,
            }
        except Exception as error:
            result = {
                "kind": "email",
                "ok": False,
                "file": os.path.basename(str(backup_event.get("path") or backup_event.get("file") or "")),
                "error": str(error),
            }

        try:
            self.root.after(0, lambda item=result: self.on_backup_event(item))
        except tk.TclError:
            pass

    def on_backup_event(self, backup_event):
        """Handle backup status silently; never show a popup or expose a path in the HUD."""
        if backup_event.get("kind") == "email":
            if backup_event.get("ok"):
                events.append((datetime.now().strftime("%H:%M:%S"), "BACKUP EMAIL", "SENT"))
            else:
                # Keep the detailed failure in the terminal log only; do not show a popup.
                events.append((datetime.now().strftime("%H:%M:%S"), "BACKUP EMAIL", "FAILED"))
                print("FORTRESS backup email failed:", backup_event.get("error", "unknown error"))
            self.request_rebuild()
            return

        self.security_alert = backup_event
        ok = bool(backup_event.get("ok"))
        if ok:
            self.latest_backup = backup_event
            if not backup_event.get("email_started"):
                backup_event["email_started"] = True
                threading.Thread(
                    target=self._send_backup_email_worker,
                    args=(dict(backup_event),),
                    daemon=True,
                ).start()
        events.append((
            datetime.now().strftime("%H:%M:%S"),
            "SECURITY BACKUP",
            "CREATED" if ok else "FAILED"
        ))
        if not ok:
            print("FORTRESS backup creation failed:", backup_event.get("error", "controller reported failure"))
        # Intentionally no show_backup_alert(), no path display, and no backup chat message.
        self.request_rebuild()

    def open_latest_backup(
        self,
        event=None
    ):

        if self.latest_backup:
            self.open_backup(self.latest_backup)
        else:
            self.add_chat_message("NEXUS", "No intrusion backup has been made yet.", CYAN)

    def open_recycle_bin(
        self,
        event=None
    ):

        try:
            if os.name == "nt":
                subprocess.Popen("explorer shell:RecycleBinFolder")
            else:
                print("Recycle Bin can only be opened on Windows.")
        except Exception as error:
            print("Could not open Recycle Bin:", error)

    def open_backup(
        self,
        backup_event
    ):
        """Open File Explorer on the backup folder with the zip selected."""

        path = backup_event.get("path")
        folder = backup_event.get("folder")

        try:
            if os.name == "nt" and path and os.path.exists(path):
                subprocess.Popen(f'explorer /select,"{path}"')
            elif os.name == "nt" and folder:
                os.startfile(folder)
            else:
                print("Backup folder:", folder)
        except Exception as error:
            print("Could not open backup folder:", error)

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

    # Palm enrollment mode:  python FORTRESS_NEXUS.py enroll
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("enroll", "--enroll", "1"):
        enroll()
        sys.exit()

    root = tk.Tk()

    app = FortressHUD(
        root
    )

    root.mainloop()
