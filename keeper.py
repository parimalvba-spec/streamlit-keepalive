from playwright.sync_api import sync_playwright, Page
import time
import datetime
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
#  CONFIG – add / remove your Streamlit URLs here
# ─────────────────────────────────────────────
SITES = [
    "https://bg-pro.streamlit.app/",
    "https://bg-removed.streamlit.app/",
    "https://pdf-pro.streamlit.app/",
]

PING_INTERVAL = 300   # seconds between activity pings (5 min)
WAKE_TIMEOUT  = 10_000  # ms to wait for the wake button
PORT = int(os.environ.get("PORT", 8080))  # Render sets PORT automatically


# ─────────────────────────────────────────────
#  Tiny web server so Render free tier doesn't kill the process
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Streamlit keeper is running!")

    def log_message(self, format, *args):
        pass  # suppress request logs


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log(f"Web server started on port {PORT}")
    server.serve_forever()


# ─────────────────────────────────────────────
def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def wake_if_sleeping(page, url: str):
    """Click the wake button if the app is hibernating."""
    try:
        page.wait_for_selector(
            'button[data-testid="wakeup-button-viewer"]',
            timeout=WAKE_TIMEOUT,
        )
        page.locator('button[data-testid="wakeup-button-viewer"]').click()
        page.wait_for_load_state("networkidle", timeout=60_000)
        log(f"  ↑ Woke up sleeping app → {url}")
    except Exception:
        log(f"  ✓ Already running    → {url}")


def send_activity(page):
    """Send tiny mouse / scroll activity so Streamlit resets its idle timer."""
    try:
        page.mouse.move(200, 300)
        page.evaluate("window.scrollBy(0, 1)")
    except Exception:
        pass


def install_browser():
    """Ensure Chromium is installed at runtime (needed on Render)."""
    import subprocess
    log("Checking / installing Chromium …")
    subprocess.run(
        ["python", "-m", "playwright", "install", "chromium"],
        check=True,
    )
    log("Chromium ready.")


def main():
    # Start web server in background thread (keeps Render free tier happy)
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()

    install_browser()
    log("Starting Streamlit keeper …")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        # Open every site once and wake it if needed
        pages: list[tuple[str, Page]] = []
        for url in SITES:
            page = browser.new_page()
            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("networkidle", timeout=60_000)
                wake_if_sleeping(page, url)
                pages.append((url, page))
                log(f"  ✓ Loaded → {url}")
            except Exception as e:
                log(f"  ✗ Failed to load {url}: {e}")

        # Keep-alive loop
        log(f"\nAll sites loaded. Pinging every {PING_INTERVAL // 60} min …\n")
        while True:
            time.sleep(PING_INTERVAL)

            for url, page in pages:
                try:
                    page.reload(timeout=60_000)
                    page.wait_for_load_state("networkidle", timeout=60_000)
                    wake_if_sleeping(page, url)
                    send_activity(page)
                    log(f"Pinged → {url}")
                except Exception as e:
                    log(f"Error on {url}: {e} — reopening …")
                    try:
                        page.goto(url, timeout=60_000)
                        page.wait_for_load_state("networkidle", timeout=60_000)
                        wake_if_sleeping(page, url)
                    except Exception as e2:
                        log(f"Recovery failed for {url}: {e2}")


if __name__ == "__main__":
    main()
