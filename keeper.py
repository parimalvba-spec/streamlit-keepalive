from playwright.sync_api import sync_playwright, Page
import time
import datetime
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
# Sites are loaded from sites.txt — just add/remove URLs there, no code change needed!
def load_sites():
    with open("sites.txt", "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

SITES = load_sites()

PING_INTERVAL = 300
WAKE_TIMEOUT  = 10_000
PORT = int(os.environ.get("PORT", 8080))

# Shared status dict (updated by keeper loop)
status = {}  # url -> {"state": "✅ Running" | "😴 Woken" | "❌ Error", "last_ping": "..."}


# ─────────────────────────────────────────────
#  Web server – shows live status page
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = ""
        for url, info in status.items():
            state = info.get("state", "⏳ Loading...")
            last  = info.get("last_ping", "—")
            color = "#2ecc71" if "Running" in state or "Woken" in state else (
                    "#f39c12" if "Loading" in state else "#e74c3c")
            rows += f"""
            <tr>
                <td><a href="{url}" target="_blank">{url}</a></td>
                <td style="color:{color}; font-weight:bold">{state}</td>
                <td>{last}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Streamlit Keeper</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body {{ font-family: Arial, sans-serif; background: #0e1117; color: #fff; padding: 30px; }}
        h1 {{ color: #4fc3f7; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #1e2530; padding: 12px; text-align: left; color: #4fc3f7; }}
        td {{ padding: 12px; border-bottom: 1px solid #2a3140; }}
        a {{ color: #4fc3f7; }}
        .footer {{ margin-top: 20px; color: #666; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>🟢 Streamlit Keeper Dashboard</h1>
    <p>Auto-refreshes every 30 seconds &nbsp;|&nbsp; Server time: {now}</p>
    <table>
        <tr>
            <th>Site</th>
            <th>Status</th>
            <th>Last Ping</th>
        </tr>
        {rows if rows else '<tr><td colspan="3" style="color:#f39c12">⏳ Starting up, please wait...</td></tr>'}
    </table>
    <p class="footer">Pinging every {PING_INTERVAL // 60} minutes</p>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log(f"Web server started on port {PORT}")
    server.serve_forever()


# ─────────────────────────────────────────────
def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def wake_if_sleeping(page, url: str) -> str:
    try:
        page.wait_for_selector(
            'button[data-testid="wakeup-button-viewer"]',
            timeout=WAKE_TIMEOUT,
        )
        page.locator('button[data-testid="wakeup-button-viewer"]').click()
        page.wait_for_load_state("networkidle", timeout=60_000)
        log(f"  ↑ Woke up sleeping app → {url}")
        return "😴 Woken Up"
    except Exception:
        log(f"  ✓ Already running    → {url}")
        return "✅ Running"


def send_activity(page):
    try:
        page.mouse.move(200, 300)
        page.evaluate("window.scrollBy(0, 1)")
    except Exception:
        pass


def install_browser():
    import subprocess
    log("Checking / installing Chromium …")
    subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)
    log("Chromium ready.")


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()

    install_browser()
    log("Starting Streamlit keeper …")

    # Init status
    for url in SITES:
        status[url] = {"state": "⏳ Loading...", "last_ping": "—"}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        pages: list[tuple[str, Page]] = []
        for url in SITES:
            page = browser.new_page()
            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("networkidle", timeout=60_000)
                state = wake_if_sleeping(page, url)
                pages.append((url, page))
                status[url] = {"state": state, "last_ping": now_str()}
                log(f"  ✓ Loaded → {url}")
            except Exception as e:
                status[url] = {"state": "❌ Error", "last_ping": now_str()}
                log(f"  ✗ Failed to load {url}: {e}")

        log(f"\nAll sites loaded. Pinging every {PING_INTERVAL // 60} min …\n")
        while True:
            time.sleep(PING_INTERVAL)

            for url, page in pages:
                try:
                    page.reload(timeout=60_000)
                    page.wait_for_load_state("networkidle", timeout=60_000)
                    state = wake_if_sleeping(page, url)
                    send_activity(page)
                    status[url] = {"state": state, "last_ping": now_str()}
                    log(f"Pinged → {url}")
                except Exception as e:
                    status[url] = {"state": "❌ Error", "last_ping": now_str()}
                    log(f"Error on {url}: {e} — reopening …")
                    try:
                        page.goto(url, timeout=60_000)
                        page.wait_for_load_state("networkidle", timeout=60_000)
                        state = wake_if_sleeping(page, url)
                        status[url] = {"state": state, "last_ping": now_str()}
                    except Exception as e2:
                        log(f"Recovery failed for {url}: {e2}")


if __name__ == "__main__":
    main()
