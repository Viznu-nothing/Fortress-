from flask import Flask, jsonify, render_template_string, request
import cv2
import numpy as np
import json
import math
import re
import os
import time
import pickle
import threading
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

app = Flask(__name__)

# ============================================================
# FORTRESS PHONE CONTROLLER — PRE-ENROLLED FORTRESS CARDS
# Layer 1: FACE VERIFICATION
# Layer 2: PALM VERIFICATION
# Layer 3: PHONE PIN
# Layer 4: CARD VERIFICATION
# ============================================================

PASSWORD = "2580"
SECURITY_CODE = "2580"

state = {
    "face": "READY",
    "palm": "LOCKED",
    "password": "LOCKED",          # PHONE PIN state
    "security_code": "LOCKED",     # CARD VERIFICATION state
    "vault": "LOCKED",

    "face_verified": False,
    "palm_verified": False,
    "password_verified": False,
    "security_code_verified": False,
    "card_owner": "",
    "card_id": "",

    "message": "FACE VERIFICATION REQUIRED"
}


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>FORTRESS</title>

<style>

* {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

html,
body {

    margin: 0;
    padding: 0;

    min-height: 100%;

    background: #02070C;
    color: #EAF7FF;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body {
    min-height: 100vh;
}

.page {

    width: 100%;
    max-width: 430px;

    min-height: 100vh;

    margin: 0 auto;

    padding:
        0
        18px
        20px
        18px;

    display: flex;
    flex-direction: column;
}

.header {

    margin: 0 -18px;

    padding:
        22px
        24px
        20px
        24px;

    min-height: 92px;

    background: #02080F;

    border-bottom:
        1px solid #123848;

    display: flex;

    align-items: center;

    justify-content: space-between;
}

.brand-area {

    display: flex;

    align-items: center;

    gap: 12px;
}

.brand-icon {

    width: 52px;
    height: 52px;

    border:
        1px solid #20D9FF;

    border-radius: 15px;

    display: flex;

    align-items: center;
    justify-content: center;

    color: #5BAEFF;

    font-size: 20px;

    background: #06121A;

    box-shadow:
        0 0 24px
        rgba(32,217,255,0.14);
}

.brand {

    font-size: 25px;

    font-weight: 700;

    letter-spacing: 1px;

    color: #F4FAFF;
}

.system {

    text-align: right;
}

.system-title {

    color: #638391;

    font-size: 9px;

    letter-spacing: 1px;

    margin-bottom: 7px;
}

.system-online {

    color: #16E6A0;

    font-size: 9px;

    letter-spacing: 1px;
}

.system-online::before {

    content: "●";

    font-size: 10px;

    margin-right: 8px;
}

.hero {

    padding:
        32px
        0
        24px
        0;
}

.hero-title {

    margin: 0;

    font-size: 34px;

    line-height: 1.05;

    font-weight: 700;

    color: #F4FAFF;
}

.hero-subtitle {

    margin-top: 12px;

    color: #638391;

    font-size: 11px;

    letter-spacing: 2px;
}

.layer-list {

    display: flex;

    flex-direction: column;

    gap: 10px;
}

.layer {

    min-height: 92px;

    padding:
        0
        18px;

    border-radius: 18px;

    border:
        1px solid #10202B;

    background:
        linear-gradient(
            135deg,
            #06121A,
            #030B12
        );

    display: flex;

    align-items: center;

    justify-content: space-between;

    transition: 0.2s ease;
}

.layer-left {

    display: flex;

    align-items: center;

    gap: 12px;
}

.dot {

    width: 11px;
    height: 11px;

    border-radius: 50%;

    background: #0C3043;

    flex: 0 0 auto;
}

.layer-name {

    font-size: 11px;

    font-weight: 700;

    letter-spacing: 0.2px;
}

.layer-state {

    font-size: 9px;

    letter-spacing: 1px;
}

.layer.active {

    border-color: #248BFF;

    box-shadow:
        0 0 22px
        rgba(36,139,255,0.08);
}

.layer.active .dot {

    background: #248BFF;

    box-shadow:
        0 0 16px
        rgba(36,139,255,0.8);
}

.layer.active .layer-name,
.layer.active .layer-state {

    color: #EAF7FF;
}

.layer.active .layer-state {

    color: #55AFFF;
}

.layer.verified {

    border-color: #16E6A0;
}

.layer.verified .dot {

    background: #16E6A0;

    box-shadow:
        0 0 16px
        rgba(22,230,160,0.7);
}

.layer.verified .layer-name,
.layer.verified .layer-state {

    color: #16E6A0;
}

.layer.locked {

    opacity: 0.48;
}

.layer.failed {

    border-color: #FF5268;
}

.layer.failed .dot {

    background: #FF5268;
}

.layer.failed .layer-name,
.layer.failed .layer-state {

    color: #FF5268;
}


/* ============================================================
   PASSWORD PANEL
   ============================================================ */

.password-panel {

    display: none;

    margin-top: 12px;

    padding: 16px;

    border:
        1px solid #248BFF;

    border-radius: 18px;

    background: #06121A;
}

.password-title {

    color: #55AFFF;

    font-size: 11px;

    font-weight: 700;

    letter-spacing: 1px;

    margin-bottom: 18px;
}

.password-input {

    width: 100%;

    height: 48px;

    border-radius: 12px;

    border:
        1px solid #248BFF;

    background: #02070C;

    color: #F4FAFF;

    text-align: center;

    font-size: 20px;

    letter-spacing: 7px;

    outline: none;
}

.password-input:focus {

    box-shadow:
        0 0 20px
        rgba(36,139,255,0.16);
}

.verify-password {

    width: 100%;

    height: 46px;

    margin-top: 10px;

    border:
        1px solid #248BFF;

    border-radius: 15px;

    background: #071522;

    color: #55AFFF;

    font-size: 11px;

    font-weight: 700;

    letter-spacing: 1px;
}


/* ============================================================
   SECURITY CODE PANEL
   ============================================================ */

.security-panel {

    display: none;

    margin-top: 12px;

    padding: 16px;

    border:
        1px solid #248BFF;

    border-radius: 18px;

    background: #06121A;
}

.security-title {

    color: #55AFFF;

    font-size: 11px;

    font-weight: 700;

    letter-spacing: 1px;

    margin-bottom: 18px;
}

.security-input {

    width: 100%;

    height: 48px;

    border-radius: 12px;

    border:
        1px solid #248BFF;

    background: #02070C;

    color: #F4FAFF;

    text-align: center;

    font-size: 20px;

    letter-spacing: 7px;

    outline: none;
}

.security-input:focus {

    box-shadow:
        0 0 20px
        rgba(36,139,255,0.16);
}

.verify-security {

    width: 100%;

    height: 46px;

    margin-top: 10px;

    border:
        1px solid #248BFF;

    border-radius: 15px;

    background: #071522;

    color: #55AFFF;

    font-size: 11px;

    font-weight: 700;

    letter-spacing: 1px;
}


/* ============================================================
   ERROR
   ============================================================ */

.error {

    min-height: 20px;

    margin-top: 12px;

    color: #FF5268;

    text-align: center;

    font-size: 9px;

    letter-spacing: 1px;
}


/* ============================================================
   FACE BUTTON
   ============================================================ */

.face-button {

    width: 100%;

    height: 56px;

    margin-top: 22px;

    border:
        1px solid #248BFF;

    border-radius: 18px;

    background: #030D17;

    color: #55AFFF;

    font-size: 9px;

    font-weight: 700;

    letter-spacing: 1px;
}

.face-button:active,
.verify-password:active,
.verify-security:active {

    transform: scale(0.985);
}


/* ============================================================
   VAULT
   ============================================================ */

.vault {

    margin-top: 24px;

    border:
        1px solid #102A38;

    border-radius: 28px;

    background:
        linear-gradient(
            135deg,
            #07131C,
            #030A10
        );

    min-height: 92px;

    display: flex;

    align-items: center;

    justify-content: center;

    flex-direction: column;
}

.vault-title {

    color: #638391;

    font-size: 11px;

    letter-spacing: 2px;

    margin-bottom: 14px;
}

.vault-state {

    color: #FF5268;

    font-size: 21px;

    font-weight: 700;

    letter-spacing: 1px;
}

.vault-state.authorized {

    color: #16E6A0;
}

.footer {

    text-align: center;

    color: #173340;

    font-size: 9px;

    letter-spacing: 1px;

    padding-top: 16px;
}


/* ============================================================
   MOBILE
   ============================================================ */

@media (max-width: 600px) {

    .page {

        max-width: 430px;

        padding:
            0
            18px
            20px
            18px;
    }

    .header {

        margin: 0 -18px;

        padding:
            22px
            24px
            20px
            24px;

        min-height: 92px;
    }

    .brand-area {

        gap: 12px;
    }

    .brand-icon {

        width: 52px;
        height: 52px;

        border-radius: 15px;

        font-size: 20px;
    }

    .brand {

        font-size: 25px;

        letter-spacing: 3px;
    }

    .system-title {

        font-size: 8px;

        letter-spacing: 1.5px;
    }

    .system-online {

        font-size: 12px;
    }

    .hero {

        padding:
            32px
            0
            24px
            0;
    }

    .hero-title {

        font-size: 34px;
    }

    .hero-subtitle {

        margin-top: 12px;

        font-size: 10px;

        letter-spacing: 2px;
    }

    .layer-list {

        gap: 10px;
    }

    .layer {

        min-height: 82px;

        padding:
            0
            18px;

        border-radius: 18px;
    }

    .layer-left {

        gap: 12px;
    }

    .dot {

        width: 11px;
        height: 11px;
    }

    .layer-name {

        font-size: 14px;

        letter-spacing: 0.2px;
    }

    .layer-state {

        font-size: 9px;

        letter-spacing: 1px;
    }

    .password-panel {

        padding: 16px;

        border-radius: 18px;
    }

    .password-title {

        font-size: 11px;

        margin-bottom: 10px;
    }

    .password-input {

        height: 48px;

        font-size: 20px;

        letter-spacing: 7px;
    }

    .verify-password {

        height: 46px;

        margin-top: 10px;

        font-size: 10px;
    }

    .security-panel {

        padding: 16px;

        border-radius: 18px;
    }

    .security-title {

        font-size: 11px;

        margin-bottom: 10px;
    }

    .security-input {

        height: 48px;

        font-size: 20px;

        letter-spacing: 7px;
    }

    .verify-security {

        height: 46px;

        margin-top: 10px;

        font-size: 10px;
    }

    .face-button {

        height: 56px;

        margin-top: 22px;

        font-size: 11px;

        letter-spacing: 2px;
    }

    .vault {

        min-height: 92px;

        margin-top: 24px;

        border-radius: 18px;
    }

    .vault-title {

        font-size: 8px;

        letter-spacing: 2px;

        margin-bottom: 8px;
    }

    .vault-state {

        font-size: 21px;
    }

    .footer {

        padding-top: 16px;

        font-size: 7px;
    }
}

</style>

</head>


<body>

<div class="page">


    <!-- ======================================================
         HEADER
         ====================================================== -->

    <div class="header">

        <div class="brand-area">

            <div class="brand-icon">
                ◇
            </div>

            <div class="brand">
                FORTRESS
            </div>

        </div>


        <div class="system">

            <div class="system-title">
                SYSTEM
            </div>

            <div class="system-online">
                ONLINE
            </div>

        </div>

    </div>


    <!-- ======================================================
         HERO
         ====================================================== -->

    <div class="hero">

        <h1 class="hero-title">
            Secure Access
        </h1>

        <div class="hero-subtitle">
            FORTRESS CONTROLLER
        </div>

    </div>


    <!-- ======================================================
         LAYERS
         ====================================================== -->

    <div class="layer-list">


        <!-- LAYER 1 -->

        <div
            class="layer active"
            id="layer1">

            <div class="layer-left">

                <div class="dot"></div>

                <div class="layer-name">
                    FACE VERIFICATION
                </div>

            </div>

            <div
                class="layer-state"
                id="faceState">

                READY

            </div>

        </div>


        <!-- LAYER 2 -->

        <div
            class="layer locked"
            id="layer2">

            <div class="layer-left">

                <div class="dot"></div>

                <div class="layer-name">
                    PALM VERIFICATION
                </div>

            </div>

            <div
                class="layer-state"
                id="palmState">

                LOCKED

            </div>

        </div>


        <!-- LAYER 3 -->

        <div
            class="layer locked"
            id="layer3">

            <div class="layer-left">

                <div class="dot"></div>

                <div class="layer-name">
                    PHONE PIN
                </div>

            </div>

            <div
                class="layer-state"
                id="phonePinState">

                LOCKED

            </div>

        </div>


        <!-- LAYER 4 -->

        <div
            class="layer locked"
            id="layer4">

            <div class="layer-left">

                <div class="dot"></div>

                <div class="layer-name">
                    CARD VERIFICATION
                </div>

            </div>

            <div
                class="layer-state"
                id="cardState">

                LOCKED

            </div>

        </div>

    </div>


    <!-- ======================================================
         PASSWORD PANEL
         ====================================================== -->

    <div
        class="password-panel"
        id="passwordPanel">

        <div class="password-title">
            ENTER PHONE PIN
        </div>

        <input
            id="password"
            class="password-input"
            type="password"
            inputmode="numeric"
            maxlength="4"
            autocomplete="off"
        >

        <button
            class="verify-password"
            onclick="verifyPassword()">

            VERIFY PHONE PIN

        </button>

        <div
            class="error"
            id="passwordError">
        </div>

    </div>


    <!-- PALM VERIFICATION PANEL: request laptop webcam scan -->
    <div class="security-panel" id="palmPanel">
        <div class="security-title">SCAN PALM WITH LAPTOP WEBCAM</div>
        <button class="verify-security" type="button" id="verifyPalmButton" onclick="verifyPalm()">SCAN PALM ON LAPTOP</button>
        <div class="error" id="palmError"></div>
    </div>

    <!-- CARD VERIFICATION PANEL -->
    <div class="security-panel" id="securityPanel">
        <div class="security-title">SCAN FORTRESS CARD QR WITH LAPTOP WEBCAM</div>
        <button class="verify-security" type="button" id="verifyCardButton" onclick="startCardScan()">SCAN CARD ON LAPTOP</button>
        <div class="error" id="securityError"></div>
    </div>

    <!-- ======================================================
         FACE BUTTON
         ====================================================== -->

    <button
        class="face-button"
        id="faceButton"
        onclick="requestFaceVerification()">

        VERIFY FACE

    </button>


    <!-- ======================================================
         VAULT
         ====================================================== -->

    <div class="vault">

        <div class="vault-title">
            PHYSICAL VAULT
        </div>

        <div
            class="vault-state"
            id="vaultState">

            LOCKED

        </div>

    </div>


    <!-- ======================================================
         FOOTER
         ====================================================== -->

    <div class="footer">

        FORTRESS SECURITY CONTROLLER // NEXUS

    </div>


</div>


<script>


// ============================================================
// UPDATE UI — FACE -> PALM -> PHONE PIN -> CARD
// ============================================================
function updateUI(data) {
    const layer1 = document.getElementById("layer1");
    const layer2 = document.getElementById("layer2");
    const layer3 = document.getElementById("layer3");
    const layer4 = document.getElementById("layer4");

    const faceState = document.getElementById("faceState");
    const palmState = document.getElementById("palmState");
    const phonePinState = document.getElementById("phonePinState");
    const cardState = document.getElementById("cardState");

    const palmPanel = document.getElementById("palmPanel");
    const passwordPanel = document.getElementById("passwordPanel");
    const securityPanel = document.getElementById("securityPanel");
    const faceButton = document.getElementById("faceButton");

    palmPanel.style.display = "none";
    passwordPanel.style.display = "none";
    securityPanel.style.display = "none";

    if (data.face === "READY" || data.face === "FAILED") {
        layer1.className = data.face === "FAILED" ? "layer failed" : "layer active";
        faceState.textContent = data.face === "FAILED" ? "DENIED" : "READY";
        faceButton.style.display = "block";
        faceButton.disabled = false;
        faceButton.textContent = "VERIFY FACE";
        layer2.className = "layer locked"; palmState.textContent = "LOCKED";
        layer3.className = "layer locked"; phonePinState.textContent = "LOCKED";
        layer4.className = "layer locked"; cardState.textContent = "LOCKED";
    }

    if (data.face === "SCANNING") {
        layer1.className = "layer active";
        faceState.textContent = "SCANNING";
        faceButton.style.display = "block";
        faceButton.textContent = "SCANNING FACE...";
        faceButton.disabled = true;
    }

    if (data.face === "VERIFIED") {
        layer1.className = "layer verified";
        faceState.textContent = "VERIFIED";
        faceButton.style.display = "none";
    }

    if (data.palm === "ACTIVE") {
        layer2.className = "layer active";
        palmState.textContent = "READY";
        palmPanel.style.display = "block";
    } else if (data.palm === "SCANNING") {
        layer2.className = "layer active";
        palmState.textContent = "SCANNING";
        palmPanel.style.display = "block";
    } else if (data.palm === "VERIFIED") {
        layer2.className = "layer verified";
        palmState.textContent = "VERIFIED";
    } else if (data.palm === "FAILED") {
        layer2.className = "layer failed";
        palmState.textContent = "DENIED";
        palmPanel.style.display = "block";
    }

    if (data.password === "ACTIVE") {
        layer3.className = "layer active";
        phonePinState.textContent = "READY";
        passwordPanel.style.display = "block";
    } else if (data.password === "VERIFIED") {
        layer3.className = "layer verified";
        phonePinState.textContent = "VERIFIED";
    } else if (data.password === "FAILED") {
        layer3.className = "layer failed";
        phonePinState.textContent = "DENIED";
        passwordPanel.style.display = "block";
    }

    if (data.security_code === "ACTIVE") {
        layer4.className = "layer active";
        cardState.textContent = "READY";
        securityPanel.style.display = "block";
    } else if (data.security_code === "SCANNING") {
        layer4.className = "layer active";
        cardState.textContent = "SCANNING";
        securityPanel.style.display = "block";
    } else if (data.security_code === "VERIFIED") {
        layer4.className = "layer verified";
        cardState.textContent = "VERIFIED";
    } else if (data.security_code === "FAILED") {
        layer4.className = "layer failed";
        cardState.textContent = "DENIED";
        securityPanel.style.display = "block";
    }

    if (data.vault === "AUTHORIZED") {
        document.getElementById("vaultState").textContent = "ACCESS AUTHORIZED";
        document.getElementById("vaultState").className = "vault-state authorized";
    } else {
        document.getElementById("vaultState").textContent = "LOCKED";
        document.getElementById("vaultState").className = "vault-state";
    }
}

// ============================================================
// REFRESH STATE
// ============================================================

async function refreshState() {

    try {

        const response =
            await fetch("/state");

        const data =
            await response.json();

        updateUI(data);

    }

    catch (error) {

        console.log(
            "Controller connection error"
        );

    }

}


// ============================================================
// FACE REQUEST
// ============================================================

async function requestFaceVerification() {

    const button =
        document.getElementById("faceButton");


    button.disabled =
        true;

    button.textContent =
        "SCANNING FACE...";


    try {

        const response =
            await fetch(
                "/face_request",
                {
                    method: "POST"
                }
            );


        const data =
            await response.json();

        updateUI(data);

    }

    catch (error) {

        button.disabled =
            false;

        button.textContent =
            "VERIFY FACE";
    }

}


// ============================================================
// PHONE PIN
// ============================================================
async function verifyPassword() {
    const input = document.getElementById("password");
    const error = document.getElementById("passwordError");
    error.textContent = "";
    try {
        const response = await fetch("/password", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({password: input.value})
        });
        const data = await response.json();
        updateUI(data);
        if (data.password === "VERIFIED") input.value = "";
        else error.textContent = data.message || "PHONE PIN DENIED";
    } catch (error) {
        document.getElementById("passwordError").textContent = "CONTROLLER ERROR";
    }
}

