from playwright.sync_api import sync_playwright, Page
import time
import datetime
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
PING_INTERVAL = 300
WAKE_TIMEOUT  = 15_000
GOTO_TIMEOUT  = 120_000
MAX_RETRIES   = 3
PORT = int(os.environ.get("PORT", 8080))

status = {}  # url -> {state, last_ping, retries}


def load_sites():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.txt"),
        os.path.join(os.getcwd(), "sites.txt"),
        "/opt/render/project/src/sites.txt",
    ]
    for path in candidates:
        if os.path.exists(path):
            log(f"Loading sites from: {path}")
            with open(path, "r") as f:
                sites = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            log(f"Loaded {len(sites)} sites: {sites}")
            return sites
    log("ERROR: sites.txt not found in any of: " + str(candidates))
    return []


# ─────────────────────────────────────────────
#  Web server – status dashboard
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = len(status)
        ok    = sum(1 for v in status.values() if "Running" in v.get("state","") or "Woken" in v.get("state",""))
        err   = sum(1 for v in status.values() if "Error" in v.get("state",""))

        rows = ""
        for url, info in status.items():
            state   = info.get("state", "⏳ Loading...")
            last    = info.get("last_ping", "—")
            retries = info.get("retries", 0)
            color   = "#2ecc71" if ("Running" in state or "Woken" in state) else (
                      "#f39c12" if ("Loading" in state or "Retry" in state) else "#e74c3c")
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
#  Playwright helpers
# ─────────────────────────────────────────────
def wake_if_sleeping(page, url: str) -> str:
    # Try multiple selectors for the wake button
    selectors = [
        'button[data-testid="wakeup-button-viewer"]',
        'button:has-text("Yes, get this app back up!")',
        'button:has-text("get this app back up")',
        'button._restartButton_2xb9v_14',
        'button[class*="restartButton"]',
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=5_000)
            page.locator(selector).first.click()
            log(f"  ↑ Clicked wake button ({selector}) → {url}")
            page.wait_for_load_state("networkidle", timeout=GOTO_TIMEOUT)
            log(f"  ↑ Woke up → {url}")
            return "😴 Woken Up"
        except Exception:
            continue
    log(f"  ✓ Running  → {url}")
    return "✅ Running"


def load_page_with_retry(page, url: str) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status[url]["retries"] = attempt - 1
            page.goto(url, timeout=GOTO_TIMEOUT)
            # Use domcontentloaded — faster than networkidle for Streamlit apps
            page.wait_for_load_state("domcontentloaded", timeout=GOTO_TIMEOUT)
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


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────
def main():
    threading.Thread(target=start_web_server, daemon=True).start()
    install_browser()

    sites = load_sites()
    if not sites:
        log("No sites found! Check sites.txt exists in the repo.")
        # Keep web server alive so we can see the error on dashboard
        while True:
            time.sleep(60)

    log(f"Starting keeper for {len(sites)} sites …")

    # Init status for all sites
    for url in sites:
        status[url] = {"state": "⏳ Loading...", "last_ping": "—", "retries": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        pages: list[tuple[str, Page]] = []
        for url in sites:
            page = browser.new_page()
            if load_page_with_retry(page, url):
                state = wake_if_sleeping(page, url)
                pages.append((url, page))
                status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
            else:
                status[url] = {"state": "❌ Error", "last_ping": now_str(), "retries": MAX_RETRIES}
                log(f"  ✗ Giving up on {url}")

        log(f"\nAll sites processed. Pinging every {PING_INTERVAL // 60} min …\n")

        while True:
            time.sleep(PING_INTERVAL)

            # Reload sites.txt every cycle (dynamic add/remove)
            current_sites = load_sites()
            for url in current_sites:
                page_entry = next((pg for u, pg in pages if u == url), None)
                if page_entry is None:
                    page_entry = browser.new_page()
                    status[url] = {"state": "⏳ Loading...", "last_ping": "—", "retries": 0}

                try:
                    page_entry.reload(timeout=GOTO_TIMEOUT)
                    page_entry.wait_for_load_state("networkidle", timeout=GOTO_TIMEOUT)
                    state = wake_if_sleeping(page_entry, url)
                    send_activity(page_entry)
                    status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
                    if (url, page_entry) not in pages:
                        pages.append((url, page_entry))
                    log(f"Pinged → {url}")
                except Exception as e:
                    log(f"Error on {url}: {e} — retrying …")
                    if load_page_with_retry(page_entry, url):
                        state = wake_if_sleeping(page_entry, url)
                        status[url] = {"state": state, "last_ping": now_str(), "retries": 0}
                        if (url, page_entry) not in pages:
                            pages.append((url, page_entry))
                    else:
                        status[url] = {"state": "❌ Error", "last_ping": now_str(), "retries": MAX_RETRIES}


if __name__ == "__main__":
    main()
