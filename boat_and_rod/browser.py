#!/usr/bin/env python3
"""
browser.py - Browser automation for Reader View development.

Launches a dedicated Chrome instance and provides commands for navigation,
JS execution, screenshots, and pushing settings via Chrome DevTools Protocol.

Usage:
    python browser.py start             Launch automation Chrome
    python browser.py setup             Install Reader View extension (one-time)
    python browser.py stop              Stop Chrome
    python browser.py status            Show browser & extension info
    python browser.py open <url>        Navigate to URL
    python browser.py js <expression>   Execute JavaScript, print result
    python browser.py screenshot [file] Capture screenshot (default: screenshot.png)
    python browser.py click <selector>  Click element by CSS selector
    python browser.py push              Push CSS/JSON from my-settings/ to extension
    python browser.py import            Import all preferences from reader-view-preferences.json
    python browser.py wait [seconds]    Wait for page load (default: 2s)

Notes:
    Managed Chrome blocks --load-extension, so the extension must be installed
    from the Chrome Web Store once via 'setup'. It persists in the profile.
"""

import sys
import os
import json
import time
import base64
import subprocess
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

try:
    import websocket
except ImportError:
    print("Missing dependency: websocket-client")
    print("Install with: pip install websocket-client")
    sys.exit(1)

# --- Paths ---

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
SETTINGS_DIR = PROJECT_DIR / "my-settings"

STATE_DIR = Path.home() / ".reader-browser"
STATE_FILE = STATE_DIR / "state.json"
CHROME_DATA = STATE_DIR / "chrome-data"
EXT_ID_FILE = STATE_DIR / ".extension-id"

DEBUG_PORT = int(os.environ.get("BROWSER_PORT", 9333))

CWS_URL = "https://chromewebstore.google.com/detail/reader-view/ecabifbgmdmgdllomnfinbmaellmclnh"
CWS_EXT_ID = "ecabifbgmdmgdllomnfinbmaellmclnh"  # Known ID for CWS-installed Reader View

# Chrome built-in extension IDs — skip these during discovery
BUILTIN_EXT_IDS = {
    "ghbmnnjooekpmoecnnnilnnbdlolhkhi",  # Chrome Web Store
    "nmmhkkegccagdldgiimedpiccmgmieda",  # Chrome Payment Handler
    "nkeimhogjdpnpccoofpliimaahmaaome",  # Google Hangouts
    "fignfifoniblkonapihmkfakmlgkbkcf",  # Google TTS Engine
    "ahfgeienlihckogmohjhadlkjgocpleb",  # Web Store component
    "mhjfbmdgcfjbbpaeojofohoefgiehjai",  # Chrome PDF Viewer
}

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "Chrome not found. Set CHROME_BIN environment variable."
    )


# --- CDP helpers ---

def cdp_http(endpoint):
    """GET a CDP HTTP endpoint, return parsed JSON."""
    try:
        resp = urlopen(f"http://localhost:{DEBUG_PORT}{endpoint}", timeout=5)
        return json.loads(resp.read())
    except (URLError, json.JSONDecodeError):
        return None


def cdp_send(ws_url, method, params=None):
    """Send a single CDP command over WebSocket, return the response."""
    ws = websocket.create_connection(ws_url, timeout=10)
    try:
        msg = {"id": 1, "method": method}
        if params:
            msg["params"] = params
        ws.send(json.dumps(msg))
        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == 1:
                return resp
    finally:
        ws.close()


def get_browser_ws():
    """Get the browser-level WebSocket URL."""
    info = cdp_http("/json/version")
    return info["webSocketDebuggerUrl"] if info else None


def get_page_ws():
    """Get WebSocket URL for the first 'page' type target."""
    targets = cdp_http("/json") or []
    for t in targets:
        if t.get("type") == "page":
            return t.get("webSocketDebuggerUrl")
    return None


def get_all_targets():
    """List all targets from CDP."""
    return cdp_http("/json") or []


def save_state(pid):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"pid": pid, "port": DEBUG_PORT}))


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def require_running():
    """Exit with error if Chrome isn't running."""
    if not get_browser_ws():
        print("Chrome is not running. Use: python browser.py start")
        sys.exit(1)


def require_page():
    """Get active page WebSocket URL or exit."""
    ws = get_page_ws()
    if not ws:
        print("No active page found.")
        sys.exit(1)
    return ws


