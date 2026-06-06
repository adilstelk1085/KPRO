"""
KPRO Auto Downloader
Login ke kpro.telkom.co.id, input OTP manual, download file Excel.
"""

import os
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

# ── Config dari .env ─────────────────────────────────────────────────────────
USERNAME     = os.getenv("KPRO_USERNAME", "")
PASSWORD     = os.getenv("KPRO_PASSWORD", "")
BASE_URL     = os.getenv("KPRO_BASE_URL", "https://kpro.telkom.co.id/kpro")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", r"D:\KPRO"))
MAX_RETRIES  = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY  = int(os.getenv("RETRY_DELAY", 5))

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


# ── Input OTP manual ─────────────────────────────────────────────────────────

def get_otp_manual() -> str:
    """Tampilkan prompt ke user dan minta OTP diketik manual."""
    print("\n" + "="*50)
    print("  Cek Telegram kamu — OTP sudah dikirim oleh")
    print("  @kproverification_newbot")
    print("="*50)
    while True:
        otp = input("  Masukkan OTP: ").strip()
        if otp.isdigit() and 4 <= len(otp) <= 8:
            return otp
        print("  OTP tidak valid. Masukkan angka 4-8 digit.")


# ── KPRO session ─────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def login(session: requests.Session) -> None:
    log.info("Login dengan username %s ...", USERNAME)
    payload = {"username": USERNAME, "password": PASSWORD}
    resp = session.post(f"{BASE_URL}/api/login", json=payload, timeout=30)

    if resp.status_code == 404:
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
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = DOWNLOAD_DIR / f"KPRO_{timestamp}.xlsx"
    filename.write_bytes(data)
    log.info("File disimpan: %s", filename)
    return filename


# ── Main dengan retry ────────────────────────────────────────────────────────

def run_once() -> Path:
    session = create_session()
    login(session)
    otp = get_otp_manual()
    submit_otp(session, otp)
    data = download_excel(session)
    return save_file(data)


def main() -> None:
    print("\n===== KPRO Downloader =====")
    print(f"Username : {USERNAME}")
    print(f"Simpan ke: {DOWNLOAD_DIR}\n")

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("=== Percobaan %d/%d ===", attempt, MAX_RETRIES)
        try:
            saved = run_once()
            print(f"\nSelesai! File tersimpan di: {saved}")
            return
        except requests.HTTPError as exc:
            log.error("HTTP error: %s", exc)
        except RuntimeError as exc:
            log.error("Error: %s", exc)
        except KeyboardInterrupt:
            print("\nDibatalkan.")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Error tidak terduga: %s", exc)

        if attempt < MAX_RETRIES:
            log.info("Menunggu %d detik sebelum retry ...", RETRY_DELAY * attempt)
            time.sleep(RETRY_DELAY * attempt)

    log.error("Semua percobaan gagal.")


if __name__ == "__main__":
    main()
