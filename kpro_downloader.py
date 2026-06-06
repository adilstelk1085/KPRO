"""
KPRO Auto Downloader
Logs into kpro.telkom.co.id, handles OTP via Telegram, and downloads Excel file.
"""

import os
import re
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("kpro_downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Config from .env ────────────────────────────────────────────────────────
USERNAME        = os.getenv("KPRO_USERNAME", "")
PASSWORD        = os.getenv("KPRO_PASSWORD", "")
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL        = os.getenv("KPRO_BASE_URL", "https://kpro.telkom.co.id/kpro")
DOWNLOAD_DIR    = Path(os.getenv("DOWNLOAD_DIR", r"D:\KPRO"))
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY     = int(os.getenv("RETRY_DELAY", 5))
OTP_TIMEOUT     = int(os.getenv("OTP_TIMEOUT", 120))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Content-Type": "application/json",
}

# ── Telegram helper ──────────────────────────────────────────────────────────

def _tg_api(method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_otp_from_telegram(timeout: int = OTP_TIMEOUT) -> str:
    """Poll Telegram for a message containing a numeric OTP code."""
    log.info("Menunggu OTP dari Telegram (timeout %ds) ...", timeout)
    last_update_id = None

    # Get the latest update_id to avoid re-reading old messages
    updates = _tg_api("getUpdates", limit=1, offset=-1).get("result", [])
    if updates:
        last_update_id = updates[-1]["update_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        offset = (last_update_id + 1) if last_update_id is not None else None
        kwargs = {"timeout": 20, "allowed_updates": "message"}
        if offset:
            kwargs["offset"] = offset

        try:
            result = _tg_api("getUpdates", **kwargs).get("result", [])
        except requests.RequestException as exc:
            log.warning("Gagal polling Telegram: %s", exc)
            time.sleep(2)
            continue

        for update in result:
            last_update_id = update["update_id"]
            msg = update.get("message", {})
            # Only accept messages from the configured chat
            if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
                continue
            text = msg.get("text", "")
            # Look for a 4-8 digit OTP in the message
            match = re.search(r"\b(\d{4,8})\b", text)
            if match:
                otp = match.group(1)
                log.info("OTP diterima: %s", otp)
                return otp

    raise TimeoutError(f"OTP tidak diterima dalam {timeout} detik.")


# ── KPRO session ─────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def login(session: requests.Session) -> None:
    """POST login credentials and handle the initial auth step."""
    log.info("Login dengan username %s ...", USERNAME)
    payload = {"username": USERNAME, "password": PASSWORD}
    resp = session.post(f"{BASE_URL}/api/login", json=payload, timeout=30)

    if resp.status_code == 404:
        # Fallback: try form-based login endpoint
        session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
        resp = session.post(
            f"{BASE_URL}/login",
            data={"username": USERNAME, "password": PASSWORD},
            timeout=30,
            allow_redirects=True,
        )
        session.headers.update({"Content-Type": "application/json"})

    resp.raise_for_status()
    log.info("Login awal berhasil (HTTP %d).", resp.status_code)


def submit_otp(session: requests.Session, otp: str) -> None:
    """Submit the OTP received via Telegram."""
    log.info("Mengirim OTP %s ...", otp)
    payload = {"otp": otp}

    for endpoint in (f"{BASE_URL}/api/verify-otp", f"{BASE_URL}/verify-otp"):
        try:
            resp = session.post(endpoint, json=payload, timeout=30)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            log.info("OTP diterima server (HTTP %d).", resp.status_code)
            return
        except requests.HTTPError as exc:
            log.warning("Endpoint %s gagal: %s", endpoint, exc)

    raise RuntimeError("Semua endpoint OTP gagal.")


def download_excel(session: requests.Session) -> bytes:
    """Download the Excel file and return its raw bytes."""
    candidates = [
        f"{BASE_URL}/api/export/excel",
        f"{BASE_URL}/api/download/excel",
        f"{BASE_URL}/export",
        f"{BASE_URL}/download",
    ]
    for url in candidates:
        log.info("Mencoba download dari %s ...", url)
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "excel" in content_type or "spreadsheet" in content_type or len(resp.content) > 1000:
                log.info("File berhasil diunduh (%d bytes).", len(resp.content))
                return resp.content
            log.warning("Respons bukan file Excel, lanjut ke endpoint berikutnya.")
        except requests.HTTPError as exc:
            log.warning("Endpoint %s gagal: %s", url, exc)

    raise RuntimeError("Tidak ada endpoint download yang berhasil.")


def save_file(data: bytes) -> Path:
    """Save the downloaded bytes to DOWNLOAD_DIR with a timestamp."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = DOWNLOAD_DIR / f"KPRO_{timestamp}.xlsx"
    filename.write_bytes(data)
    log.info("File disimpan: %s", filename)
    return filename


# ── Main with retry ──────────────────────────────────────────────────────────

def run_once() -> Path:
    session = create_session()
    login(session)
    otp = get_otp_from_telegram()
    submit_otp(session, otp)
    data = download_excel(session)
    return save_file(data)


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        log.error(
            "TELEGRAM_BOT_TOKEN belum diisi di file .env! "
            "Dapatkan token dari @BotFather di Telegram."
        )
        return

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("=== Percobaan %d/%d ===", attempt, MAX_RETRIES)
        try:
            saved = run_once()
            log.info("Selesai! File tersimpan di: %s", saved)
            return
        except TimeoutError as exc:
            log.error("Timeout: %s", exc)
        except requests.HTTPError as exc:
            log.error("HTTP error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error tidak terduga: %s", exc)

        if attempt < MAX_RETRIES:
            log.info("Menunggu %d detik sebelum retry ...", RETRY_DELAY * attempt)
            time.sleep(RETRY_DELAY * attempt)

    log.error("Semua percobaan gagal.")


if __name__ == "__main__":
    main()