def get_ext_id():
    """Read cached extension ID."""
    if EXT_ID_FILE.exists():
        ext_id = EXT_ID_FILE.read_text().strip()
        if ext_id and ext_id not in BUILTIN_EXT_IDS:
            return ext_id
    return None


def discover_ext_id():
    """Discover Reader View extension ID from browser targets or verify CWS ID."""
    browser_ws = get_browser_ws()
    if not browser_ws:
        return None

    # First try finding extension targets (works when service worker is active)
    try:
        resp = cdp_send(browser_ws, "Target.getTargets")
        for t in resp.get("result", {}).get("targetInfos", []):
            url = t.get("url", "")
            if url.startswith("chrome-extension://"):
                ext_id = url.split("/")[2]
                if ext_id not in BUILTIN_EXT_IDS:
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    EXT_ID_FILE.write_text(ext_id)
                    return ext_id
    except Exception:
        pass

    # Fallback: verify the known CWS extension ID by navigating to it
    try:
        page_ws = get_page_ws()
        if page_ws:
            test_url = f"chrome-extension://{CWS_EXT_ID}/data/reader/index.html"
            resp = cdp_send(page_ws, "Page.navigate", {"url": test_url})
            if not resp.get("result", {}).get("errorText"):
                time.sleep(0.5)
                title_resp = cdp_send(page_ws, "Runtime.evaluate", {
                    "expression": "document.title || ''",
                    "returnByValue": True,
                })
                title = title_resp.get("result", {}).get("result", {}).get("value", "")
                if title and "error" not in title.lower():
                    # Navigate back to about:blank to not leave on extension page
                    cdp_send(page_ws, "Page.navigate", {"url": "about:blank"})
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    EXT_ID_FILE.write_text(CWS_EXT_ID)
                    return CWS_EXT_ID
    except Exception:
        pass

    return None


# --- Commands ---

def cmd_start():
    if get_browser_ws():
        print(f"Chrome already running on port {DEBUG_PORT}.")
        ext_id = get_ext_id() or discover_ext_id()
        if ext_id:
            print(f"Extension ID: {ext_id}")
        else:
            print("Reader View not installed. Run: python browser.py setup")
        return

    chrome = find_chrome()
    CHROME_DATA.mkdir(parents=True, exist_ok=True)

    args = [
        chrome,
        f"--user-data-dir={CHROME_DATA}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "about:blank",
    ]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print("Starting Chrome...")
    for _ in range(30):
        if get_browser_ws():
            save_state(proc.pid)
            print(f"Chrome started (PID {proc.pid})")

            time.sleep(1.5)
            ext_id = discover_ext_id()
            if ext_id:
                print(f"Extension ID: {ext_id}")
            else:
                print("Reader View not installed. Run: python browser.py setup")
            return
        time.sleep(0.5)

    print("ERROR: Chrome did not start in time.")
    proc.kill()
    sys.exit(1)


def cmd_setup():
    """Open CWS Reader View page for one-time installation."""
    require_running()
    page_ws = require_page()
    cdp_send(page_ws, "Page.navigate", {"url": CWS_URL})
    print(f"Opened Chrome Web Store: Reader View")
    print("Click 'Add to Chrome' to install, then run: python browser.py status")


def cmd_stop():
    state = load_state()
    if not state:
        print("No saved state. Nothing to stop.")
        return

    pid = state.get("pid")
    if pid:
        try:
            subprocess.run(
                ["taskkill", "/pid", str(pid), "/f", "/t"],
                capture_output=True,
            )
            print(f"Chrome stopped (PID {pid})")
        except Exception as e:
            print(f"Could not stop Chrome: {e}")

    STATE_FILE.unlink(missing_ok=True)


def cmd_status():
    state = load_state()
    ws = get_browser_ws()

    if ws:
        print(f"Chrome is running (port {DEBUG_PORT})")
        if state:
            print(f"  PID: {state.get('pid')}")

        ext_id = get_ext_id() or discover_ext_id()
        if ext_id:
            print(f"  Extension ID: {ext_id}")
            print(f"  Options URL: chrome-extension://{ext_id}/data/options/index.html")
        else:
            print("  Reader View: NOT INSTALLED")
            print(f"  Run: python browser.py setup")

        targets = get_all_targets()
        pages = [t for t in targets if t.get("type") == "page"]
        print(f"  Open pages: {len(pages)}")
        for i, p in enumerate(pages):
            marker = "*" if i == 0 else " "
            title = p.get("title", "(no title)")
            url = p.get("url", "")
            print(f"    {marker} {title}")
            print(f"      {url}")
    else:
        print("Chrome is not running.")
        ext_id = get_ext_id()
        if ext_id:
            print(f"  Cached Extension ID: {ext_id}")


