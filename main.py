import os
import io
import time
import urllib.parse
from flask import Flask, jsonify, request, send_file
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(_name_)

# ---------- Selenium / Chrome setup ----------
CHROME_USER_DATA_DIR = os.getenv("CHROME_USER_DATA_DIR", "/app/session")  # persisted if you mount storage
WHATSAPP_WEB_URL = "https://web.whatsapp.com/"

_driver = None
_wait = None
_status = "BOOTING"
_ready_at = None

def get_driver():
    global _driver, _wait, _status
    if _driver is not None:
        return _driver
    opts = Options()
    # Headless in containers
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(f"--user-data-dir={CHROME_USER_DATA_DIR}")  # keep session cookies
    opts.add_argument("--lang=en-US")
    try:
        _driver = webdriver.Chrome(options=opts)
        _wait = WebDriverWait(_driver, 30)
        _status = "BROWSER_READY"
        return _driver
    except WebDriverException as e:
        _status = "BROWSER_ERROR"
        raise e

def ensure_loaded_login_page():
    """Open WhatsApp Web and wait until either QR appears OR chats UI appears."""
    global _status, _ready_at
    d = get_driver()
    if d.current_url != WHATSAPP_WEB_URL:
        d.get(WHATSAPP_WEB_URL)
    try:
        # Wait until either left pane chats OR QR canvas shows up
        # Chats list (after login) commonly has role="grid"
        _wait.until(
            lambda drv: (
                len(drv.find_elements(By.CSS_SELECTOR, "[role='grid']")) > 0
            ) or (
                len(drv.find_elements(By.CSS_SELECTOR, "canvas, img[alt*='Scan']")) > 0
            )
        )
        # Check if logged in (chats grid exists)
        if len(d.find_elements(By.CSS_SELECTOR, "[role='grid']")) > 0:
            _status = "READY"
            if _ready_at is None:
                _ready_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        else:
            _status = "QR_REQUIRED"
    except TimeoutException:
        _status = "TIMEOUT_LOADING"

def take_qr_screenshot_bytes():
    """Return PNG bytes of the QR code area (fallback: full page)."""
    d = get_driver()
    ensure_loaded_login_page()
    # If already ready, no QR
    if _status == "READY":
        return None

    # Try to capture the QR canvas or container
    # WhatsApp frequently uses a <canvas> for QR
    elem = None
    for selector in ["canvas", "img[alt*='Scan']", "div[aria-label*='Scan'] canvas"]:
        found = d.find_elements(By.CSS_SELECTOR, selector)
        if found:
            elem = found[0]
            break

    png = None
    if elem:
        try:
            png = elem.screenshot_as_png
        except Exception:
            png = None

    # Fallback to full-page screenshot if element approach fails
    if png is None:
        png = d.get_screenshot_as_png()

    return png

def send_text(to_number: str, text: str):
    """
    Navigates to a prefill URL and sends the message.
    to_number: like 9198XXXXXXXX (no +)
    """
    d = get_driver()
    ensure_loaded_login_page()
    if _status != "READY":
        raise RuntimeError(f"Client not ready (status={_status}). Scan QR first at /qr.")

    encoded = urllib.parse.quote(text)
    url = f"https://web.whatsapp.com/send?phone={to_number}&text={encoded}"
    d.get(url)
    try:
        # Wait for message box to appear and the text to prefill
        _ = WebDriverWait(d, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[contenteditable='true']"))
        )
        # Send by pressing Enter on active element (reliably triggers send)
        # Focus the editable div and press Enter via JS
        d.execute_script("""
            const boxes = document.querySelectorAll("div[contenteditable='true']");
            if (boxes.length) boxes[boxes.length-1].focus();
        """)
        # Use Enter key via JS (dispatch key events)
        d.execute_script("""
            const evt = new KeyboardEvent('keydown', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true});
            document.activeElement.dispatchEvent(evt);
        """)
        # Small wait so WhatsApp can send
        time.sleep(2)
        return True
    except TimeoutException:
        return False


# ---------- Flask routes ----------
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": _status,
        "ready_at": _ready_at,
        "routes": ["/qr", "/status", "POST /send {to, message}"]
    })

@app.route("/status", methods=["GET"])
def status():
    try:
        ensure_loaded_login_page()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "status": _status}), 500
    return jsonify({"ok": True, "status": _status, "ready_at": _ready_at})

@app.route("/qr", methods=["GET"])
def qr():
    try:
        png = take_qr_screenshot_bytes()
        if png is None:
            # Already logged in
            return jsonify({"ok": True, "status": "READY", "message": "Already authenticated. No QR needed."})
        return send_file(io.BytesIO(png), mimetype="image/png")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "status": _status}), 500

@app.route("/send", methods=["POST"])
def send():
    payload = request.get_json(silent=True) or {}
    to = str(payload.get("to", "")).strip()
    message = str(payload.get("message", "")).strip()
    if not to or not message:
        return jsonify({"ok": False, "error": "Missing 'to' or 'message'"}), 400
    if not to.isdigit():
        return jsonify({"ok": False, "error": "Phone must be digits, e.g. 9198XXXXXXXX (no +)"}), 400
    try:
        ok = send_text(to, message)
        if not ok:
            return jsonify({"ok": False, "error": "Failed to locate chat or send message"}), 500
        return jsonify({"ok": True, "sent_to": to, "message": message})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if _name_ == "_main_":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