// ============================================================
// LAPTOP-WEBCAM PALM SCAN — uses the user's palm_verify_test algorithm
// ============================================================
async function verifyPalm() {
    const error = document.getElementById("palmError");
    const button = document.getElementById("verifyPalmButton");
    error.textContent = "";
    button.disabled = true;
    button.textContent = "OPENING LAPTOP WEBCAM...";
    try {
        const response = await fetch("/palm_request", {method: "POST"});
        const data = await response.json();
        updateUI(data);
        if (!response.ok) error.textContent = data.message || "COULD NOT START PALM SCAN";
        else error.textContent = "Laptop webcam opened. Show your enrolled palm to the webcam; press Q to cancel.";
    } catch (e) {
        error.textContent = "CONTROLLER ERROR — CHECK LAPTOP SERVER";
    } finally {
        button.disabled = false;
        button.textContent = "SCAN PALM ON LAPTOP";
    }
}

// ============================================================
// LAPTOP-WEBCAM CARD SCAN — demo-only QR scanning animation
// ============================================================
async function startCardScan() {
    const error = document.getElementById("securityError");
    const button = document.getElementById("verifyCardButton");
    error.textContent = "";
    button.disabled = true;
    button.textContent = "OPENING LAPTOP WEBCAM...";
    try {
        const response = await fetch("/card_request", {method: "POST"});
        const data = await response.json();
        updateUI(data);
        if (!response.ok) error.textContent = data.message || "COULD NOT START CARD SCAN";
        else error.textContent = "Laptop webcam opened. Show the card QR to the webcam; press Q to cancel.";
    } catch (e) {
        error.textContent = "CONTROLLER ERROR — CHECK LAPTOP SERVER";
    } finally {
        button.disabled = false;
        button.textContent = "SCAN CARD ON LAPTOP";
    }
}

