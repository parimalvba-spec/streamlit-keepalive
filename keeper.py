from playwright.sync_api import sync_playwright, Page
import time
import datetime
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
def load_sites():
    with open("sites.txt", "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

SITES = load_sites()

PING_INTERVAL = 300        # seconds between pings (5 min)
WAKE_TIMEOUT  = 15_000     # ms to wait for wake button
GOTO_TIMEOUT  = 120_000    # ms for page load (2 min — generous for slow apps)
MAX_RETRIES   = 3          # retries before marking as error
PORT = int(os.environ.get("PORT", 8080))

status = {}  # url -> {state, last_ping, retries}


# ─────────────────────────────────────────────
#  Web server – status dashboard
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = len(status)
        ok    = sum(1 for v in status.values() if "Running" in v.get("state","") or "Woken" in v.get("state",""))
        err   = sum(1 for v in status.values() if "Error" in v.get("state",""))

        rows = ""
        for url, info in status.items():
            state   = info.get("state", "⏳ Loading...")
            last    = info.get("last_ping", "—")
            retries = info.get("retries", 0)
            color   = "#2ecc71" if ("Running" in state or "Woken" in state) else (
                      "#f39c12" if "Loading" in state or "Retry" in state else "#e74c3c")
            retry_badge = f' <span style="color:#f39c12;font-size:11px">(retry {retries}/{MAX_RETRIES})</span>' if retries > 0 else ""
            rows += f"""
            <tr>
                <td><a href="{url}" target="_blank">{url}</a></td>
                <td style="color:{color}; font-weight:bold">{state}{retry_badge}</td>
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
        .stats {{ display: flex; gap: 20px; margin: 15px 0; }}
        .stat {{ background: #1e2530; padding: 12px 24px; border-radius: 8px; text-align: center; }}
        .stat .num {{ font-size: 28px; font-weight: bold; }}
        .stat .label {{ font-size: 12px; color: #aaa; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #1e2530; padding: 12px; text-align: left; color: #4fc3f7; }}
        td {{ padding: 12px; border-bottom: 1px solid #2a3140; }}
        a {{ color: #4fc3f7; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{ margin-top: 20px; color: #666; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>🟢 Streamlit Keeper Dashboard</h1>
    <p>Auto-refreshes every 30 seconds &nbsp;|&nbsp; Server time: {now}</p>
    <div class="stats">
        <div class="stat"><div class="num" style="color:#4fc3f7">{total}</div><div class="label">Total Sites</div></div>
        <div class="stat"><div class="num" style="color:#2ecc71">{ok}</div><div class="label">Running</div></div>
        <div class="stat"><div class="num" style="color:#e74c3c">{err}</div><div class="label">Errors</div></div>
    </div>
    <table>
        <tr><th>Site</th><th>Status</th><th>Last Ping</th></tr>
        {rows if rows else '<tr><td colspan="3" style="color:#f39c12">⏳ Starting up, please wait...</td></tr>'}
    </table>
    <p class="footer">Pinging every {PING_INTERVAL // 60} minutes &nbsp;|&nbsp; {total} sites monitored</p>
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


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def wake_if_sleeping(page, url: str) -> str:
    try:
        page.wait_for_selector(
            'button[data-testid="wakeup-button-viewer"]',
            timeout=WAKE_TIMEOUT,
        )
        page.locator('button[data-testid="wakeup-button-viewer"]').click()
        page.wait_for_load_state("networkidle", timeout=GOTO_TIMEOUT)
        log(f"  ↑ Woke up → {url}")
        return "😴 Woken Up"
    except Exception:
        log(f"  ✓ Running  → {url}")
        return "✅ Running"


def load_page(page, url: str):
    """Load a page with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status[url]["retries"] = attempt - 1
            page.goto(url, timeout=GOTO_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=GOTO_TIMEOUT)
            return True
        except Exception as e:
            log(f"  Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")
            status[url]["retries"] = attempt
            status[url]["state"] = f"🔄 Retry {attempt}/{MAX_RETRIES}"
            status[url]["last_ping"] = now_str()
            if attempt < MAX_RETRIES:
                time.sleep(10)
    return False


def send_activity(page):
    try:
        page.mouse.move(200, 300)
        page.evaluate("window.scrollBy(0, 1)")
    except Exception:
        pass


def install_browser():
    import subprocess
    log("Installing Chromium …")
    subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)
    log("Chromium ready.")


def main():
    threading.Thread(target=start_web_server, daemon=True).start()
    install_browser()
    log(f"Starting Streamlit keeper for {len(SITES)} sites …")

    for url in SITES:
        status[url] = {"state": "⏳ Loading...", "last_ping": "—", "retries": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        pages: list[tuple[str, Page]] = []
        for url in SITES:
            page = browser.new_page()
            if load_page(page, url):
                state = wake_if_sleeping(page, url)
                pages.append((url, page))
                status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
            else:
                status[url] = {"state": "❌ Error", "last_ping": now_str(), "retries": MAX_RETRIES}
                log(f"  ✗ Giving up on {url} — will retry next ping cycle")

        log(f"\nAll sites processed. Pinging every {PING_INTERVAL // 60} min …\n")

        while True:
            time.sleep(PING_INTERVAL)

            # Re-read sites.txt in case it changed
            current_sites = load_sites()

            for url in current_sites:
                # Find existing page or open new one
                page_entry = next((p for u, p in pages if u == url), None)

                if page_entry is None:
                    # New site added to sites.txt
                    page_entry = browser.new_page()
                    status[url] = {"state": "⏳ Loading...", "last_ping": "—", "retries": 0}

                try:
                    page_entry.reload(timeout=GOTO_TIMEOUT)
                    page_entry.wait_for_load_state("networkidle", timeout=GOTO_TIMEOUT)
                    state = wake_if_sleeping(page_entry, url)
                    send_activity(page_entry)
                    status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
                    log(f"Pinged → {url}")
                except Exception as e:
                    log(f"Error on {url}: {e} — retrying …")
                    if load_page(page_entry, url):
                        state = wake_if_sleeping(page_entry, url)
                        status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
                        if (url, page_entry) not in pages:
                            pages.append((url, page_entry))
                    else:
                        status[url] = {"state": "❌ Error", "last_ping": now_str(), "retries": MAX_RETRIES}


if __name__ == "__main__":
    main()
