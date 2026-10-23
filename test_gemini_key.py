"""
FORTRESS — Gemini key tester.

Run this next to your gemini_key.txt:   python test_gemini_key.py

It calls Google's Gemini REST API directly (no google-genai needed) and
prints the EXACT reason a key is rejected, plus the models the key can use.
This pinpoints whether the problem is the key, the project, or the region.
"""

import os
import re
import json
import urllib.request
import urllib.error

BASE = "https://generativelanguage.googleapis.com/v1beta"


def find_key():
    # Same robust search as FORTRESS: several folders and filenames.
    here = os.path.dirname(os.path.abspath(__file__))
    folders = [here, os.getcwd(), os.path.join(here, "NEXUS")]
    names = ["gemini_key.txt", "gemini_key.txt.txt", "gemini_key",
             "GEMINI_API_KEY.txt", "api_key.txt"]
    for folder in folders:
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8-sig") as f:
                    raw = f.read()
                match = re.search(r"AIza[0-9A-Za-z_\-]{35}", raw)
                if match:
                    print("Key file:", path)
                    return match.group(0)
                print("Found", path, "but it has no AIza... key in it.")
    env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env and env.strip():
        print("Key from environment variable.")
        match = re.search(r"AIza[0-9A-Za-z_\-]{35}", env)
        return match.group(0) if match else env.strip()
    return None


def call(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"raw": body}
    except Exception as exc:
        return None, {"network_error": str(exc)}


def main():
    print("=" * 60)
    print("FORTRESS GEMINI KEY TESTER")
    print("=" * 60)

    key = find_key()
    if not key:
        print("\nNO KEY FOUND. Put your key in gemini_key.txt next to this file.")
        print("Get a free key at: https://aistudio.google.com/apikey")
        input("\nPress Enter to exit...")
        return

    masked = key[:6] + "..." + key[-4:] + f" ({len(key)} chars)"
    print("Testing key:", masked)
    if not key.startswith("AIza") or len(key) < 35:
        print("WARNING: this does not look like an AI Studio key (should start AIza, ~39 chars).")

    # 1) List models — the clearest check that the key + project are valid.
    print("\n[1] Listing models the key can use...")
    status, data = call(f"{BASE}/models?key={key}")
    if status == 200:
        usable = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                usable.append(m.get("name", "").split("/")[-1])
        print("  OK. Models for generateContent:", ", ".join(usable) or "(none!)")
        model = next((m for m in usable if "flash" in m), usable[0] if usable else None)
    else:
        print("  FAILED. Google says:")
        err = data.get("error", data)
        print("   status:", status)
        print("   message:", err.get("message", data))
        print("   reason :", (err.get("details") or err.get("status") or ""))
        _diagnose(status, json.dumps(data).lower())
        input("\nPress Enter to exit...")
        return

    # 2) Try an actual generateContent call.
    if model:
        print(f"\n[2] Sending a test question to {model}...")
        req = urllib.request.Request(
            f"{BASE}/models/{model}:generateContent?key={key}",
            data=json.dumps({"contents": [{"parts": [{"text": "Say OK."}]}]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            print("  SUCCESS. Gemini replied:", text.strip())
            print("\nYour key WORKS. Use this model in FORTRESS if needed:", model)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            print("  FAILED on generateContent. status:", exc.code)
            print("  ", body[:300])
            _diagnose(exc.code, body.lower())
        except Exception as exc:
            print("  Network error:", exc)

    input("\nPress Enter to exit...")


def _diagnose(status, text):
    print("\nDIAGNOSIS:")
    if "api key not valid" in text or "api_key_invalid" in text or status == 400:
        print("  The key value is wrong or incomplete. Create a fresh key at")
        print("  https://aistudio.google.com/apikey and copy it whole.")
    elif "permission" in text or "service_disabled" in text or "has not been used" in text or status == 403:
        print("  The key is real but the Generative Language API is not enabled for")
        print("  its Google project. Easiest fix: make the key at")
        print("  https://aistudio.google.com/apikey (it enables the API automatically).")
    elif "location is not supported" in text or "user location" in text:
        print("  Gemini is not available in your region for this key. Use a VPN to a")
        print("  supported region, or use the FORTRESS offline answers for the demo.")
    elif status == 429 or "quota" in text or "resource_exhausted" in text:
        print("  The free quota is used up. Wait a minute, or try fewer requests.")
    else:
        print("  Unrecognised error above. Send me the status and message.")


if __name__ == "__main__":
    main()