// ENTER key submits the phone PIN.
document.getElementById("password").addEventListener("keydown", function(event) {
    if (event.key === "Enter") verifyPassword();
});

// ============================================================
// AUTO REFRESH
// ============================================================

setInterval(
    refreshState,
    1000
);


refreshState();

</script>

</body>

</html>
"""


# ============================================================
# WEB PAGE
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML
    )


# ============================================================
# STATE
# ============================================================

@app.route("/state")
def get_state():

    return jsonify(
        state
    )


# ============================================================
# FACE REQUEST
# ============================================================

@app.route(
    "/face_request",
    methods=["POST"]
)
def face_request():
    """Ask the laptop HUD to run face verification on the laptop webcam.

    The HUD (FORTRESS_FACE_PHONE_INTEGRATED.py) polls /state, starts the
    scan when it sees SCANNING, and reports back via /face_verified or
    /face_denied.
    """
    state["face"] = "SCANNING"
    state["face_verified"] = False
    state["message"] = "FACE VERIFICATION STARTED — CHECK LAPTOP WEBCAM WINDOW"
    return jsonify(state)


# ============================================================
# FACE VERIFIED
# ============================================================

@app.route("/face_verified", methods=["POST"])
def face_verified():
    state.update({
        "face": "VERIFIED", "face_verified": True,
        "palm": "ACTIVE", "palm_verified": False,
        "password": "LOCKED", "password_verified": False,
        "security_code": "LOCKED", "security_code_verified": False,
        "card_owner": "", "card_id": "", "vault": "LOCKED",
        "message": "FACE VERIFIED // PALM SCAN REQUIRED"
    })
    return jsonify(state)


@app.route("/face_denied", methods=["POST"])
def face_denied():
    state.update({
        "face": "FAILED", "face_verified": False,
        "palm": "LOCKED", "palm_verified": False,
        "password": "LOCKED", "password_verified": False,
        "security_code": "LOCKED", "security_code_verified": False,
        "card_owner": "", "card_id": "", "vault": "LOCKED",
        "message": "ACCESS DENIED // FACE VERIFICATION FAILED"
    })
    return jsonify(state)


# ============================================================
# USER'S PALM VERIFICATION ALGORITHM — laptop webcam
# Uses hand_landmarker.task + face_data/enrolled_palm.pkl
# ============================================================
PALM_SCAN_LOCK = threading.Lock()
PALM_SCAN_THREAD = None
BASE_DIR = Path(__file__).resolve().parent


def run_user_palm_verification():
    """Run the uploaded palm_verify_test.py algorithm on the laptop webcam."""
    global PALM_SCAN_THREAD
    camera = None
    landmarker = None
    try:
        import mediapipe as mp
        model_path = BASE_DIR / "hand_landmarker.task"
        profile_path = BASE_DIR / "face_data" / "enrolled_palm.pkl"
        if not model_path.exists():
            state.update(palm="FAILED", palm_verified=False, message="PALM SCAN ERROR: hand_landmarker.task NOT FOUND")
            return
        if not profile_path.exists():
            state.update(palm="FAILED", palm_verified=False, message="PALM SCAN ERROR: face_data/enrolled_palm.pkl NOT FOUND")
            return
        with open(profile_path, "rb") as f:
            profile = pickle.load(f)
        enrolled_template = np.asarray(profile["template"], dtype=np.float32)
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.65,
            min_hand_presence_confidence=0.65,
            min_tracking_confidence=0.65,
        )
        landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        camera = cv2.VideoCapture(0)
        if not camera.isOpened():
            state.update(palm="FAILED", palm_verified=False, message="PALM SCAN ERROR: LAPTOP WEBCAM COULD NOT OPEN")
            return
        state.update(palm="SCANNING", palm_verified=False, message="PALM SCANNING ON LAPTOP WEBCAM")
        start_time = time.time()
        scores = []
        consecutive_matches = 0
        verified = False
        MATCH_THRESHOLD = 0.985
        REQUIRED_MATCHES = 8
        while True:
            ret, frame = camera.read()
            if not ret:
                state.update(palm="FAILED", palm_verified=False, message="PALM SCAN ERROR: CAMERA FRAME UNAVAILABLE")
                return
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int((time.time() - start_time) * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)
            status = "SEARCHING FOR PALM"
            score_text = "MATCH: --"
            if result.hand_landmarks:
                hand = result.hand_landmarks[0]
                points = np.asarray([[lm.x, lm.y, lm.z] for lm in hand], dtype=np.float32)
                points -= points[0].copy()
                scale = np.linalg.norm(points[9])
                if scale > 1e-6:
                    points /= scale
                    live_vector = points.flatten()
                    norm = np.linalg.norm(live_vector)
                    if norm > 1e-6:
                        live_vector = live_vector / norm
                        similarity = float(np.dot(enrolled_template, live_vector))
                        similarity = max(-1.0, min(1.0, similarity))
                        scores.append(similarity)
                        if len(scores) > 10: scores.pop(0)
                        average_score = float(np.mean(scores))
                        score_text = f"MATCH: {average_score:.4f}"
                        if average_score >= MATCH_THRESHOLD:
                            consecutive_matches += 1
                            status = f"PALM MATCHING {consecutive_matches}/{REQUIRED_MATCHES}"
                        else:
                            consecutive_matches = 0
                            status = "PALM DETECTED — NO MATCH"
                        if consecutive_matches >= REQUIRED_MATCHES:
                            verified = True
                            status = "PALM VERIFIED"
                h, w = frame.shape[:2]
                for lm in hand:
                    cv2.circle(frame, (int(lm.x*w), int(lm.y*h)), 4, (0,220,255), -1)
                connections = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),(13,17),(17,18),(18,19),(19,20),(0,17)]
                for a,b in connections:
                    cv2.line(frame, (int(hand[a].x*w),int(hand[a].y*h)), (int(hand[b].x*w),int(hand[b].y*h)), (0,180,255), 2)
            else:
                consecutive_matches = 0
                scores.clear()
                status = "NO PALM DETECTED"
            cv2.putText(frame, "FORTRESS PALM VERIFICATION", (20,35), cv2.FONT_HERSHEY_SIMPLEX, .8, (0,220,255), 2)
            cv2.putText(frame, status, (20,75), cv2.FONT_HERSHEY_SIMPLEX, .7, (255,255,255), 2)
            cv2.putText(frame, score_text, (20,110), cv2.FONT_HERSHEY_SIMPLEX, .65, (180,220,230), 2)
            cv2.putText(frame, "Press Q to exit", (20,145), cv2.FONT_HERSHEY_SIMPLEX, .55, (150,180,190), 2)
            cv2.imshow("FORTRESS - Palm Verification", frame)
            if verified:
                state.update(palm="VERIFIED", palm_verified=True, password="ACTIVE", password_verified=False,
                             security_code="LOCKED", security_code_verified=False, vault="LOCKED",
                             message="PALM VERIFIED // PHONE PIN REQUIRED")
                cv2.waitKey(1200)
                return
            if cv2.waitKey(1) & 0xFF == ord("q"):
                state.update(palm="FAILED", palm_verified=False, password="LOCKED", security_code="LOCKED",
                             vault="LOCKED", message="PALM VERIFICATION CANCELLED")
                return
    except Exception as exc:
        state.update(palm="FAILED", palm_verified=False, message=f"PALM SCAN ERROR: {type(exc).__name__}: {exc}")
    finally:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()
        if landmarker is not None:
            landmarker.close()
        PALM_SCAN_LOCK.release()


@app.route("/palm_request", methods=["POST"])
def palm_request():
    global PALM_SCAN_THREAD
    if not state.get("face_verified"):
        state["message"] = "FACE VERIFICATION REQUIRED"
        return jsonify(state), 403
    if not PALM_SCAN_LOCK.acquire(blocking=False):
        state["message"] = "PALM SCAN ALREADY RUNNING ON LAPTOP"
        return jsonify(state), 409
    state.update(palm="SCANNING", palm_verified=False, message="OPENING LAPTOP WEBCAM FOR PALM VERIFICATION")
    PALM_SCAN_THREAD = threading.Thread(target=run_user_palm_verification, daemon=True)
    PALM_SCAN_THREAD.start()
    return jsonify(state), 202


def extract_palm_features(image):
    """Basic experimental texture descriptor; not a certified biometric."""
    if image is None or image.size == 0:
        raise ValueError("Invalid palm image.")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    h, w = gray.shape
    side = min(h, w)
    if side < 2:
        raise ValueError("Palm image is too small.")
    x, y = (w - side) // 2, (h - side) // 2
    gray = cv2.resize(gray[y:y + side, x:x + side], (256, 256))
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    values = []
    for angle in range(0, 180, 30):
        kernel = cv2.getGaborKernel((21, 21), 4.0, np.deg2rad(angle), 10.0, 0.5, 0, ktype=cv2.CV_32F)
        response = cv2.filter2D(gray.astype(np.float32), cv2.CV_32F, kernel)
        for row in range(4):
            for col in range(4):
                patch = response[row * 64:(row + 1) * 64, col * 64:(col + 1) * 64]
                values.extend([float(np.mean(patch)), float(np.std(patch))])
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


@app.route("/palm", methods=["POST"])
def verify_palm():
    if not state["face_verified"]:
        state["message"] = "FACE VERIFICATION REQUIRED"
        return jsonify(state), 403
    uploaded = request.files.get("palmphoto")
    if not uploaded or not uploaded.filename:
        state["palm"] = "FAILED"
        state["message"] = "NO PALM IMAGE RECEIVED"
        return jsonify(state), 400
    raw = uploaded.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        state["palm"] = "FAILED"
        state["message"] = "PALM IMAGE TOO LARGE"
        return jsonify(state), 413
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        state["palm"] = "FAILED"
        state["message"] = "INVALID PALM IMAGE"
        return jsonify(state), 400

    from pathlib import Path
    profile_path = Path(__file__).resolve().parent / "palm_data" / "uploaded_palms.json"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        templates = profile.get("templates", [])
    except FileNotFoundError:
        state["palm"] = "FAILED"
        state["message"] = "NO PALM TEMPLATES FOUND — ENROL PALM PHOTOS FIRST"
        return jsonify(state), 400
    except (OSError, json.JSONDecodeError, AttributeError):
        state["palm"] = "FAILED"
        state["message"] = "PALM TEMPLATE FILE IS INVALID"
        return jsonify(state), 500

    if not templates:
        state["palm"] = "FAILED"
        state["message"] = "NO ENROLLED PALM TEMPLATES"
        return jsonify(state), 400

    try:
        probe = extract_palm_features(image)
        scores = []
        for template in templates:
            stored = np.asarray(template.get("features", []), dtype=np.float32)
            if stored.shape == probe.shape:
                denominator = float(np.linalg.norm(stored) * np.linalg.norm(probe))
                if denominator > 0:
                    scores.append(float(np.dot(stored, probe) / denominator))
    except (ValueError, cv2.error) as exc:
        state["palm"] = "FAILED"
        state["message"] = f"PALM PROCESSING ERROR: {exc}"
        return jsonify(state), 400

    # Experimental threshold only; this is not validated biometric authentication.
    if scores and max(scores) >= 0.90:
        state["palm"] = "VERIFIED"
        state["palm_verified"] = True
        state["password"] = "ACTIVE"
        state["password_verified"] = False
        state["security_code"] = "LOCKED"
        state["security_code_verified"] = False
        state["vault"] = "LOCKED"
        state["message"] = "PALM POTENTIAL MATCH // PHONE PIN REQUIRED"
        return jsonify(state)

    state["palm"] = "FAILED"
    state["palm_verified"] = False
    state["password"] = "LOCKED"
    state["security_code"] = "LOCKED"
    state["vault"] = "LOCKED"
    state["message"] = "PALM DID NOT MATCH THE ENROLLED TEMPLATE"
    return jsonify(state), 401


@app.route("/password", methods=["POST"])
def verify_password():
    if not state["palm_verified"]:
        state["message"] = "PALM VERIFICATION REQUIRED"
        return jsonify(state), 403
    data = request.get_json(silent=True) or {}
    entered = str(data.get("password", ""))
    if entered == PASSWORD:
        state["password"] = "VERIFIED"
        state["password_verified"] = True
        state["security_code"] = "ACTIVE"
        state["security_code_verified"] = False
        state["vault"] = "LOCKED"
        state["message"] = "PHONE PIN VERIFIED // CARD SCAN REQUIRED"
        return jsonify(state)
    state["password"] = "FAILED"
    state["password_verified"] = False
    state["security_code"] = "LOCKED"
    state["security_code_verified"] = False
    state["vault"] = "LOCKED"
    state["message"] = "INVALID PHONE PIN"
    return jsonify(state), 401


# ============================================================
# RESET
# ============================================================

@app.route("/reset", methods=["POST"])
def reset():
    state.update({
        "face": "READY", "palm": "LOCKED", "password": "LOCKED",
        "security_code": "LOCKED", "vault": "LOCKED",
        "face_verified": False, "palm_verified": False,
        "password_verified": False, "security_code_verified": False,
        "message": "FACE VERIFICATION REQUIRED"
    })
    return jsonify(state)


# ============================================================
# QR CARD ENROLMENT (phone camera/photo upload)
# ============================================================

BASE_DIR = __import__("pathlib").Path(__file__).resolve().parent
CARD_DATABASE = BASE_DIR / "fortress_enrolled_cards.json"
CARD_ID_PATTERN = re.compile(r"^FRT-[A-Z][A-Z0-9]{2,15}$")
# If Tesseract is installed in a non-standard Windows location, uncomment and edit:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pending_card = {}


def load_card_database():
    if not CARD_DATABASE.exists():
        return {"cards": {}}
    try:
        data = json.loads(CARD_DATABASE.read_text(encoding="utf-8"))
        if not isinstance(data.get("cards"), dict):
            raise ValueError("Invalid cards database format.")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Cannot safely read card database: {exc}") from exc


def parse_fortress_qr(payload):
    # Current printed card format: FORTRESS | FRT-V001 | OWNER
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) != 3 or parts[0].upper() != "FORTRESS":
        return None
    card_id = parts[1].upper()
    owner = parts[2].strip()
    if not CARD_ID_PATTERN.fullmatch(card_id) or not owner:
        return None
    return card_id, owner


def read_printed_fortress_id(image):
    """Read the printed FRT-V001 / FRT-A001 text from the card image using OCR."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Try original, enlarged, and thresholded versions to improve OCR on card photos.
    variants = [gray]
    enlarged = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    variants.append(enlarged)
    variants.append(cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    for candidate in variants:
        try:
            text = pytesseract.image_to_string(candidate, config="--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        except Exception:
            return None, "OCR_ENGINE_UNAVAILABLE: install Tesseract OCR and pytesseract"
        # OCR can confuse separators; normalize common visual mistakes, then validate full ID.
        cleaned = text.upper().replace("_", "-").replace(" ", "")
        cleaned = cleaned.replace("FRT—", "FRT-").replace("FRT–", "FRT-")
        matches = re.findall(r"FRT[-_][A-Z0-9]{3,16}", cleaned)
        for candidate_id in matches:
            candidate_id = candidate_id.replace("_", "-")
            if CARD_ID_PATTERN.fullmatch(candidate_id):
                return candidate_id, None
    return None, None


# ============================================================
# CARD VERIFICATION — laptop webcam, demo-only QR scan animation
# The QR is NOT decoded or checked against any database. The scan
# always completes; a QR in view just makes the progress run faster.
# ============================================================
CARD_SCAN_LOCK = threading.Lock()
CARD_SCAN_THREAD = None
DEMO_CARD_OWNER = "VISHNU VARDHAN"
DEMO_CARD_ID = "FRT-V001"


def draw_card_scan_overlay(frame, progress, status, qr_points, now):
    h, w = frame.shape[:2]
    cyan, green, white = (255, 217, 32), (160, 230, 22), (255, 247, 234)

    # Dim everything outside the target box.
    box = int(min(w, h) * 0.62)
    x1, y1 = (w - box) // 2, (h - box) // 2
    x2, y2 = x1 + box, y1 + box
    shade = frame.copy()
    shade[:] = (shade * 0.35).astype(np.uint8)
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
    cv2.putText(frame, "FORTRESS CARD VERIFICATION", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cyan, 2)
    cv2.putText(frame, status, (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.65, white, 2)
    bar_x1, bar_x2, bar_y = 20, w - 20, h - 40
    cv2.rectangle(frame, (bar_x1, bar_y), (bar_x2, bar_y + 14), (72, 56, 18), 1)
    fill = bar_x1 + int((bar_x2 - bar_x1) * min(progress, 1.0))
    cv2.rectangle(frame, (bar_x1 + 2, bar_y + 2), (max(bar_x1 + 2, fill - 2), bar_y + 12), cyan, -1)
    cv2.putText(frame, f"{int(min(progress, 1.0) * 100)}%", (bar_x2 - 50, bar_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, white, 1)
    cv2.putText(frame, "Press Q to cancel", (20, h - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (190, 180, 150), 1)


def run_card_scan():
    """Show a QR scanning animation on the laptop webcam, then approve the demo card."""
    camera = None
    try:
        # Same camera opening as face_verify_test.py: DirectShow first on Windows.
        camera = cv2.VideoCapture(0, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(0)
        if not camera.isOpened():
            camera = cv2.VideoCapture(0)
        if not camera.isOpened():
            state.update(security_code="FAILED", security_code_verified=False,
                         message="CARD SCAN ERROR: LAPTOP WEBCAM COULD NOT OPEN")
            return
        state.update(security_code="SCANNING", security_code_verified=False,
                     message="CARD SCANNING ON LAPTOP WEBCAM")

        detector = cv2.QRCodeDetector()
        stages = [
            (0.00, "SEARCHING FOR FORTRESS CARD..."),
            (0.25, "QR PATTERN ACQUIRED"),
            (0.50, "DECODING SECURE PAYLOAD..."),
            (0.75, "MATCHING ENROLLED CARD DATABASE..."),
        ]
        progress = 0.0
        last = time.time()
        while True:
            ret, frame = camera.read()
            if not ret:
                state.update(security_code="FAILED", security_code_verified=False,
                             message="CARD SCAN ERROR: CAMERA FRAME UNAVAILABLE")
                return
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
                state.update(security_code="VERIFIED", security_code_verified=True,
                             card_owner=DEMO_CARD_OWNER, card_id=DEMO_CARD_ID, vault="AUTHORIZED",
                             message=f"CARD VERIFIED // {DEMO_CARD_OWNER}")
                end = time.time() + 1.8
                while time.time() < end:
                    ret, frame = camera.read()
                    if not ret:
                        break
                    draw_card_scan_overlay(frame, 1.0, "CARD VERIFIED", qr_points, time.time())
                    h, w = frame.shape[:2]
                    cv2.putText(frame, "ACCESS GRANTED", (w // 2 - 150, h // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (160, 230, 22), 3)
                    cv2.putText(frame, f"{DEMO_CARD_ID} // {DEMO_CARD_OWNER}", (w // 2 - 150, h // 2 + 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 247, 234), 2)
                    cv2.imshow("FORTRESS - Card Verification", frame)
                    cv2.waitKey(30)
                return

            draw_card_scan_overlay(frame, progress, status, qr_points, now)
            cv2.imshow("FORTRESS - Card Verification", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                state.update(security_code="FAILED", security_code_verified=False, vault="LOCKED",
                             message="CARD VERIFICATION CANCELLED")
                return
    except Exception as exc:
        state.update(security_code="FAILED", security_code_verified=False,
                     message=f"CARD SCAN ERROR: {type(exc).__name__}: {exc}")
    finally:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()
        CARD_SCAN_LOCK.release()


@app.route("/card_request", methods=["POST"])
def card_request():
    global CARD_SCAN_THREAD
    if not state.get("password_verified"):
        state["message"] = "PHONE PIN VERIFICATION REQUIRED"
        return jsonify(state), 403
    if not CARD_SCAN_LOCK.acquire(blocking=False):
        state["message"] = "CARD SCAN ALREADY RUNNING ON LAPTOP"
        return jsonify(state), 409
    state.update(security_code="SCANNING", security_code_verified=False,
                 message="OPENING LAPTOP WEBCAM FOR CARD VERIFICATION")
    CARD_SCAN_THREAD = threading.Thread(target=run_card_scan, daemon=True)
    CARD_SCAN_THREAD.start()
    return jsonify(state), 202


# QR enrolment routes intentionally removed.
# Cards must already exist in fortress_enrolled_cards.json.


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("=" * 60)

    print(
        "FORTRESS PHONE CONTROLLER"
    )

    print("=" * 60)

    print(
        "LAYER 1 : FACE VERIFICATION"
    )

    print(
        "LAYER 2 : PALM VERIFICATION"
    )

    print(
        "LAYER 3 : PHONE PIN"
    )

    print(
        "LAYER 4 : CARD VERIFICATION"
    )

    print("=" * 60)

    print("PHONE URL:")

    print(
        "Open http://<LAPTOP-LAN-IP>:5001 on the phone, using the laptop's Wi-Fi IP."
    )

    print("=" * 60)


    app.run(

        host="0.0.0.0",

        port=5001,

        debug=False

    )