def cmd_open(url):
    require_running()
    if not url.startswith(("http", "chrome", "about", "file")):
        url = "https://" + url
    page_ws = require_page()
    resp = cdp_send(page_ws, "Page.navigate", {"url": url})
    err = resp.get("result", {}).get("errorText")
    if err:
        print(f"Navigation error: {err}")
    else:
        print(f"Navigated to {url}")


def cmd_js(expression):
    require_running()
    page_ws = require_page()
    resp = cdp_send(page_ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    result = resp.get("result", {}).get("result", {})
    exception = resp.get("result", {}).get("exceptionDetails")
    if exception:
        desc = exception.get("exception", {}).get("description", "Unknown error")
        print(f"JS Error: {desc}", file=sys.stderr)
        sys.exit(1)
    elif result.get("type") == "undefined":
        pass
    elif "value" in result:
        val = result["value"]
        if isinstance(val, str):
            print(val)
        else:
            print(json.dumps(val, indent=2))
    elif result.get("description"):
        print(result["description"])


def cmd_screenshot(filename="screenshot.png"):
    require_running()
    page_ws = require_page()
    resp = cdp_send(page_ws, "Page.captureScreenshot", {"format": "png"})
    data = resp.get("result", {}).get("data", "")
    if data:
        path = Path(filename).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        print(f"Screenshot saved: {path}")
    else:
        print("Failed to capture screenshot.")
        sys.exit(1)


def cmd_click(selector):
    require_running()
    page_ws = require_page()
    escaped = selector.replace("'", "\\'")
    js = (
        f"(() => {{"
        f"  const el = document.querySelector('{escaped}');"
        f"  if (!el) return 'Element not found: {escaped}';"
        f"  el.click();"
        f"  return 'clicked';"
        f"}})()"
    )
    resp = cdp_send(page_ws, "Runtime.evaluate", {
        "expression": js,
        "returnByValue": True,
    })
    val = resp.get("result", {}).get("result", {}).get("value", "")
    if val == "clicked":
        print(f"Clicked: {selector}")
    else:
        print(val)
        sys.exit(1)


def cmd_wait(seconds=2):
    require_running()
    time.sleep(float(seconds))
    print(f"Waited {seconds}s")


def cmd_push():
    require_running()
    ext_id = get_ext_id()
    if not ext_id:
        ext_id = discover_ext_id()
    if not ext_id:
        print("Extension ID unknown. Run 'setup' to install Reader View first.")
        sys.exit(1)

    # Read settings files from my-settings/
    css_file = SETTINGS_DIR / "reader-view.css"
    sidebar_file = SETTINGS_DIR / "frame-sidebar.css"
    action_file = SETTINGS_DIR / "user-action.json"

    for f in [css_file, sidebar_file, action_file]:
        if not f.exists():
            print(f"Missing: {f}")
            sys.exit(1)

    reader_css = css_file.read_text(encoding="utf-8")
    sidebar_css = sidebar_file.read_text(encoding="utf-8")
    user_action = action_file.read_text(encoding="utf-8")

    # Validate and parse JSON
    try:
        actions_parsed = json.loads(user_action)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in user-action.json: {e}")
        sys.exit(1)

    # Build storage payload (keys match chrome.storage.local keys)
    payload = {
        "user-css": reader_css,
        "top-css": sidebar_css,
        "user-action": actions_parsed,
    }

    # Navigate to options page (need extension context for chrome.storage)
    opts_url = f"chrome-extension://{ext_id}/data/options/index.html"
    print("Opening options page...")
    page_ws = require_page()
    cdp_send(page_ws, "Page.navigate", {"url": opts_url})
    time.sleep(2.5)

    # Base64-encode to avoid escaping issues
    b64_payload = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    js = (
        f"new Promise((resolve, reject) => {{"
        f"  const data = JSON.parse(atob('{b64_payload}'));"
        f"  chrome.storage.local.set(data, () => {{"
        f"    if (chrome.runtime.lastError) reject(chrome.runtime.lastError.message);"
        f"    else resolve('OK');"
        f"  }});"
        f"}})"
    )
    resp = cdp_send(page_ws, "Runtime.evaluate", {
        "expression": js,
        "returnByValue": True,
        "awaitPromise": True,
    })

    exception = resp.get("result", {}).get("exceptionDetails")
    if exception:
        desc = exception.get("exception", {}).get("description", "Unknown error")
        print(f"Error: {desc}", file=sys.stderr)
        sys.exit(1)

    result_val = resp.get("result", {}).get("result", {}).get("value", "")
    if result_val == "OK":
        print("  user-css <- reader-view.css")
        print("  top-css <- frame-sidebar.css")
        print("  user-action <- user-action.json")
        print("Settings pushed and saved!")
    else:
        print(f"Unexpected result: {result_val}")


def cmd_import():
    """Import all preferences from reader-view-preferences.json via chrome.storage.local."""
    require_running()
    ext_id = get_ext_id() or discover_ext_id()
    if not ext_id:
        print("Extension ID unknown. Run 'setup' to install Reader View first.")
        sys.exit(1)

    prefs_file = SETTINGS_DIR / "reader-view-preferences.json"
    if not prefs_file.exists():
        print(f"Missing: {prefs_file}")
        sys.exit(1)

    prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
    print(f"Loaded {len(prefs)} preferences from {prefs_file.name}")

    # Navigate to extension options page (need extension context for chrome.storage)
    opts_url = f"chrome-extension://{ext_id}/data/options/index.html"
    print("Opening options page...")
    page_ws = require_page()
    cdp_send(page_ws, "Page.navigate", {"url": opts_url})
    time.sleep(2.5)

    # Base64-encode the full preferences JSON to avoid escaping issues
    b64_prefs = base64.b64encode(json.dumps(prefs).encode("utf-8")).decode("ascii")

    # Push via chrome.storage.local.set()
    js = (
        f"new Promise((resolve, reject) => {{"
        f"  const prefs = JSON.parse(atob('{b64_prefs}'));"
        f"  chrome.storage.local.set(prefs, () => {{"
        f"    if (chrome.runtime.lastError) reject(chrome.runtime.lastError.message);"
        f"    else resolve('OK');"
        f"  }});"
        f"}})"
    )
    resp = cdp_send(page_ws, "Runtime.evaluate", {
        "expression": js,
        "returnByValue": True,
        "awaitPromise": True,
    })

    exception = resp.get("result", {}).get("exceptionDetails")
    if exception:
        desc = exception.get("exception", {}).get("description", "Unknown error")
        print(f"Error: {desc}", file=sys.stderr)
        sys.exit(1)

    result_val = resp.get("result", {}).get("result", {}).get("value", "")
    if result_val == "OK":
        print("All preferences imported successfully!")
    else:
        print(f"Unexpected result: {result_val}")


# --- Main ---

COMMANDS = {
    "start": (cmd_start, 0, ""),
    "setup": (cmd_setup, 0, ""),
    "stop": (cmd_stop, 0, ""),
    "status": (cmd_status, 0, ""),
    "open": (cmd_open, 1, "<url>"),
    "js": (cmd_js, 1, "<expression>"),
    "screenshot": (cmd_screenshot, -1, "[filename]"),
    "click": (cmd_click, 1, "<selector>"),
    "wait": (cmd_wait, -1, "[seconds]"),
    "push": (cmd_push, 0, ""),
    "import": (cmd_import, 0, ""),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd_name = sys.argv[1]
    if cmd_name not in COMMANDS:
        print(f"Unknown command: {cmd_name}")
        print(__doc__)
        sys.exit(1)

    func, nargs, usage = COMMANDS[cmd_name]

    if nargs == 0:
        func()
    elif nargs == 1:
        if len(sys.argv) < 3:
            print(f"Usage: python browser.py {cmd_name} {usage}")
            sys.exit(1)
        func(" ".join(sys.argv[2:]))
    elif nargs == -1:
        if len(sys.argv) > 2:
            func(sys.argv[2])
        else:
            func()


if __name__ == "__main__":
    main()
