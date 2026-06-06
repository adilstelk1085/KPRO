"""
KPRO Auto Downloader
- Ambil captcha dari halaman login
- Input captcha + OTP manual
- Centang checkbox agree otomatis
- Download file Excel
"""

import os
import re
import sys
import time
import logging
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}


# ── Helper ────────────────────────────────────────────────────────────────────

def buka_gambar(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        log.warning("Tidak bisa buka gambar otomatis: %s", exc)


def tanya(prompt: str, validator=None, pesan_error="Input tidak valid.") -> str:
    while True:
        nilai = input(f"  {prompt}: ").strip()
        if validator is None or validator(nilai):
            return nilai
        print(f"  {pesan_error}")


# ── Ambil halaman login + captcha ────────────────────────────────────────────

def get_login_page(session: requests.Session) -> BeautifulSoup:
    log.info("Membuka halaman login ...")
    resp = session.get(f"{BASE_URL}/login", timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def ambil_captcha(session: requests.Session, soup: BeautifulSoup) -> str:
    """
    Temukan gambar captcha di HTML, download, simpan ke file,
    buka otomatis, minta user ketik teksnya.
    """
    # Cari tag <img> yang mengandung kata 'captcha'
    img_tag = soup.find("img", src=re.compile(r"captcha", re.I))
    if not img_tag:
        # Coba cari semua img dan pilih yang bukan logo/icon
        imgs = soup.find_all("img")
        for img in imgs:
            src = img.get("src", "")
            if any(kw in src.lower() for kw in ["captcha", "verify", "code", "random"]):
                img_tag = img
                break

    if not img_tag:
        print("\n  [PERINGATAN] Gambar captcha tidak ditemukan otomatis.")
        print(f"  Buka manual: {BASE_URL}/login")
        return tanya("Masukkan teks captcha yang kamu lihat")

    src = img_tag.get("src", "")
    # Jika src relatif, tambahkan base URL
    if src.startswith("/"):
        captcha_url = BASE_URL.rstrip("/") + src
    elif src.startswith("http"):
        captcha_url = src
    else:
        captcha_url = f"{BASE_URL}/{src}"

    log.info("Download captcha dari: %s", captcha_url)
    resp = session.get(captcha_url, timeout=15)
    resp.raise_for_status()

    tmp = Path("captcha_tmp.png")
    tmp.write_bytes(resp.content)

    print("\n" + "="*52)
    print("  Gambar captcha disimpan di:", tmp.resolve())
    print("  Membuka gambar captcha ...")
    print("="*52)
    buka_gambar(tmp)

    return tanya(
        "Masukkan teks captcha (perhatikan huruf besar/kecil)",
        lambda v: len(v) >= 1,
    )


# ── Login ─────────────────────────────────────────────────────────────────────

def login(session: requests.Session, soup: BeautifulSoup, captcha_teks: str) -> None:
    log.info("Login dengan username %s ...", USERNAME)

    # Cari action URL dari form
    form = soup.find("form")
    action = BASE_URL + "/login"
    if form and form.get("action"):
        act = form["action"]
        action = act if act.startswith("http") else BASE_URL.rstrip("/") + "/" + act.lstrip("/")

    # Ambil semua hidden input (termasuk CSRF token jika ada)
    payload = {}
    if form:
        for inp in form.find_all("input", type="hidden"):
            if inp.get("name"):
                payload[inp["name"]] = inp.get("value", "")

    # Isi field utama
    payload.update({
        "username": USERNAME,
        "password": PASSWORD,
        "captcha": captcha_teks,
        "agree":   "on",        # checkbox Term of Use
    })

    session.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{BASE_URL}/login",
    })

    resp = session.post(action, data=payload, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    # Cek apakah masih di halaman login (berarti gagal)
    if "captcha" in resp.text.lower() and "sign in" in resp.text.lower():
        raise ValueError("Login gagal — captcha mungkin salah atau kredensial tidak valid.")

    log.info("Login berhasil (HTTP %d).", resp.status_code)


# ── OTP ──────────────────────────────────────────────────────────────────────

def input_otp_manual() -> str:
    print("\n" + "="*52)
    print("  Cek Telegram kamu — OTP dikirim oleh bot KPRO.")
    print("  (Jika ada notif 'Join New Bot', join dulu botnya)")
    print("="*52)
    return tanya(
        "Masukkan OTP",
        lambda v: v.isdigit() and 4 <= len(v) <= 8,
        "OTP tidak valid. Masukkan angka 4-8 digit.",
    )


def submit_otp(session: requests.Session, otp: str) -> None:
    log.info("Mengirim OTP %s ...", otp)
    session.headers.update({"Content-Type": "application/json"})
    payload = {"otp": otp}

    for endpoint in (f"{BASE_URL}/api/verify-otp", f"{BASE_URL}/verify-otp", f"{BASE_URL}/otp"):
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


# ── Download Excel ────────────────────────────────────────────────────────────

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


# ── Main ──────────────────────────────────────────────────────────────────────

def run_once() -> Path:
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Buka halaman login
    soup = get_login_page(session)

    # 2. Ambil & input captcha manual
    captcha_teks = ambil_captcha(session, soup)

    # 3. Login (dengan captcha + agree checkbox)
    login(session, soup, captcha_teks)

    # 4. Input OTP manual dari Telegram
    otp = input_otp_manual()
    submit_otp(session, otp)

    # 5. Download Excel
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
            Path("captcha_tmp.png").unlink(missing_ok=True)
            return
        except ValueError as exc:
            log.error("%s", exc)
            print("\n  Coba lagi dengan captcha yang benar.\n")
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
