# ==========================================================================
# main.py — Sistem Kinerja TTE (PPNP)
# ==========================================================================
# STRUKTUR FILE
#    1. Imports
#    2. Konfigurasi & Inisialisasi
#    3. Schema Pydantic
#    4. Helper umum (keamanan, file, status surat, utas surat)
#    5. Helper PDF (QR, inject nomor surat & TTD)
#    6. Route halaman HTML
#    7. API Autentikasi, Profil & TTE
#    8. API Data Organisasi
#    9. API Surat (CRUD, kirim, tracking, upload, posisi TTD, download)
#   10. API Tanda Tangan
#   11. API Disposisi
#   12. API Penugasan, Laporan & Penilaian Tugas
#   13. API Penilaian Layanan (bintang per utas surat)
#   14. API Agregasi Inbox (Surat Masuk, Perlu Tindakan, Riwayat)
# ==========================================================================


# ==========================================================================
# 1. IMPORTS
# ==========================================================================
import hashlib
import hmac
import io
import os
import random
import re
import shutil
import string
import time
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import quote

import bcrypt
import pymupdf
import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from database import SessionLocal
import secrets
from collections import defaultdict, deque

# ==========================================================================
# 2. KONFIGURASI & INISIALISASI
# ==========================================================================
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

# ⚠️ Untuk produksi, set lewat environment variable SECRET_KEY_VERIFIKASI.
# Kunci ini yang membuat signature QR verifikasi tidak bisa dipalsukan.
# (Jika diganti, QR pada surat yang sudah terbit tidak akan valid lagi.)
SECRET_KEY_VERIFIKASI = os.getenv(
    "SECRET_KEY_VERIFIKASI",
    "8f3e1a9c7d2b4f6e8a1c3d5f7b9e2a4c6d8f0b2e4a6c8d0f2b4e6a8c0d2f4b6e"
)

PATH_LOGO = "uploads/assets/logo.png"
# Folder privat: TIDAK di-mount sebagai static, hanya bisa dibuka lewat endpoint berhak akses
DIR_PRIVATE = "private_uploads"
os.makedirs(DIR_PRIVATE, exist_ok=True)

# Mapping kode unit -> file kop surat (di uploads/assets)
MAPPING_KOP = {
    # Direktur & Wakil Direktur
    "D001"  : "kop surat direktur dan wadir.png",
    "D002"  : "kop surat direktur dan wadir.png",
    "D003"  : "kop surat direktur dan wadir.png",
    "D004"  : "kop surat direktur dan wadir.png",
    # Jurusan
    "D002.1": "kop surat jurusan BP.png",
    "D002.2": "kop surat jurursan BT.png",   # typo di nama file dipertahankan
    "D002.3": "kop surat kepala jurusan PKH.png",
    "D002.4": "kop surat kepala jurusan RPK.png",
    "D002.5": "kop surat jurusan THP.png",
    # Unit lainnya
    "D002.6": "kop surat P3M.png",
    "D002.8": "kop surat UP3M.png",
    "D002.9": "kop surat UPT Perpustakaan.png",
    "D003.2": "kop surat UPT Komputer.png",
    "D003.3": "kop surat UPT PP.png",
    # Lembaga
    "D005"  : "kop surat SENAT.png",
    "D006"  : "kop surat SPI.png",
}

# Pastikan folder upload tersedia
for _folder in ["uploads/ttd", "uploads/dokumen", "uploads/final", "uploads/surat"]:
    os.makedirs(_folder, exist_ok=True)

app = FastAPI(title="Sistem Kinerja TTE")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================================================
# 3. SCHEMA PYDANTIC
# ==========================================================================

# --- Autentikasi ---
class LoginRequest(BaseModel):
    nip: str
    password: str


# --- Surat ---
class SuratCreate(BaseModel):
    jenis_surat: str
    nomor_surat: Optional[str] = None
    perihal: str
    asal_surat: Optional[str] = None
    tanggal_surat: Optional[str] = None
    tanggal_diterima: Optional[str] = None
    isi_surat: Optional[str] = None
    file_surat: Optional[str] = None
    nip_pembuat: str
    nip_penandatangan: Optional[str] = None
    nip_tujuan: Optional[str] = None
    tujuan_tambahan: Optional[str] = None
    tipe_input: str = "editor"
    file_upload: Optional[str] = None
    surat_asal_id: Optional[int] = None       # tautan ke surat pemicu (surat lanjutan / balasan)
    disposisi_asal_id: Optional[int] = None   # tautan ke node disposisi pemicu (Surat Tugas)
    penugasan_asal_id: Optional[int] = None   # tautan ke penugasan pemicu (Surat Tugas dari sekretaris)
    pegawai_ditugaskan: List[str] = []        # NIP pegawai yang ditugaskan (khusus surat tugas)
    tanggal_tugas_mulai: Optional[str] = None
    tanggal_tugas_selesai: Optional[str] = None
    jenis_tugas: Optional[str] = None
    tempat_tugas: Optional[str] = None
    jam_mulai: Optional[str] = None
    jam_selesai: Optional[str] = None
    agenda_rapat: Optional[str] = None
    nip_pimpinan_rapat: Optional[str] = None
    nip_notulen: Optional[str] = None


# --- Posisi TTD & nomor surat (surat upload PDF) ---
class PosisiTTDItem(BaseModel):
    nip_penyetuju: str
    label_ttd: str
    posisi_hal: int
    posisi_x: float
    posisi_y: float


class PosisiNomorItem(BaseModel):
    posisi_hal: int
    posisi_x: float
    posisi_y: float
    prefix_teks: Optional[str] = None


class SetPosisiTTDRequest(BaseModel):
    posisi_list: List[PosisiTTDItem]
    mode_paralel: bool = False


class SetPosisiNomorRequest(BaseModel):
    posisi_list: List[PosisiNomorItem]


# --- Upload ---
class BatalkanUploadRequest(BaseModel):
    nama_file: str


# --- Disposisi ---
class DisposisiItem(BaseModel):
    nip_ke: str
    catatan: str  # wajib


class DisposisiCreate(BaseModel):
    surat_id: int
    nip_pengirim: str                          # siapa yang sedang login & bertindak
    daftar_penerima: List[DisposisiItem]
    disposisi_asal_id: Optional[int] = None    # None = disposisi akar (dari Direktur)


class ArsipkanRequest(BaseModel):
    catatan: str  # wajib, alasan arsip


# --- Penugasan, laporan & penilaian tugas ---
class PenugasanCreate(BaseModel):
    surat_id: int
    disposisi_id: Optional[int] = None
    nip_pemberi: str
    daftar_penerima: List[str]                 # bisa lebih dari 1 penerima sekaligus
    deskripsi_tugas: Optional[str] = None
    deadline: Optional[str] = None


class LaporanTugasCreate(BaseModel):
    penugasan_id: int
    isi_laporan: Optional[str] = None
    file_bukti: Optional[str] = None


class PenilaianCreate(BaseModel):
    penugasan_id: int
    nip_penilai: str
    skala: str  # sangat_baik | baik | cukup | kurang
    catatan: Optional[str] = None


# --- Penilaian layanan (bintang per utas surat) ---
class PenilaianLayananCreate(BaseModel):
    surat_id: int          # surat manapun dalam utas — akar utas dicari otomatis
    nip_penilai: str
    bintang: int
    catatan: Optional[str] = None


# ==========================================================================
# 4. HELPER UMUM
# ==========================================================================

# --- 4a. Keamanan ---
def buat_signature(kode_pelacakan: str) -> str:
    """Signature HMAC untuk kode_pelacakan, dipakai di URL verifikasi QR."""
    return hmac.new(
        SECRET_KEY_VERIFIKASI.encode("utf-8"),
        kode_pelacakan.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()[:16]


def cek_signature(kode_pelacakan: str, sig: str) -> bool:
    return hmac.compare_digest(buat_signature(kode_pelacakan), sig)  # aman dari timing attack


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))


# --- 4b. Format & generator ---
def format_tanggal_indonesia(dt) -> str:
    """Contoh: '16 September 2026, 12:29 WIB'."""
    if not dt:
        return "—"
    bulan = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{dt.day} {bulan[dt.month - 1]} {dt.year}, {dt.hour:02d}:{dt.minute:02d} WIB"


def generate_kode() -> str:
    karakter = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "PPNP-" + "".join(secrets.choice(karakter) for _ in range(6))


# --- 4c. File upload ---
def buat_path_surat(jenis_surat: str, perihal: str, ekstensi: str) -> tuple[str, str]:
    """Return (folder_relatif, nama_file). Folder dibuat otomatis di dalam uploads/."""
    now = datetime.utcnow()
    perihal_bersih = re.sub(r"[^a-zA-Z0-9]+", "_", (perihal or "surat").strip())[:50].strip("_") or "surat"
    kode_acak = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))

    nama_file = f"{now.strftime('%Y%m%d')}_{perihal_bersih}_{kode_acak}.{ekstensi}"
    folder_relatif = f"surat/{jenis_surat}/{now.strftime('%Y')}/{now.strftime('%m')}"
    os.makedirs(f"uploads/{folder_relatif}", exist_ok=True)

    return folder_relatif, nama_file


def simpan_file_upload(file: UploadFile, path_relatif: str):
    """Tulis file upload ke uploads/<path_relatif>."""
    with open(f"uploads/{path_relatif}", "wb+") as f:
        shutil.copyfileobj(file.file, f)

def hapus_file_notulen(path_relatif: Optional[str]):
    """Hapus file notulen di folder privat (dan di uploads, untuk file lama sebelum dipindah)."""
    if not path_relatif:
        return
    for dasar in (DIR_PRIVATE, "uploads"):
        path_fisik = os.path.join(dasar, path_relatif)
        if os.path.exists(path_fisik):
            try:
                os.remove(path_fisik)
            except Exception as e:
                print(f"⚠️ Gagal hapus file {path_fisik}: {e}")


def _is_direktur(nip: str, db: Session) -> bool:
    pg = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pg:
        return False
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pg.kode_unit
    ).first()
    return bool(unit and unit.level == 1)


# --- 4d. Status surat (agregasi disposisi & penugasan) ---
def hitung_ulang_status_surat(surat_id: int, db: Session):
    """
    Hitung ulang status surat eksternal/internal dari NOL berdasarkan agregat
    seluruh cabang (leaf) disposisi & penugasan. Dipanggil setiap kali ada
    perubahan pada pohon disposisi/penugasan — status TIDAK di-set manual.

    Sebuah cabang dianggap AKTIF (belum tuntas) jika:
      - ada Disposisi berstatus 'diteruskan' yang TIDAK punya anak disposisi, atau
      - ada Penugasan berstatus 'proses' untuk surat ini.
    Tidak ada cabang aktif -> 'selesai'; selain itu -> 'diproses'.
    """
    db.flush()  # pastikan perubahan yang belum di-commit terlihat oleh query di bawah

    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or surat.jenis_surat not in ["eksternal", "internal"]:
        return

    id_yang_punya_anak = [
        row[0] for row in db.query(models.Disposisi.disposisi_asal_id)
        .filter(models.Disposisi.disposisi_asal_id.isnot(None))
        .distinct().all()
    ]

    query_leaf_aktif = db.query(models.Disposisi).filter(
        models.Disposisi.surat_id == surat_id,
        models.Disposisi.status == "diteruskan"
    )
    if id_yang_punya_anak:
        query_leaf_aktif = query_leaf_aktif.filter(models.Disposisi.id.notin_(id_yang_punya_anak))
    leaf_aktif = query_leaf_aktif.count()

    penugasan_aktif = db.query(models.Penugasan).filter(
        models.Penugasan.surat_id == surat_id,
        models.Penugasan.status == "proses"
    ).count()

    surat.status = "selesai" if (leaf_aktif == 0 and penugasan_aktif == 0) else "diproses"


def tandai_disposisi_jadi_tugas(surat: models.Surat, db: Session):
    """
    Jika `surat` adalah Surat Tugas (permohonan yang tertaut ke disposisi asal),
    tandai disposisi pemicunya 'diproses_jadi_tugas' lalu hitung ulang status surat aslinya.
    """
    if surat.jenis_surat != "permohonan" or not surat.disposisi_asal_id:
        return
    disposisi = db.query(models.Disposisi).filter(
        models.Disposisi.id == surat.disposisi_asal_id
    ).first()
    if disposisi and disposisi.status == "diteruskan":
        disposisi.status = "diproses_jadi_tugas"
        hitung_ulang_status_surat(disposisi.surat_id, db)


# --- 4e. Utas surat (rantai surat_asal_id) ---
def cari_surat_root(surat_id: int, db: Session) -> Optional[models.Surat]:
    """Telusuri surat_asal_id ke belakang sampai ketemu surat paling awal (akar utas)."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    while surat and surat.surat_asal_id:
        induk = db.query(models.Surat).filter(models.Surat.id == surat.surat_asal_id).first()
        if not induk:
            break
        surat = induk
    return surat


def ambil_semua_id_dalam_utas(root_id: int, db: Session) -> list:
    """Telusuri maju dari akar utas, kumpulkan id semua surat dalam rantai balasan."""
    hasil = [root_id]
    antrian = [root_id]
    while antrian:
        current_id = antrian.pop(0)
        for anak in db.query(models.Surat).filter(models.Surat.surat_asal_id == current_id).all():
            hasil.append(anak.id)
            antrian.append(anak.id)
    return hasil


# ==========================================================================
# 5. HELPER PDF (QR, inject nomor surat & TTD)
# ==========================================================================
BLOK_W  = 155   # lebar blok TTD (pt) — harus sama dengan FRAME_PT_W di index.html
QR_SIZE = 70    # ukuran QR (pt)
BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
                 "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def buat_qr_png(data: str) -> bytes:
    """QR Code (PNG) berisi `data`, dengan logo PPNP di tengah bila file logo ada."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if os.path.exists(PATH_LOGO):
        logo = Image.open(PATH_LOGO).convert("RGBA")
        ukuran_logo = int(qr_pil.size[0] * 0.22)
        logo = logo.resize((ukuran_logo, ukuran_logo))

        # Lingkaran putih di belakang logo (supaya QR tetap terbaca jelas)
        bg_size = ukuran_logo + 12
        bg = Image.new("RGBA", (bg_size, bg_size), (255, 255, 255, 0))
        ImageDraw.Draw(bg).ellipse((0, 0, bg_size, bg_size), fill=(255, 255, 255, 255))

        pos_bg   = ((qr_pil.size[0] - bg_size) // 2, (qr_pil.size[1] - bg_size) // 2)
        pos_logo = ((qr_pil.size[0] - ukuran_logo) // 2, (qr_pil.size[1] - ukuran_logo) // 2)

        qr_pil = qr_pil.convert("RGBA")
        qr_pil.paste(bg, pos_bg, bg)
        qr_pil.paste(logo, pos_logo, logo)
        qr_pil = qr_pil.convert("RGB")

    buffer = io.BytesIO()
    qr_pil.save(buffer, format="PNG")
    return buffer.getvalue()


def _simpan_pdf_di_tempat(doc, pdf_path: str):
    """Simpan dokumen ke file sementara dulu, baru timpa file asli."""
    path_sementara = pdf_path + ".tmp"
    doc.save(path_sementara, garbage=4, deflate=True)
    doc.close()
    os.replace(path_sementara, pdf_path)


def inject_nomor_ke_pdf(surat_id: int, nomor_surat: str, db: Session):
    """Sisipkan teks nomor surat ke semua titik pin yang ditentukan pembuat surat."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or not surat.file_upload:
        return

    pdf_path = f"uploads/{surat.file_upload}"
    if not os.path.exists(pdf_path):
        return

    titik_list = db.query(models.PosisiNomorSurat).filter(
        models.PosisiNomorSurat.surat_id == surat_id
    ).all()
    if not titik_list:
        return  # tidak ada pin nomor surat

    doc = pymupdf.open(pdf_path)
    for titik in titik_list:
        hal_index = max(0, min((titik.posisi_hal or 1) - 1, len(doc) - 1))
        page = doc[hal_index]
        x0 = (titik.posisi_x / 100.0) * page.rect.width
        y0 = (titik.posisi_y / 100.0) * page.rect.height

        page.insert_text(
            (x0, y0), f"{titik.prefix_teks or ''}{nomor_surat}",
            fontsize=11, fontname="tiro", color=(0, 0, 0)   # 'tiro' = Times Roman bawaan PyMuPDF
        )

    _simpan_pdf_di_tempat(doc, pdf_path)


def inject_ttd_ke_pdf(surat_id: int, db: Session) -> Optional[str]:
    """Sisipkan blok TTD (QR + nama + NIP + tanggal) ke PDF. Return path file, atau None."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or not surat.file_upload:
        return None

    pdf_path = f"uploads/{surat.file_upload}"
    if not os.path.exists(pdf_path):
        return None

    persetujuan_list = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id           == surat_id,
        models.PersetujuanSurat.status_persetujuan == "disetujui",
        models.PersetujuanSurat.posisi_x.isnot(None)
    ).order_by(models.PersetujuanSurat.urutan).all()

    # Surat permohonan: hanya TTE Utama (Direktur) yang dicetak.
    # Paraf level di bawahnya hanya proses internal, tidak dicetak sebagai stempel.
    if surat.jenis_surat == "permohonan":
        persetujuan_list = [p for p in persetujuan_list if p.jenis_ttd == "TTE Utama"]

    doc = pymupdf.open(pdf_path)

    # QR berisi URL verifikasi publik (sama untuk semua blok TTD pada surat ini)
    sig = buat_signature(surat.kode_pelacakan or "")
    qr_png = buat_qr_png(f"{BASE_URL}/verifikasi?kode={surat.kode_pelacakan}&sig={sig}")

    for p in persetujuan_list:
        pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_penyetuju).first()

        hal_index = max(0, min((p.posisi_hal or 1) - 1, len(doc) - 1))
        page = doc[hal_index]
        x0 = (p.posisi_x / 100.0) * page.rect.width
        y0 = (p.posisi_y / 100.0) * page.rect.height

        # Label (contoh: "Mengetahui,")
        if p.label_ttd:
            page.insert_text((x0, y0 + 10), p.label_ttd, fontsize=8, fontname="helv", color=(0, 0, 0))

        # QR Code
        page.insert_image(
            pymupdf.Rect(x0, y0 + 14, x0 + QR_SIZE, y0 + 14 + QR_SIZE),
            stream=qr_png
        )

        # Garis bawah
        y_garis = y0 + 14 + QR_SIZE + 6
        page.draw_line(
            pymupdf.Point(x0, y_garis), pymupdf.Point(x0 + BLOK_W, y_garis),
            color=(0, 0, 0), width=0.5
        )

        # Nama pejabat (bold), NIP, tanggal & status SAH
        nama = (pegawai.nama if pegawai else "—")[:40]
        page.insert_text((x0, y_garis + 11), nama, fontsize=8, fontname="hebo", color=(0, 0, 0))
        page.insert_text((x0, y_garis + 22), f"NIP. {p.nip_penyetuju}", fontsize=7.5, fontname="helv", color=(0, 0, 0))

        if p.tanggal_proses:
            tgl = p.tanggal_proses
            tgl_str = f"{tgl.day} {BULAN_SINGKAT[tgl.month - 1]} {tgl.year} {tgl.hour:02d}:{tgl.minute:02d}"
            page.insert_text(
                (x0, y_garis + 33), f"SAH — TTE: {tgl_str}",
                fontsize=6.5, fontname="helv", color=(0.1, 0.5, 0.1)
            )

    _simpan_pdf_di_tempat(doc, pdf_path)
    return surat.file_upload


# ==========================================================================
# 6. ROUTE HALAMAN HTML
# ==========================================================================
@app.get("/", response_class=HTMLResponse)
def halaman_login(request: Request):
    return templates.TemplateResponse(request=request, name="auth/login.html")


@app.get("/surat", response_class=HTMLResponse)
def halaman_manajemen_surat(request: Request):
    return templates.TemplateResponse(request=request, name="admin/index.html")


@app.get("/profil", response_class=HTMLResponse)
def halaman_profil(request: Request):
    return templates.TemplateResponse(request=request, name="admin/profil.html")


@app.get("/inbox", response_class=HTMLResponse)
def halaman_inbox(request: Request):
    return templates.TemplateResponse(request=request, name="admin/inbox.html")

@app.get("/dashboard", response_class=HTMLResponse)
def halaman_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="admin/dashboard.html")

@app.get("/verifikasi", response_class=HTMLResponse)
def halaman_verifikasi(request: Request, kode: str = None, sig: str = None, db: Session = Depends(get_db)):
    """Halaman publik untuk verifikasi keabsahan surat via QR Code."""
    konteks = {"request": request, "valid": False, "pesan": ""}

    def tampil(pesan: str):
        konteks["pesan"] = pesan
        return templates.TemplateResponse(request=request, name="auth/verifikasi.html", context=konteks)

    if not kode or not sig:
        return tampil("Parameter tidak lengkap.")
    if not cek_signature(kode, sig):
        return tampil("Kode verifikasi tidak valid atau telah dimanipulasi.")

    surat = db.query(models.Surat).filter(models.Surat.kode_pelacakan == kode).first()
    if not surat:
        return tampil("Surat dengan kode ini tidak ditemukan dalam sistem.")
    if surat.status != "selesai":
        return tampil("Surat ini belum selesai proses tanda tangan, sehingga belum sah.")

    persetujuan_final = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id           == surat.id,
        models.PersetujuanSurat.status_persetujuan == "disetujui",
        models.PersetujuanSurat.jenis_ttd          == "TTE Utama"
    ).order_by(models.PersetujuanSurat.urutan.desc()).first()

    pejabat = None
    if persetujuan_final:
        pejabat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == persetujuan_final.nip_penyetuju
        ).first()

    unit_pejabat = None
    jabatan_final = None
    if pejabat:
        unit_pejabat = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == pejabat.kode_unit
        ).first()
        jabatan_final = (
            unit_pejabat.jabatan_surat
            if unit_pejabat and unit_pejabat.jabatan_surat
            else pejabat.jabatan
        )

    konteks.update({
        "valid"        : True,
        "kode"         : surat.kode_pelacakan,
        "perihal"      : surat.perihal,
        "jenis_surat"  : surat.jenis_surat.upper(),
        "nomor_surat"  : surat.nomor_surat or "Belum ada nomor",
        "nama_pejabat" : pejabat.nama if pejabat else "—",
        "jabatan"      : jabatan_final or "—",
        "unit_pejabat" : unit_pejabat.nama_unit if unit_pejabat else "—",
        "tanggal_tte"  : format_tanggal_indonesia(persetujuan_final.tanggal_proses) if persetujuan_final else "—",
        "waktu_akses"  : format_tanggal_indonesia(datetime.utcnow()),
    })
    return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)


# ==========================================================================
# 7. API AUTENTIKASI, PROFIL & TTE
# ==========================================================================
@app.post("/api/login")
def proses_login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip).first()
    if not user or user.password != req.password:
        raise HTTPException(status_code=401, detail="NIP atau Password salah!")
    return {
        "nip"      : user.nip,
        "nama"     : user.nama,
        "jabatan"  : user.jabatan,
        "role_ppnp": user.role_ppnp,
        "kode_unit": user.kode_unit
    }


@app.get("/api/profil")
def get_profil(nip: str, db: Session = Depends(get_db)):
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        raise HTTPException(status_code=404, detail="Pegawai tidak ditemukan")

    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()

    return {
        "nip"      : pegawai.nip,
        "nama"     : pegawai.nama,
        "jabatan"  : pegawai.jabatan,
        "role_ppnp": pegawai.role_ppnp,
        "kode_unit": pegawai.kode_unit,
        "nama_unit": unit.nama_unit if unit else "—",
        "tte_aktif": pegawai.tte_aktif,
        "file_ttd" : pegawai.file_ttd,
    }



# ==========================================================================
# 8. JALANKAN SERVER (python main.py)
# ==========================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)


@app.post("/api/upload/ttd")
def upload_ttd(
    file: UploadFile = File(...),
    nip : str        = Form(...),
    db  : Session    = Depends(get_db)
):
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["png", "jpg", "jpeg"]:
        raise HTTPException(status_code=400, detail="Format file harus PNG atau JPG")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    nama_bersih = re.sub(r"[^a-zA-Z0-9]+", "_", pegawai.nama.strip()) if pegawai else "unknown"
    nip_bersih  = re.sub(r"[^a-zA-Z0-9]", "", nip)

    nama_file = f"ttd_{nama_bersih}_{nip_bersih}.{ext}"
    simpan_file_upload(file, f"ttd/{nama_file}")

    return {"nama_file": nama_file, "url": f"/uploads/ttd/{nama_file}"}


@app.post("/api/registrasi-ttd")
def registrasi_ttd(
    nip     : str     = Form(...),
    pin     : str     = Form(...),
    file_ttd: str     = Form(...),
    db      : Session = Depends(get_db)
):
    if not pin.isdigit() or len(pin) != 6:
        raise HTTPException(status_code=400, detail="PIN harus 6 digit angka")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        raise HTTPException(status_code=404, detail="Pegawai tidak ditemukan")

    pegawai.pin_tte   = hash_pin(pin)
    pegawai.file_ttd  = file_ttd
    pegawai.tte_aktif = True
    db.commit()
    return {"message": "Registrasi TTD berhasil! TTE Anda sudah aktif."}


@app.post("/api/ganti-pin")
def ganti_pin(
    nip     : str     = Form(...),
    pin_lama: str     = Form(...),
    pin_baru: str     = Form(...),
    db      : Session = Depends(get_db)
):
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai or not pegawai.pin_tte:
        raise HTTPException(status_code=404, detail="TTE belum terdaftar")
    if not verify_pin(pin_lama, pegawai.pin_tte):
        raise HTTPException(status_code=401, detail="PIN lama tidak sesuai")
    if not pin_baru.isdigit() or len(pin_baru) != 6:
        raise HTTPException(status_code=400, detail="PIN baru harus 6 digit angka")

    pegawai.pin_tte = hash_pin(pin_baru)
    db.commit()
    return {"message": "PIN berhasil diubah"}


# ==========================================================================
# 8. API DATA ORGANISASI
# ==========================================================================
@app.get("/api/pejabat")
def get_pejabat(kode_unit: str = None, db: Session = Depends(get_db)):
    if not kode_unit:
        return []

    pejabat = db.query(models.Pegawai).filter(
        models.Pegawai.kode_unit == kode_unit,
        models.Pegawai.role_ppnp != "staf"
    ).all()
    unit_info = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()

    return [
        {
            "nip"      : p.nip,
            "nama"     : p.nama,
            "jabatan"  : p.jabatan,
            "nama_unit": unit_info.nama_unit if unit_info else "-"
        }
        for p in pejabat
    ]


@app.get("/api/unit-info")
def get_unit_info(kode_unit: str, db: Session = Depends(get_db)):
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    if not unit:
        raise HTTPException(404, "Unit tidak ditemukan")

    return {
        "kode_unit"        : unit.kode_unit,
        "nama_unit"        : unit.nama_unit,
        "level"            : unit.level,
        "kode_induk"       : unit.kode_induk,
        "nama_induk"       : unit.nama_induk,
        "punya_kop_sendiri": unit.punya_kop_sendiri,
        "jabatan_surat"    : unit.jabatan_surat,
    }


@app.get("/api/direktur-info")
def get_direktur_info(db: Session = Depends(get_db)):
    """NIP & nama Direktur aktif — dipakai untuk pre-fill form surat lanjutan."""
    direktur_unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.level == 1
    ).first()
    if not direktur_unit or not direktur_unit.nip_pimpinan:
        raise HTTPException(404, "Data Direktur tidak ditemukan")
    return {"nip": direktur_unit.nip_pimpinan, "nama": direktur_unit.nama_pimpinan}


@app.get("/api/unit-tujuan")
def get_unit_tujuan(kode_unit: str, db: Session = Depends(get_db)):
    """Daftar pimpinan unit yang bisa dijadikan tujuan surat internal."""
    unit_pengirim = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    punya_kop = unit_pengirim.punya_kop_sendiri if unit_pengirim else False

    semua_unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.nip_pimpinan.isnot(None),
        models.UnitOrganisasi.kode_unit != kode_unit
    ).order_by(models.UnitOrganisasi.level).all()

    result = []
    for u in semua_unit:
        if not punya_kop and u.level <= 2:
            continue
        result.append({
            "kode_unit"    : u.kode_unit,
            "nama_unit"    : u.nama_unit,
            "level"        : u.level,
            "nip_pimpinan" : u.nip_pimpinan,
            "nama_pimpinan": u.nama_pimpinan,
        })
    return result


@app.get("/api/kop")
def cek_kop(kode_unit: str):
    if not re.match(r"^[A-Za-z0-9\.]+$", kode_unit):
        raise HTTPException(status_code=400, detail="Kode unit tidak valid")

    nama_file = MAPPING_KOP.get(kode_unit)
    if not nama_file or not os.path.exists(f"uploads/assets/{nama_file}"):
        return {"ada": False, "url": None}

    return {"ada": True, "url": f"/uploads/assets/{quote(nama_file)}"}


@app.get("/api/kandidat-penerima")
def get_kandidat_penerima(kode_unit: str, mode: str, nip_pemberi: str = None, db: Session = Depends(get_db)):
    """
    Kandidat penerima untuk disposisi / penugasan, dikelompokkan per level.
    - mode "disposisi": pejabat (non-staf) terdekat ke bawah; jika sebuah unit tidak
      punya pejabat, penelusuran lanjut ke unit di bawahnya.
    - mode "penugasan": semua pejabat & staf di seluruh turunan, plus staf di unit
      sendiri dan pemberi tugas sendiri.
    """
    unit_pengirim = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    if not unit_pengirim:
        raise HTTPException(404, "Unit tidak ditemukan")

    sertakan_staf = (mode == "penugasan")

    def ambil_pejabat_unit(u):
        query = db.query(models.Pegawai).filter(models.Pegawai.kode_unit == u.kode_unit)
        if not sertakan_staf:
            query = query.filter(models.Pegawai.role_ppnp != "staf")
        return query.all()

    def ambil_anak_langsung(kode_unit_x):
        # Unit yang punya induk tambahan hanya muncul lewat jalur induk tambahannya
        semua_kode_anak_tambahan = {
            row.kode_unit_anak for row in db.query(models.UnitIndukTambahan).all()
        }

        normal = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_induk == kode_unit_x
        ).all()
        normal = [u for u in normal if u.kode_unit not in semua_kode_anak_tambahan]

        tambahan_units = []
        for row in db.query(models.UnitIndukTambahan).filter(
            models.UnitIndukTambahan.kode_induk_tambahan == kode_unit_x
        ).all():
            u = db.query(models.UnitOrganisasi).filter(
                models.UnitOrganisasi.kode_unit == row.kode_unit_anak
            ).first()
            if u:
                tambahan_units.append(u)

        return normal + tambahan_units

    hasil_flat = []
    dikunjungi = set()

    def tambah_pejabat(u, daftar):
        for p in daftar:
            hasil_flat.append({
                "level": u.level, "nip": p.nip, "nama": p.nama,
                "jabatan": p.jabatan, "nama_unit": u.nama_unit,
            })

    if mode == "disposisi":
        def proses_cascade(unit_list):
            for u in unit_list:
                if u.kode_unit in dikunjungi:
                    continue
                dikunjungi.add(u.kode_unit)

                pejabat = ambil_pejabat_unit(u)
                if pejabat:
                    tambah_pejabat(u, pejabat)
                else:
                    proses_cascade(ambil_anak_langsung(u.kode_unit))

        proses_cascade(ambil_anak_langsung(kode_unit))

    else:  # mode == "penugasan"
        # Staf yang menempel LANGSUNG ke unit pengirim (staf tidak punya unit terpisah)
        for p in db.query(models.Pegawai).filter(
            models.Pegawai.kode_unit == kode_unit,
            models.Pegawai.role_ppnp == "staf"
        ).all():
            hasil_flat.append({
                "level": unit_pengirim.level + 1,
                "nip": p.nip, "nama": p.nama,
                "jabatan": p.jabatan, "nama_unit": unit_pengirim.nama_unit,
            })

        # Pemberi tugas sendiri — biasanya ikut terlibat di tim
        if nip_pemberi:
            pemberi = db.query(models.Pegawai).filter(models.Pegawai.nip == nip_pemberi).first()
            if pemberi and pemberi.kode_unit == kode_unit:
                hasil_flat.append({
                    "level": unit_pengirim.level,
                    "nip": pemberi.nip, "nama": f"{pemberi.nama} (Saya Sendiri)",
                    "jabatan": pemberi.jabatan, "nama_unit": unit_pengirim.nama_unit,
                })

        def proses_semua_turunan(kode_unit_x):
            for u in ambil_anak_langsung(kode_unit_x):
                if u.kode_unit in dikunjungi:
                    continue
                dikunjungi.add(u.kode_unit)
                tambah_pejabat(u, ambil_pejabat_unit(u))
                proses_semua_turunan(u.kode_unit)

        proses_semua_turunan(kode_unit)

    per_level = {}
    for item in hasil_flat:
        per_level.setdefault(item["level"], []).append(item)

    return [
        {"level": lvl, "label": f"Level {lvl}", "pejabat": per_level[lvl]}
        for lvl in sorted(per_level.keys())
    ]


# ==========================================================================
# 9. API SURAT
# ==========================================================================

@app.post("/api/surat")
def simpan_surat(req: SuratCreate, db: Session = Depends(get_db)):
    pastikan_boleh_kelola_surat(req.nip_pembuat, db)
    validasi_surat_tugas(req, db)
    rapat = req.jenis_surat == "permohonan" and req.jenis_tugas == "rapat"
    if req.nomor_surat and req.nomor_surat.strip():
        sudah_ada = db.query(models.Surat).filter(
            models.Surat.nomor_surat == req.nomor_surat.strip()
        ).first()
        if sudah_ada:
            raise HTTPException(400, f"Nomor surat '{req.nomor_surat}' sudah digunakan pada surat lain!")

    kode_resi  = generate_kode()
    nip_ttd    = req.nip_penandatangan if req.nip_penandatangan and req.nip_penandatangan.strip() else None
    nip_tujuan = req.nip_tujuan if req.nip_tujuan and req.nip_tujuan.strip() else None

    db.add(models.Surat(
        jenis_surat       = req.jenis_surat,
        nomor_surat       = req.nomor_surat,
        perihal           = req.perihal,
        asal_surat        = req.asal_surat,
        isi_surat         = req.isi_surat,
        file_surat        = req.file_surat,
        kode_pelacakan    = kode_resi,
        nip_pembuat       = req.nip_pembuat,
        nip_penandatangan = nip_ttd,
        nip_tujuan        = nip_tujuan,
        tujuan_tambahan   = req.tujuan_tambahan,
        tanggal_surat     = req.tanggal_surat,
        tanggal_diterima  = req.tanggal_diterima,
        status            = "draft",
        tipe_input        = req.tipe_input or "editor",
        file_upload       = req.file_upload,
        surat_asal_id     = req.surat_asal_id,
        disposisi_asal_id = req.disposisi_asal_id,
        penugasan_asal_id = req.penugasan_asal_id,
        tanggal_tugas_mulai   = req.tanggal_tugas_mulai   if req.jenis_surat == "permohonan" else None,
        tanggal_tugas_selesai = req.tanggal_tugas_selesai if req.jenis_surat == "permohonan" else None,
        jenis_tugas           = req.jenis_tugas  if req.jenis_surat == "permohonan" else None,
        tempat_tugas          = req.tempat_tugas if req.jenis_surat == "permohonan" else None,
        jam_mulai             = req.jam_mulai.strip() if rapat else None,
        jam_selesai           = ((req.jam_selesai or "").strip() or None) if rapat else None,
        agenda_rapat          = req.agenda_rapat.strip() if rapat else None,
        nip_pimpinan_rapat    = req.nip_pimpinan_rapat if rapat else None,
    ))
    db.flush()
    surat_baru = db.query(models.Surat).filter(models.Surat.kode_pelacakan == kode_resi).first()
    simpan_penerima_surat_tugas(surat_baru, req.pegawai_ditugaskan, db,
                                req.nip_notulen if rapat else None)
    db.commit()
    return {"message": "Surat berhasil disimpan", "kode_pelacakan": kode_resi,
            "surat_id": surat_baru.id}

@app.get("/api/surat")
def get_daftar_surat(nip: str, status: str = "draft", db: Session = Depends(get_db)):
    """
    Daftar surat buatan `nip`. status="terkirim" -> semua yang bukan draft.
    Setiap item ditambah `nama_penerima` & `jabatan_penerima`:
    - Internal  : pegawai tujuan (nip_tujuan)
    - Eksternal : Direktur
    - Permohonan: tidak punya penerima -> None
    """
    query = db.query(models.Surat).filter(models.Surat.nip_pembuat == nip)
    if status == "terkirim":
        query = query.filter(models.Surat.status.notin_(["draft"]))
    else:
        query = query.filter(models.Surat.status == status)
    daftar = query.order_by(models.Surat.id.desc()).all()

    direktur_unit = None  # dicari sekali saja, hanya kalau ada surat eksternal
    hasil = []
    for s in daftar:
        item = {c.name: getattr(s, c.name) for c in s.__table__.columns}
        nama_penerima, jabatan_penerima = None, None

        if s.jenis_surat == "eksternal":
            if direktur_unit is None:
                direktur_unit = db.query(models.UnitOrganisasi).filter(
                    models.UnitOrganisasi.level == 1
                ).first()
            nama_penerima    = (direktur_unit.nama_pimpinan if direktur_unit and direktur_unit.nama_pimpinan
                                else "Direktur")
            jabatan_penerima = "Direktur"
        elif s.nip_tujuan:
            penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == s.nip_tujuan).first()
            if penerima:
                nama_penerima    = penerima.nama
                jabatan_penerima = penerima.jabatan

        item["nama_penerima"]    = nama_penerima
        item["jabatan_penerima"] = jabatan_penerima
        hasil.append(item)

    return hasil

@app.get("/api/surat/{surat_id}")
def get_detail_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(status_code=404, detail="Surat tidak ditemukan")

    pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()

    # --- Penandatangan ---
    penandatangan = None
    if surat.nip_penandatangan:
        penandatangan = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_penandatangan
        ).first()

    if penandatangan is None and surat.jenis_surat == "permohonan":
        tte_utama = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id  == surat.id,
            models.PersetujuanSurat.jenis_ttd == "TTE Utama"
        ).first()
        if tte_utama:
            penandatangan = db.query(models.Pegawai).filter(
                models.Pegawai.nip == tte_utama.nip_penyetuju
            ).first()

    def jabatan_formal(pegawai):
        """Jabatan resmi untuk surat (unit.jabatan_surat), fallback ke jabatan pegawai."""
        if not pegawai:
            return None
        unit = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == pegawai.kode_unit
        ).first()
        return unit.jabatan_surat if unit and unit.jabatan_surat else pegawai.jabatan

    # --- Tujuan ---
    tujuan = None
    if surat.nip_tujuan:
        tujuan = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_tujuan).first()

    # --- Chain persetujuan (untuk Tracking, bukan untuk dicetak di badan surat) ---
    chain_persetujuan = []
    for p in db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat.id
    ).order_by(models.PersetujuanSurat.urutan).all():
        pejabat_p = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_penyetuju).first()
        chain_persetujuan.append({
            "urutan"         : p.urutan,
            "jenis_ttd"      : p.jenis_ttd,
            "status"         : p.status_persetujuan,
            "nama_pejabat"   : pejabat_p.nama    if pejabat_p else "—",
            "jabatan_pejabat": pejabat_p.jabatan if pejabat_p else "—",
            "tanggal_proses" : str(p.tanggal_proses) if p.tanggal_proses else None,
        })

    # --- Status TTE selesai ---
    tte_selesai            = False
    file_ttd_penandatangan = None
    tanggal_tte            = None
    if surat.status == "selesai":
        persetujuan_final = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "disetujui"
        ).order_by(models.PersetujuanSurat.urutan.desc()).first()

        if persetujuan_final:
            tte_selesai = True
            tanggal_tte = str(persetujuan_final.tanggal_proses) if persetujuan_final.tanggal_proses else None
            pejabat_ttd = db.query(models.Pegawai).filter(
                models.Pegawai.nip == persetujuan_final.nip_penyetuju
            ).first()
            if pejabat_ttd and pejabat_ttd.file_ttd:
                file_ttd_penandatangan = pejabat_ttd.file_ttd

    # --- Surat asal (lampiran) ---
    surat_asal_info = None
    if surat.surat_asal_id:
        s_asal = db.query(models.Surat).filter(models.Surat.id == surat.surat_asal_id).first()
        if s_asal:
            pembuat_asal = db.query(models.Pegawai).filter(models.Pegawai.nip == s_asal.nip_pembuat).first()
            surat_asal_info = {
                "id"          : s_asal.id,
                "perihal"     : s_asal.perihal,
                "jenis_surat" : s_asal.jenis_surat,
                "nama_pembuat": pembuat_asal.nama if pembuat_asal else "—",
            }

    kode_unit_kop = "D001" if surat.jenis_surat == "permohonan" else (pembuat.kode_unit if pembuat else None)

    return {
        "id"                    : surat.id,
        "jenis_surat"           : surat.jenis_surat,
        "nomor_surat"           : surat.nomor_surat,
        "perihal"               : surat.perihal,
        "asal_surat"            : surat.asal_surat,
        "isi_surat"             : surat.isi_surat,
        "file_surat"            : surat.file_surat,
        "kode_pelacakan"        : surat.kode_pelacakan,
        "status"                : surat.status,
        "tanggal_dibuat"        : str(surat.tanggal_dibuat),
        "tanggal_surat"         : surat.tanggal_surat,
        "tanggal_diterima"      : surat.tanggal_diterima,
        "nip_pembuat"           : surat.nip_pembuat,
        "nama_pembuat"          : pembuat.nama      if pembuat else "—",
        "jabatan_pembuat"       : pembuat.jabatan   if pembuat else "—",
        "kode_unit_pembuat"     : pembuat.kode_unit if pembuat else None,
        "kode_unit_kop"         : kode_unit_kop,
        "nama_penandatangan"    : penandatangan.nama if penandatangan else None,
        "jabatan_penandatangan" : jabatan_formal(penandatangan),
        "nip_penandatangan"     : penandatangan.nip  if penandatangan else None,
        "nama_tujuan"           : tujuan.nama if tujuan else None,
        "jabatan_tujuan"        : jabatan_formal(tujuan),
        "tujuan_tambahan"       : surat.tujuan_tambahan,
        "tte_selesai"           : tte_selesai,
        "file_ttd_penandatangan": file_ttd_penandatangan,
        "tanggal_tte"           : tanggal_tte,
        "chain_persetujuan"     : chain_persetujuan,
        "tipe_input"            : surat.tipe_input,
        "file_upload"           : surat.file_upload,
        "file_final"            : surat.file_final,
        "url_verifikasi"        : (
            f"{surat.kode_pelacakan}|{buat_signature(surat.kode_pelacakan)}"
            if surat.kode_pelacakan else None
        ),
        "surat_asal"            : surat_asal_info,
        "tanggal_tugas_mulai"   : surat.tanggal_tugas_mulai,
        "tanggal_tugas_selesai" : surat.tanggal_tugas_selesai,
        "jenis_tugas"           : surat.jenis_tugas,
        "tempat_tugas"          : surat.tempat_tugas,
        "pegawai_ditugaskan"    : daftar_pegawai_ditugaskan(surat.id, db),
        "jam_mulai"             : surat.jam_mulai,
        "jam_selesai"           : surat.jam_selesai,
        "agenda_rapat"          : surat.agenda_rapat,
        "nip_pimpinan_rapat"    : surat.nip_pimpinan_rapat,
    }


@app.put("/api/surat/{surat_id}")
def update_surat(surat_id: int, req: SuratCreate, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa diedit")
    pastikan_boleh_kelola_surat(surat.nip_pembuat, db)

    # Cegah nomor surat ganda (kecuali dengan dirinya sendiri)
    if req.nomor_surat and req.nomor_surat.strip():
        sudah_ada = db.query(models.Surat).filter(
            models.Surat.nomor_surat == req.nomor_surat.strip(),
            models.Surat.id != surat_id
        ).first()
        if sudah_ada:
            raise HTTPException(400, f"Nomor surat '{req.nomor_surat}' sudah digunakan pada surat lain!")

    surat.nomor_surat       = req.nomor_surat
    surat.perihal           = req.perihal
    surat.asal_surat        = req.asal_surat
    surat.tanggal_surat     = req.tanggal_surat
    surat.tanggal_diterima  = req.tanggal_diterima
    surat.isi_surat         = req.isi_surat
    surat.nip_penandatangan = req.nip_penandatangan
    surat.nip_tujuan        = req.nip_tujuan
    surat.tujuan_tambahan   = req.tujuan_tambahan
    validasi_surat_tugas(req, db)
    if surat.jenis_surat == "permohonan":
        surat.tanggal_tugas_mulai   = req.tanggal_tugas_mulai
        surat.tanggal_tugas_selesai = req.tanggal_tugas_selesai
        surat.jenis_tugas           = req.jenis_tugas
        surat.tempat_tugas          = req.tempat_tugas
        rapat = req.jenis_tugas == "rapat"
        surat.jam_mulai             = req.jam_mulai.strip() if rapat else None
        surat.jam_selesai           = ((req.jam_selesai or "").strip() or None) if rapat else None
        surat.agenda_rapat          = req.agenda_rapat.strip() if rapat else None
        surat.nip_pimpinan_rapat    = req.nip_pimpinan_rapat if rapat else None
        db.query(models.PenerimaSuratTugas).filter(
            models.PenerimaSuratTugas.surat_id == surat.id
        ).delete()
        simpan_penerima_surat_tugas(surat, req.pegawai_ditugaskan, db,
                                    req.nip_notulen if rapat else None)

    # File baru menggantikan file lama (file lama dihapus dari disk)
    if req.file_surat and req.file_surat != surat.file_surat:
        hapus_file_upload(surat.file_surat)
        surat.file_surat = req.file_surat

    if req.file_upload and req.file_upload != surat.file_upload:
        hapus_file_upload(surat.file_upload)
        surat.file_upload = req.file_upload

    surat.tipe_input = req.tipe_input or surat.tipe_input

    db.commit()
    return {"message": "Draft berhasil diperbarui"}


@app.delete("/api/surat/{surat_id}")
def hapus_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa dihapus")

    db.query(models.PenerimaSuratTugas).filter(
        models.PenerimaSuratTugas.surat_id == surat.id
    ).delete()

    for nama_file in [surat.file_surat, surat.file_upload, surat.file_final]:
        hapus_file_upload(nama_file)

    db.delete(surat)
    db.commit()
    return {"message": "Draft berhasil dihapus"}


# --- 9b. Kirim & tracking ---
@app.post("/api/surat/{surat_id}/kirim")
def kirim_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa dikirim")
    pastikan_boleh_kelola_surat(surat.nip_pembuat, db)

    pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pembuat.kode_unit
    ).first() if pembuat else None
    punya_kop = unit.punya_kop_sendiri if unit else False

    # ── UPLOAD PDF: chain sudah dibuat lewat "posisi-ttd" ──
    if surat.tipe_input == "upload":
        existing_chain = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id == surat.id
        ).all()
        if not existing_chain:
            raise HTTPException(400, "Belum ada penandatangan. Klik tombol 'Atur Posisi TTD' terlebih dahulu.")

        if surat.jenis_surat == "permohonan":
            nomor_ada = db.query(models.PosisiNomorSurat).filter(
                models.PosisiNomorSurat.surat_id == surat.id
            ).first()
            if not nomor_ada:
                raise HTTPException(400, "Pin nomor surat belum dipasang. Klik 'Atur Posisi TTD' lalu pasang pin Nomor Surat terlebih dahulu.")

        tandai_disposisi_jadi_tugas(surat, db)
        surat.status = "menunggu"
        db.commit()
        return {
            "message"       : "Surat berhasil dikirim untuk ditandatangani",
            "chain_approval": [
                {"urutan": p.urutan, "nip": p.nip_penyetuju, "jenis": p.jenis_ttd}
                for p in existing_chain
            ]
        }

    # ── EKSTERNAL: masuk ke Direktur, tanpa TTE ──
    if surat.jenis_surat == "eksternal":
        surat.status = "masuk"
        surat.catatan_kembali = None
        db.commit()
        return {"message": "Surat masuk berhasil diteruskan ke Direktur"}

    # ── Susun chain persetujuan: list of (nip, jenis_ttd, urutan) ──
    chain = []

    if surat.jenis_surat == "internal":
        # Pimpinan unit yang dipilih langsung TTE
        if not surat.nip_penandatangan:
            raise HTTPException(400, "Penandatangan surat internal tidak ditemukan")
        chain.append((surat.nip_penandatangan, "TTE Utama", 1))

    elif surat.jenis_surat == "permohonan":
        if punya_kop:
            # Unit punya kop sendiri: langsung TTE Direktur
            direktur_unit = db.query(models.UnitOrganisasi).filter(
                models.UnitOrganisasi.level == 1
            ).first()
            if not direktur_unit or not direktur_unit.nip_pimpinan:
                raise HTTPException(400, "Data Direktur tidak ditemukan di database")
            chain.append((direktur_unit.nip_pimpinan, "TTE Utama", 1))
        else:
            # Paraf berjenjang mendaki hierarki unit sampai TTE Utama (Direktur)
            urutan    = 1
            kode_skrg = pembuat.kode_unit
            while kode_skrg:
                unit_skrg = db.query(models.UnitOrganisasi).filter(
                    models.UnitOrganisasi.kode_unit == kode_skrg
                ).first()
                if not unit_skrg:
                    break

                pejabat = db.query(models.Pegawai).filter(
                    models.Pegawai.kode_unit == kode_skrg,
                    models.Pegawai.role_ppnp != "staf",
                    models.Pegawai.nip       != surat.nip_pembuat
                ).first()
                if pejabat:
                    jenis = "TTE Utama" if unit_skrg.level == 1 else jenis_paraf_unit(unit_skrg)
                    chain.append((pejabat.nip, jenis, urutan))
                    urutan += 1

                kode_skrg = unit_skrg.kode_induk
    else:
        raise HTTPException(400, f"Jenis surat '{surat.jenis_surat}' tidak dikenali")

    if not chain:
        raise HTTPException(400, "Rantai persetujuan tidak bisa ditentukan")

    tandai_disposisi_jadi_tugas(surat, db)

    for nip_p, jenis_ttd, urutan in chain:
        db.add(models.PersetujuanSurat(
            surat_id           = surat.id,
            nip_penyetuju      = nip_p,
            jenis_ttd          = jenis_ttd,
            urutan             = urutan,
            status_persetujuan = "menunggu"
        ))

    surat.status = "menunggu"
    db.commit()

    return {
        "message"       : "Surat berhasil dikirim",
        "chain_approval": [{"urutan": u, "nip": n, "jenis": j} for n, j, u in chain]
    }


@app.get("/api/surat/{surat_id}/tracking")
def get_tracking_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")

    pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()

    chain = []
    for r in db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat_id
    ).order_by(models.PersetujuanSurat.urutan).all():
        pejabat = db.query(models.Pegawai).filter(models.Pegawai.nip == r.nip_penyetuju).first()
        chain.append({
            "urutan"         : r.urutan,
            "jenis_ttd"      : r.jenis_ttd,
            "status"         : r.status_persetujuan,
            "nama_pejabat"   : pejabat.nama    if pejabat else "—",
            "jabatan_pejabat": pejabat.jabatan if pejabat else "—",
            "catatan"        : r.catatan,
            "tanggal_proses" : str(r.tanggal_proses) if r.tanggal_proses else None,
        })

    tujuan = None
    if surat.nip_tujuan:
        p = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_tujuan).first()
        if p:
            tujuan = {"nama": p.nama, "jabatan": p.jabatan}

    return {
        "surat_id"       : surat.id,
        "perihal"        : surat.perihal,
        "status"         : surat.status,
        "tanggal_dibuat" : str(surat.tanggal_dibuat),
        "nama_pembuat"   : pembuat.nama    if pembuat else "—",
        "jabatan_pembuat": pembuat.jabatan if pembuat else "—",
        "tujuan"         : tujuan,
        "chain"          : chain,
    }


# --- 9c. Upload file ---
@app.post("/api/upload/surat")
def upload_file(
    file       : UploadFile = File(...),
    nomor_surat: str        = Form(default=""),
    asal_surat : str        = Form(default=""),
    perihal    : str        = Form(default="")
):
    """Upload scan surat eksternal."""
    ekstensi = file.filename.split(".")[-1]
    folder_relatif, nama_file = buat_path_surat("eksternal", perihal, ekstensi)
    path_relatif_db = f"{folder_relatif}/{nama_file}"
    simpan_file_upload(file, path_relatif_db)
    return {"nama_file": path_relatif_db}


@app.post("/api/upload/dokumen")
def upload_dokumen(
    file       : UploadFile = File(...),
    jenis_surat: str        = Form(...),
    perihal    : str        = Form(default="")
):
    """Upload dokumen PDF untuk surat tipe upload."""
    ext = file.filename.split(".")[-1].lower()
    if ext != "pdf":
        raise HTTPException(400, "Hanya file PDF yang diizinkan")

    folder_relatif, nama_file = buat_path_surat(jenis_surat, perihal, ext)
    path_relatif_db = f"{folder_relatif}/{nama_file}"
    simpan_file_upload(file, path_relatif_db)
    return {"nama_file": path_relatif_db}


@app.post("/api/upload/batalkan")
def batalkan_upload(req: BatalkanUploadRequest):
    """Hapus file yang sudah terupload tapi gagal disimpan sebagai surat (rollback manual)."""
    path_fisik = f"uploads/{req.nama_file}"
    if os.path.exists(path_fisik):
        try:
            os.remove(path_fisik)
            return {"message": "File berhasil dibatalkan/dihapus"}
        except Exception as e:
            return {"message": f"Gagal hapus: {e}"}
    return {"message": "File tidak ditemukan (mungkin sudah terhapus)"}


# --- 9d. Posisi TTD & nomor surat (surat upload PDF) ---
@app.post("/api/surat/{surat_id}/posisi-nomor")
def set_posisi_nomor(surat_id: int, req: SetPosisiNomorRequest, db: Session = Depends(get_db)):
    """Simpan titik-titik posisi nomor surat (permohonan upload PDF)."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa diatur posisi nomor surat")
    pastikan_boleh_kelola_surat(surat.nip_pembuat, db)
    if surat.jenis_surat != "permohonan":
        raise HTTPException(400, "Pin nomor surat hanya untuk surat permohonan")
    if surat.tipe_input != "upload":
        raise HTTPException(400, "Endpoint ini hanya untuk surat tipe upload")
    if not req.posisi_list:
        raise HTTPException(400, "Minimal 1 posisi nomor surat harus diisi")

    # Hapus posisi lama (jika user edit ulang); daftar boleh kosong
    db.query(models.PosisiNomorSurat).filter(
        models.PosisiNomorSurat.surat_id == surat_id
    ).delete()

    for pos in req.posisi_list:
        db.add(models.PosisiNomorSurat(
            surat_id    = surat_id,
            posisi_hal  = pos.posisi_hal,
            posisi_x    = pos.posisi_x,
            posisi_y    = pos.posisi_y,
            prefix_teks = pos.prefix_teks,
        ))

    db.commit()
    return {"message": f"{len(req.posisi_list)} posisi nomor surat berhasil disimpan"}


@app.get("/api/surat/{surat_id}/posisi-nomor")
def get_posisi_nomor(surat_id: int, db: Session = Depends(get_db)):
    daftar = db.query(models.PosisiNomorSurat).filter(
        models.PosisiNomorSurat.surat_id == surat_id
    ).all()
    return [
        {
            "id"         : p.id,
            "posisi_hal" : p.posisi_hal,
            "posisi_x"   : p.posisi_x,
            "posisi_y"   : p.posisi_y,
            "prefix_teks": p.prefix_teks,
        }
        for p in daftar
    ]


@app.post("/api/surat/{surat_id}/posisi-ttd")
def set_posisi_ttd(surat_id: int, req: SetPosisiTTDRequest, db: Session = Depends(get_db)):
    """Simpan posisi TTD & daftar penandatangan untuk surat upload PDF."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa diatur posisi TTD")
    pastikan_boleh_kelola_surat(surat.nip_pembuat, db)
    if surat.tipe_input != "upload":
        raise HTTPException(400, "Endpoint ini hanya untuk surat tipe upload")
    if not req.posisi_list:
        raise HTTPException(400, "Minimal 1 posisi TTD harus diisi")

    # Surat permohonan: pin nomor surat WAJIB dipasang lebih dulu, baru pin TTE
    if surat.jenis_surat == "permohonan":
        nomor_ada = db.query(models.PosisiNomorSurat).filter(
            models.PosisiNomorSurat.surat_id == surat_id
        ).first()
        if not nomor_ada:
            raise HTTPException(400, "Pasang pin nomor surat terlebih dahulu sebelum pin TTE")

    # Hapus posisi lama (jika user edit ulang)
    db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat_id
    ).delete()

    # ── PERMOHONAN: hanya 1 pin (Direktur / TTE Utama). Rantai paraf di bawahnya
    #    dibuat otomatis TANPA posisi (meniru logika kirim_surat). ──
    if surat.jenis_surat == "permohonan":
        if len(req.posisi_list) != 1:
            raise HTTPException(
                400,
                "Surat permohonan hanya boleh memiliki 1 posisi TTD (Direktur). "
                "Paraf pejabat di bawahnya diproses otomatis oleh sistem."
            )

        pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()
        unit_pembuat = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == pembuat.kode_unit
        ).first() if pembuat else None
        punya_kop = unit_pembuat.punya_kop_sendiri if unit_pembuat else False

        urutan = 1

        # Unit tanpa kop sendiri -> paraf berjenjang dulu
        if not punya_kop and pembuat:
            kode_skrg = pembuat.kode_unit
            while kode_skrg:
                unit_skrg = db.query(models.UnitOrganisasi).filter(
                    models.UnitOrganisasi.kode_unit == kode_skrg
                ).first()
                if not unit_skrg:
                    break

                # Level 1 (Direktur) dilewati — itu jatah pin TTE Utama
                if unit_skrg.level > 1:
                    pejabat = db.query(models.Pegawai).filter(
                        models.Pegawai.kode_unit == kode_skrg,
                        models.Pegawai.role_ppnp != "staf",
                        models.Pegawai.nip       != surat.nip_pembuat
                    ).first()
                    if pejabat:
                        db.add(models.PersetujuanSurat(
                            surat_id           = surat_id,
                            nip_penyetuju      = pejabat.nip,
                            jenis_ttd          = jenis_paraf_unit(unit_skrg),
                            urutan             = urutan,
                            status_persetujuan = "menunggu",
                            mode_paralel       = False,   # hirarki selalu berurutan
                            # posisi dikosongkan -> tidak dicetak di PDF
                        ))
                        urutan += 1

                kode_skrg = unit_skrg.kode_induk

        # Pin Direktur sebagai TTE Utama (satu-satunya yang dicetak di PDF)
        pos = req.posisi_list[0]
        db.add(models.PersetujuanSurat(
            surat_id           = surat_id,
            nip_penyetuju      = pos.nip_penyetuju,
            jenis_ttd          = "TTE Utama",
            urutan             = urutan,
            status_persetujuan = "menunggu",
            mode_paralel       = False,
            posisi_hal         = pos.posisi_hal,
            posisi_x           = pos.posisi_x,
            posisi_y           = pos.posisi_y,
            label_ttd          = pos.label_ttd,
        ))

        db.commit()
        return {"message": f"Posisi TTD Direktur disimpan. {urutan - 1} paraf berjenjang dibuat otomatis."}

    # ── INTERNAL: setiap pin = 1 penandatangan; pin terakhir = TTE Utama ──
    total_posisi = len(req.posisi_list)
    for i, pos in enumerate(req.posisi_list):
        db.add(models.PersetujuanSurat(
            surat_id           = surat_id,
            nip_penyetuju      = pos.nip_penyetuju,
            jenis_ttd          = "TTE Utama" if i == total_posisi - 1 else "Paraf",
            urutan             = i + 1,
            status_persetujuan = "menunggu",
            mode_paralel       = req.mode_paralel,
            posisi_hal         = pos.posisi_hal,
            posisi_x           = pos.posisi_x,
            posisi_y           = pos.posisi_y,
            label_ttd          = pos.label_ttd,
        ))

    db.commit()
    return {"message": f"{total_posisi} posisi TTD berhasil disimpan"}


# --- 9e. Download ---
@app.get("/api/download/{surat_id}")
def download_pdf_final(surat_id: int, db: Session = Depends(get_db)):
    """Download PDF final yang sudah diinjeksi TTD."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if not surat.file_final:
        raise HTTPException(400, "PDF final belum tersedia. Pastikan semua TTD sudah selesai.")

    path = f"uploads/{surat.file_final}"
    if not os.path.exists(path):
        raise HTTPException(404, "File tidak ditemukan di server")

    nama_download = f"PPNP_{surat.kode_pelacakan}_{surat.perihal[:30]}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=nama_download)


# ==========================================================================
# 10. API TANDA TANGAN
# ==========================================================================
@app.get("/api/inbox")
def get_inbox(nip: str, db: Session = Depends(get_db)):
    """Surat yang menunggu paraf/TTE dari pejabat ini."""
    persetujuan_list = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.nip_penyetuju      == nip,
        models.PersetujuanSurat.status_persetujuan == "menunggu"
    ).order_by(models.PersetujuanSurat.id.desc()).all()

    result = []
    for p in persetujuan_list:
        surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
        if not surat:
            continue

        # Surat tipe upload membuat PersetujuanSurat sejak "Simpan Posisi",
        # jadi pastikan surat benar-benar sudah dikirim (bukan draft)
        if surat.status == "draft":
            continue

        # Mode sequential: hanya tampil jika giliran pejabat ini. Mode paralel: selalu tampil.
        if not p.mode_paralel:
            urutan_aktif = db.query(models.PersetujuanSurat).filter(
                models.PersetujuanSurat.surat_id           == surat.id,
                models.PersetujuanSurat.status_persetujuan == "menunggu"
            ).order_by(models.PersetujuanSurat.urutan).first()
            if not urutan_aktif or urutan_aktif.nip_penyetuju != nip:
                continue

        pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()

        # Info tahap paraf/TTE sebelumnya — hanya untuk catatan di tabel inbox,
        # tidak dicetak di badan surat.
        paraf_sebelumnya = None
        step_sebelumnya = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "disetujui",
            models.PersetujuanSurat.urutan             <  p.urutan
        ).order_by(models.PersetujuanSurat.urutan.desc()).first()

        if step_sebelumnya:
            pejabat_sebelumnya = db.query(models.Pegawai).filter(
                models.Pegawai.nip == step_sebelumnya.nip_penyetuju
            ).first()
            paraf_sebelumnya = {
                "jenis_ttd"     : step_sebelumnya.jenis_ttd,
                "nama"          : pejabat_sebelumnya.nama    if pejabat_sebelumnya else "—",
                "jabatan"       : pejabat_sebelumnya.jabatan if pejabat_sebelumnya else "—",
                "catatan"       : step_sebelumnya.catatan,
                "tanggal_proses": str(step_sebelumnya.tanggal_proses) if step_sebelumnya.tanggal_proses else None,
            }

        result.append({
            "persetujuan_id"  : p.id,
            "surat_id"        : surat.id,
            "jenis_surat"     : surat.jenis_surat,
            "jenis_tugas"     : surat.jenis_tugas,
            "nomor_surat"     : surat.nomor_surat,
            "perihal"         : surat.perihal,
            "jenis_ttd"       : p.jenis_ttd,
            "urutan"          : p.urutan,
            "tanggal_dibuat"  : str(surat.tanggal_dibuat),
            "nama_pembuat"    : pembuat.nama    if pembuat else "—",
            "jabatan_pembuat" : pembuat.jabatan if pembuat else "—",
            "paraf_sebelumnya": paraf_sebelumnya,
        })

    return result


@app.post("/api/tanda-tangan/{persetujuan_id}")
def proses_tanda_tangan(
    persetujuan_id: int,
    nip           : str     = Form(...),
    pin           : str     = Form(...),
    catatan       : str     = Form(default=""),
    nomor_surat   : str     = Form(default=""),
    db            : Session = Depends(get_db)
):
    persetujuan = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.id            == persetujuan_id,
        models.PersetujuanSurat.nip_penyetuju == nip
    ).first()
    if not persetujuan:
        raise HTTPException(404, "Data persetujuan tidak ditemukan")
    if persetujuan.status_persetujuan != "menunggu":
        raise HTTPException(400, "Surat ini sudah diproses sebelumnya")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        raise HTTPException(404, "Pegawai tidak ditemukan")
    if persetujuan.jenis_ttd != "Mengetahui":   # "Mengetahui" tidak memakai PIN
        if not pegawai.tte_aktif or not pegawai.pin_tte:
            raise HTTPException(400, "TTE belum diregistrasi. Daftar di halaman Profil dulu.")
        if not verify_pin(pin, pegawai.pin_tte):
            raise HTTPException(401, "PIN salah!")

    persetujuan.status_persetujuan = "disetujui"
    persetujuan.catatan            = "" if persetujuan.jenis_ttd == "Mengetahui" else catatan
    persetujuan.tanggal_proses     = datetime.utcnow()

    surat = db.query(models.Surat).filter(models.Surat.id == persetujuan.surat_id).first()

    # Permohonan & TTE Utama -> simpan nomor surat yang diisi Direktur
    if (surat.jenis_surat == "permohonan"
            and persetujuan.jenis_ttd == "TTE Utama"
            and nomor_surat.strip()):

        sudah_ada = db.query(models.Surat).filter(
            models.Surat.nomor_surat == nomor_surat.strip(),
            models.Surat.id != surat.id
        ).first()
        if sudah_ada:
            raise HTTPException(400, f"Nomor surat '{nomor_surat.strip()}' sudah digunakan pada surat lain!")

        surat.nomor_surat = nomor_surat.strip()

        # Sisipkan nomor surat ke PDF (khusus tipe upload)
        if surat.tipe_input == "upload":
            db.commit()  # commit dulu supaya nomor_surat tersimpan sebelum inject
            inject_nomor_ke_pdf(surat.id, nomor_surat.strip(), db)

    # ── Cek apakah masih ada penandatangan lain yang menunggu ──
    if persetujuan.mode_paralel:
        # Paralel: selesai jika SEMUA sudah disetujui
        ada_berikut = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "menunggu"
        ).first() is not None
    else:
        # Sequential: cek urutan berikutnya
        ada_berikut = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "menunggu",
            models.PersetujuanSurat.urutan             >  persetujuan.urutan
        ).first() is not None

    if ada_berikut:
        db.commit()
        return {
            "message"  : f"{persetujuan.jenis_ttd} berhasil! Menunggu tanda tangan lainnya.",
            "status"   : "berlanjut",
            "jenis_ttd": persetujuan.jenis_ttd
        }

    # ── Semua tanda tangan selesai ──
    surat.status = "selesai"

    # Inject TTD ke PDF jika tipe upload
    if surat.tipe_input == "upload":
        db.commit()  # commit dulu agar data TTD tersimpan
        nama_final = inject_ttd_ke_pdf(surat.id, db)
        if nama_final:
            surat = db.query(models.Surat).filter(models.Surat.id == surat.id).first()
            surat.file_final = nama_final
            surat.status     = "selesai"

    buat_penugasan_dari_surat_tugas(surat, db)
    db.commit()
    return {
        "message"  : "Semua tanda tangan selesai! Surat telah final.",
        "status"   : "selesai",
        "jenis_ttd": persetujuan.jenis_ttd
    }


@app.get("/api/riwayat-ttd")
def get_riwayat_ttd(nip: str, db: Session = Depends(get_db)):
    """Riwayat paraf/TTE yang sudah diproses pejabat ini."""
    riwayat = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.nip_penyetuju      == nip,
        models.PersetujuanSurat.status_persetujuan.in_(["disetujui", "ditolak"])
    ).order_by(models.PersetujuanSurat.tanggal_proses.desc()).all()

    result = []
    for r in riwayat:
        surat = db.query(models.Surat).filter(models.Surat.id == r.surat_id).first()
        if not surat:
            continue
        pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pembuat).first()
        result.append({
            "surat_id"          : surat.id,
            "jenis_surat"       : surat.jenis_surat,
            "nomor_surat"       : surat.nomor_surat,
            "perihal"           : surat.perihal,
            "jenis_ttd"         : r.jenis_ttd,
            "status_persetujuan": r.status_persetujuan,
            "status_surat"      : surat.status,
            "tanggal_proses"    : str(r.tanggal_proses) if r.tanggal_proses else None,
            "catatan"           : r.catatan,
            "nama_pembuat"      : pembuat.nama    if pembuat else "—",
            "jabatan_pembuat"   : pembuat.jabatan if pembuat else "—",
        })
    return result


# ==========================================================================
# 11. API DISPOSISI
# ==========================================================================
@app.post("/api/disposisi")
def buat_disposisi(req: DisposisiCreate, db: Session = Depends(get_db)):
    """Teruskan surat (disposisi) ke satu atau beberapa pejabat sekaligus."""
    surat = db.query(models.Surat).filter(models.Surat.id == req.surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.jenis_surat not in ["eksternal", "internal"]:
        raise HTTPException(400, "Disposisi hanya berlaku untuk surat eksternal atau internal")

    # Internal yang sudah TTE ("selesai") juga siap didisposisikan
    status_boleh_disposisi = ["masuk", "diproses"]
    if surat.jenis_surat == "internal":
        status_boleh_disposisi.append("selesai")
    if surat.status not in status_boleh_disposisi:
        raise HTTPException(400, f"Surat berstatus '{surat.status}' tidak bisa didisposisikan lagi")

    if not req.daftar_penerima:
        raise HTTPException(400, "Minimal 1 penerima harus dipilih")
    for item in req.daftar_penerima:
        if not item.catatan or not item.catatan.strip():
            raise HTTPException(400, "Catatan wajib diisi untuk setiap penerima")

    # --- Validasi pengirim: pejabat level 1-3 ---
    pengirim = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_pengirim).first()
    if not pengirim:
        raise HTTPException(404, "Pegawai pengirim tidak ditemukan")

    unit_pengirim = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pengirim.kode_unit
    ).first()
    if not unit_pengirim or unit_pengirim.level not in [1, 2, 3]:
        raise HTTPException(400, "Hanya pejabat level 1 sampai 3 yang boleh mendisposisikan surat")

    # --- Disposisi akar (hanya Direktur) vs lanjutan (hanya penerima disposisi induk) ---
    if req.disposisi_asal_id is None:
        if unit_pengirim.level != 1:
            raise HTTPException(400, "Disposisi awal (akar) hanya boleh dilakukan oleh Direktur")
    else:
        disposisi_asal = db.query(models.Disposisi).filter(
            models.Disposisi.id == req.disposisi_asal_id
        ).first()
        if not disposisi_asal:
            raise HTTPException(404, "Disposisi asal tidak ditemukan")
        if disposisi_asal.surat_id != req.surat_id:
            raise HTTPException(400, "Disposisi asal tidak terkait dengan surat ini")
        if disposisi_asal.nip_ke != req.nip_pengirim:
            raise HTTPException(403, "Hanya penerima disposisi ini yang boleh meneruskannya")
        if disposisi_asal.status != "diteruskan":
            raise HTTPException(400, "Disposisi ini sudah diproses (diarsipkan/dijadikan tugas), tidak bisa diteruskan lagi")

    # --- Tiap penerima harus kandidat disposisi yang sah ---
    nip_valid = {
        p["nip"]
        for grup in get_kandidat_penerima(kode_unit=pengirim.kode_unit, mode="disposisi", db=db)
        for p in grup["pejabat"]
    }
    for item in req.daftar_penerima:
        if item.nip_ke not in nip_valid:
            raise HTTPException(400, f"NIP {item.nip_ke} bukan kandidat disposisi yang sah untuk unit ini")

    # --- Buat record disposisi untuk tiap penerima ---
    for item in req.daftar_penerima:
        penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == item.nip_ke).first()
        if not penerima:
            raise HTTPException(404, f"Pegawai dengan NIP {item.nip_ke} tidak ditemukan")

        db.add(models.Disposisi(
            surat_id          = req.surat_id,
            nip_dari          = req.nip_pengirim,
            nip_ke            = item.nip_ke,
            level_dari        = unit_pengirim.level,
            level_ke          = None,
            catatan           = item.catatan.strip(),
            status            = "diteruskan",
            sudah_diketahui   = True,
            disposisi_asal_id = req.disposisi_asal_id,
        ))

    hitung_ulang_status_surat(surat.id, db)
    db.commit()

    daftar_nip = [item.nip_ke for item in req.daftar_penerima]
    return {"message": f"Surat berhasil didisposisikan ke {len(daftar_nip)} penerima", "penerima": daftar_nip}


@app.post("/api/disposisi/{disposisi_id}/arsipkan")
def arsipkan_disposisi(disposisi_id: int, req: ArsipkanRequest, db: Session = Depends(get_db)):
    """Tandai disposisi ini cukup diarsipkan, tanpa diteruskan/ditugaskan lagi."""
    if not req.catatan or not req.catatan.strip():
        raise HTTPException(400, "Catatan alasan arsip wajib diisi")

    disposisi = db.query(models.Disposisi).filter(models.Disposisi.id == disposisi_id).first()
    if not disposisi:
        raise HTTPException(404, "Disposisi tidak ditemukan")
    if disposisi.status != "diteruskan":
        raise HTTPException(400, "Disposisi ini sudah diproses sebelumnya")

    disposisi.status          = "diarsipkan"
    disposisi.catatan         = disposisi.catatan + f"\n\n[Diarsipkan]: {req.catatan.strip()}"
    disposisi.sudah_diketahui = True
    disposisi.tanggal_arsip   = datetime.utcnow()

    hitung_ulang_status_surat(disposisi.surat_id, db)
    db.commit()
    return {"message": "Disposisi berhasil diarsipkan"}


@app.get("/api/disposisi-masuk")
def get_disposisi_masuk(nip: str, db: Session = Depends(get_db)):
    """Disposisi yang ditujukan ke pegawai ini dan belum diproses (belum diteruskan/ditugaskan/diarsipkan)."""
    daftar = db.query(models.Disposisi).filter(
        models.Disposisi.nip_ke == nip,
        models.Disposisi.status == "diteruskan"
    ).order_by(models.Disposisi.id.desc()).all()

    hasil = []
    for d in daftar:
        surat = db.query(models.Surat).filter(models.Surat.id == d.surat_id).first()
        if not surat:
            continue

        # Cek per-disposisi (bukan per-surat): 1 surat bisa punya banyak cabang paralel
        sudah_diteruskan_lagi = db.query(models.Disposisi).filter(
            models.Disposisi.disposisi_asal_id == d.id
        ).first()
        if sudah_diteruskan_lagi:
            continue

        penugasan_terkait = db.query(models.Penugasan).filter(
            models.Penugasan.disposisi_id == d.id
        ).first()
        if penugasan_terkait:
            continue

        pengirim = db.query(models.Pegawai).filter(models.Pegawai.nip == d.nip_dari).first()
        hasil.append({
            "disposisi_id" : d.id,
            "surat_id"     : surat.id,
            "perihal"      : surat.perihal,
            "asal_surat"   : surat.asal_surat,
            "nomor_surat"  : surat.nomor_surat,
            "catatan"      : d.catatan,
            "level_ke"     : d.level_ke,
            "tanggal"      : str(d.tanggal),
            "nama_pengirim": pengirim.nama if pengirim else "—",
        })

    return hasil


# ==========================================================================
# 12. API PENUGASAN, LAPORAN & PENILAIAN TUGAS
# ==========================================================================
@app.post("/api/penugasan")
def buat_penugasan(req: PenugasanCreate, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == req.surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status not in ["diproses", "sedang_dikerjakan"]:
        raise HTTPException(400, f"Surat berstatus '{surat.status}' tidak bisa diberi penugasan")
    if not req.daftar_penerima:
        raise HTTPException(400, "Minimal 1 penerima harus dipilih")

    pemberi = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_pemberi).first()
    if not pemberi:
        raise HTTPException(404, "Pegawai pemberi tugas tidak ditemukan")

    unit_pemberi = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pemberi.kode_unit
    ).first()
    if not unit_pemberi or unit_pemberi.level not in [1, 2, 3, 4]:
        raise HTTPException(400, "Hanya pejabat level 1 sampai 4 yang boleh memberi penugasan")

    deadline_parsed = None
    if req.deadline:
        try:
            deadline_parsed = datetime.strptime(req.deadline, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format deadline harus YYYY-MM-DD")

    # --- Validasi SEMUA penerima dulu (all-or-nothing, sebelum insert apapun) ---
    daftar_valid = []
    for nip_penerima in req.daftar_penerima:
        penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == nip_penerima).first()
        if not penerima:
            raise HTTPException(404, f"Pegawai dengan NIP {nip_penerima} tidak ditemukan")

        unit_penerima = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == penerima.kode_unit
        ).first()

        # Sah jika unit penerima levelnya lebih rendah, ATAU penerima (pemberi sendiri / staf)
        # berada di unit yang SAMA dengan pemberi (staf tidak punya unit terpisah).
        boleh_ditugaskan = (
            (unit_penerima and unit_penerima.level > unit_pemberi.level)
            or penerima.kode_unit == pemberi.kode_unit
        )
        if not boleh_ditugaskan:
            raise HTTPException(400, f"{penerima.nama} tidak bisa ditugaskan (bukan bawahan / bukan di unit yang sama)")

        daftar_valid.append(penerima)

    # --- Node disposisi yang dituju harus masih terbuka ---
    if req.disposisi_id:
        disposisi = db.query(models.Disposisi).filter(models.Disposisi.id == req.disposisi_id).first()
        if not disposisi:
            raise HTTPException(404, "Disposisi terkait tidak ditemukan")
        if disposisi.status != "diteruskan":
            raise HTTPException(400, "Disposisi ini sudah diproses sebelumnya, tidak bisa ditugaskan lagi")
        disposisi.status = "diproses_jadi_tugas"

    daftar_id_penugasan = []
    for penerima in daftar_valid:
        penugasan_baru = models.Penugasan(
            surat_id        = req.surat_id,
            disposisi_id    = req.disposisi_id,
            nip_pemberi     = req.nip_pemberi,
            nip_penerima    = penerima.nip,
            deskripsi_tugas = req.deskripsi_tugas,
            deadline        = deadline_parsed,
            status          = "proses",
        )
        db.add(penugasan_baru)
        db.flush()
        daftar_id_penugasan.append(penugasan_baru.id)

    hitung_ulang_status_surat(surat.id, db)
    db.commit()

    return {
        "message"           : f"Tugas berhasil diberikan ke {len(daftar_valid)} orang",
        "daftar_penugasan_id": daftar_id_penugasan
    }


@app.get("/api/tugas-saya")
def get_tugas_saya(nip: str, db: Session = Depends(get_db)):
    """Penugasan yang diberikan ke pegawai ini."""
    daftar = db.query(models.Penugasan).filter(
        models.Penugasan.nip_penerima == nip
    ).order_by(models.Penugasan.id.desc()).all()

    hasil = []
    for p in daftar:
        surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
        if not surat:
            continue
        pemberi = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_pemberi).first()
        laporan = db.query(models.LaporanTugas).filter(
            models.LaporanTugas.penugasan_id == p.id
        ).order_by(models.LaporanTugas.id.desc()).first()
        penilaian = db.query(models.Penilaian).filter(models.Penilaian.penugasan_id == p.id).first()

        hasil.append({
            "penugasan_id"      : p.id,
            "surat_id"          : surat.id,
            "perihal"           : surat.perihal,
            "asal_surat"        : surat.asal_surat,
            "deskripsi_tugas"   : p.deskripsi_tugas,
            "deadline"          : str(p.deadline) if p.deadline else None,
            "status"            : p.status,
            "tanggal_ditugaskan": str(p.tanggal_ditugaskan),
            "nama_pemberi"      : nama_pemberi_tampil(pemberi, surat, db),
            "sudah_lapor"       : laporan is not None,
            "tanggal_lapor"     : str(laporan.tanggal_lapor) if laporan else None,
            "sudah_dinilai"     : penilaian is not None,
            "skala_nilai"       : penilaian.skala if penilaian else None,
            **info_surat_tugas(p.id, db),
            **info_laporan_pd(p, db),
            **info_rapat_tugas(p, db),
            "jenis_tugas"       : surat.jenis_tugas,
        })

    return hasil


@app.get("/api/tugas-diberikan")
def get_tugas_diberikan(nip: str, db: Session = Depends(get_db)):
    """Penugasan yang DIBERIKAN pegawai ini ke orang lain — untuk menilai laporan."""
    daftar = db.query(models.Penugasan).filter(
        models.Penugasan.nip_pemberi == nip
    ).order_by(models.Penugasan.id.desc()).all()

    hasil = []
    for p in daftar:
        surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
        if not surat or surat.jenis_tugas == "rapat":   # rapat: tanpa laporan & penilaian per peserta
            continue
        penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_penerima).first()
        laporan = db.query(models.LaporanTugas).filter(
            models.LaporanTugas.penugasan_id == p.id
        ).order_by(models.LaporanTugas.id.desc()).first()
        penilaian = db.query(models.Penilaian).filter(models.Penilaian.penugasan_id == p.id).first()

        hasil.append({
            "penugasan_id"      : p.id,
            "surat_id"          : surat.id,
            "perihal"           : surat.perihal,
            "asal_surat"        : surat.asal_surat,
            "deskripsi_tugas"   : p.deskripsi_tugas,
            "deadline"          : str(p.deadline) if p.deadline else None,
            "status"            : p.status,
            "tanggal_ditugaskan": str(p.tanggal_ditugaskan),
            "nama_penerima"     : penerima.nama if penerima else "—",
            "nip_penerima"      : p.nip_penerima,
            "bisa_dikoreksi"    : penugasan_bisa_dikoreksi(p, surat, db),
            **info_surat_tugas(p.id, db),
            "sudah_lapor"       : laporan is not None,
            "isi_laporan"       : laporan.isi_laporan if laporan else None,
            "file_bukti"        : laporan.file_bukti  if laporan else None,
            "tanggal_lapor"     : str(laporan.tanggal_lapor) if laporan else None,
            "terlambat"         : bool(laporan and p.deadline and laporan.tanggal_lapor.date() > p.deadline.date()),
            "sudah_dinilai"     : penilaian is not None,
            "skala_nilai"       : penilaian.skala   if penilaian else None,
            "catatan_nilai"     : penilaian.catatan if penilaian else None,
        })

    return hasil


@app.post("/api/upload/bukti-tugas")
def upload_bukti_tugas(file: UploadFile = File(...)):
    """Upload file bukti laporan tugas (wajib PDF)."""
    ext = file.filename.split(".")[-1].lower()
    if ext != "pdf":
        raise HTTPException(400, "File bukti harus dalam format PDF. Gabungkan semua dokumentasi/lampiran menjadi satu file PDF.")

    now = datetime.utcnow()
    folder_relatif = f"bukti_tugas/{now.strftime('%Y')}/{now.strftime('%m')}"
    os.makedirs(f"uploads/{folder_relatif}", exist_ok=True)

    nama_bersih = re.sub(r"[^a-zA-Z0-9_\-]", "_", file.filename.rsplit(".", 1)[0])
    path_relatif_db = f"{folder_relatif}/{int(time.time())}_{nama_bersih}.{ext}"

    simpan_file_upload(file, path_relatif_db)
    return {"nama_file": path_relatif_db}


@app.post("/api/laporan-tugas")
def buat_laporan_tugas(req: LaporanTugasCreate, db: Session = Depends(get_db)):
    penugasan = db.query(models.Penugasan).filter(models.Penugasan.id == req.penugasan_id).first()
    if not penugasan:
        raise HTTPException(404, "Penugasan tidak ditemukan")
    if penugasan.status != "proses":
        raise HTTPException(400, "Penugasan ini sudah tidak dalam status proses")
    # Jika penugasan ini menghasilkan surat tugas yang sudah TTE, surat itu menjadi bukti laporan
    surat_tugas_selesai = db.query(models.Surat).filter(
        models.Surat.penugasan_asal_id == penugasan.id,
        models.Surat.status == "selesai"
    ).first()
    if not req.file_bukti and not surat_tugas_selesai:
        raise HTTPException(400, "File bukti (PDF) wajib dilampirkan")

    laporan_baru = models.LaporanTugas(
        penugasan_id = req.penugasan_id,
        isi_laporan  = req.isi_laporan,
        file_bukti   = req.file_bukti,
    )
    db.add(laporan_baru)
    db.commit()
    db.refresh(laporan_baru)
    return {"message": "Laporan berhasil dikirim", "laporan_id": laporan_baru.id}


@app.post("/api/penilaian")
def buat_penilaian(req: PenilaianCreate, db: Session = Depends(get_db)):
    skala_valid = ["sangat_baik", "baik", "cukup", "kurang"]
    if req.skala not in skala_valid:
        raise HTTPException(400, f"Skala harus salah satu dari: {', '.join(skala_valid)}")

    penugasan = db.query(models.Penugasan).filter(models.Penugasan.id == req.penugasan_id).first()
    if not penugasan:
        raise HTTPException(404, "Penugasan tidak ditemukan")
    if penugasan.nip_pemberi != req.nip_penilai:
        raise HTTPException(403, "Hanya pemberi tugas yang boleh menilai penugasan ini")

    laporan_ada = db.query(models.LaporanTugas).filter(
        models.LaporanTugas.penugasan_id == req.penugasan_id
    ).first()
    if not laporan_ada:
        raise HTTPException(400, "Belum ada laporan yang bisa dinilai untuk penugasan ini")

    penilaian_lama = db.query(models.Penilaian).filter(
        models.Penilaian.penugasan_id == req.penugasan_id
    ).first()
    if penilaian_lama:
        raise HTTPException(400, "Penugasan ini sudah pernah dinilai")

    penilaian_baru = models.Penilaian(
        penugasan_id = req.penugasan_id,
        skala        = req.skala,
        catatan      = req.catatan,
        nip_penilai  = req.nip_penilai,
    )
    db.add(penilaian_baru)

    # Setelah dinilai, penugasan selesai; status surat dihitung ulang
    penugasan.status = "selesai"
    hitung_ulang_status_surat(penugasan.surat_id, db)

    db.commit()
    db.refresh(penilaian_baru)
    return {"message": "Penilaian berhasil disimpan, penugasan ditandai selesai"}


# ==========================================================================
# 13. API PENILAIAN LAYANAN (bintang per utas surat)
# ==========================================================================
@app.post("/api/penilaian-layanan")
def buat_penilaian_layanan(req: PenilaianLayananCreate, db: Session = Depends(get_db)):
    """Penilaian sekali per utas — hanya pembuat surat pertama (akar utas) yang boleh menilai."""
    if req.bintang < 1 or req.bintang > 5:
        raise HTTPException(400, "Bintang harus antara 1-5")

    root = cari_surat_root(req.surat_id, db)
    if not root:
        raise HTTPException(404, "Surat tidak ditemukan")
    if root.nip_pembuat != req.nip_penilai:
        raise HTTPException(403, "Hanya pembuat surat pertama yang berhak memberi penilaian untuk utas ini")

    sudah_ada = db.query(models.PenilaianLayanan).filter(
        models.PenilaianLayanan.surat_root_id == root.id
    ).first()
    if sudah_ada:
        raise HTTPException(400, "Utas surat ini sudah pernah dinilai")

    db.add(models.PenilaianLayanan(
        surat_root_id = root.id,
        nip_penilai   = req.nip_penilai,
        bintang       = req.bintang,
        catatan       = req.catatan,
    ))
    db.commit()
    return {"message": "Penilaian berhasil disimpan, terima kasih!"}


@app.get("/api/penilaian-layanan-diterima")
def get_penilaian_layanan_diterima(nip: str, db: Session = Depends(get_db)):
    """Penilaian yang DITERIMA pegawai ini (karena pernah membalas di utas yang dinilai)."""
    hasil = []

    for p in db.query(models.PenilaianLayanan).all():
        if p.nip_penilai == nip:
            continue  # dia yang menilai, bukan yang dinilai

        id_dalam_utas = ambil_semua_id_dalam_utas(p.surat_root_id, db)
        pernah_membalas = db.query(models.Surat).filter(
            models.Surat.id.in_(id_dalam_utas),
            models.Surat.nip_pembuat == nip
        ).first()
        if not pernah_membalas:
            continue

        root    = db.query(models.Surat).filter(models.Surat.id == p.surat_root_id).first()
        penilai = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_penilai).first()

        hasil.append({
            "id"             : p.id,
            "surat_root_id"  : p.surat_root_id,
            "perihal_utas"   : root.perihal if root else "—",
            "bintang"        : p.bintang,
            "catatan"        : p.catatan,
            "tanggal_nilai"  : str(p.tanggal_nilai),
            "nama_penilai"   : penilai.nama    if penilai else "—",
            "jabatan_penilai": penilai.jabatan if penilai else "—",
        })

    hasil.sort(key=lambda x: x["tanggal_nilai"], reverse=True)
    return hasil


# ==========================================================================
# 14. API AGREGASI INBOX
# ==========================================================================
@app.get("/api/surat-masuk")
def get_surat_masuk(nip: str, db: Session = Depends(get_db)):
    """
    Surat yang DITERIMA user ini (tab "Surat Diterima"):
    - Internal  : nip_tujuan == nip & status selesai, dan belum ditindaklanjuti
                  (diteruskan/dibalas/didisposisikan). Jika penerimanya Direktur,
                  kategori = "disposisi".
    - Eksternal : khusus Direktur, status masuk & belum didisposisikan.
    - Permohonan: pembuatnya sendiri, setelah status selesai.
    """
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        return []

    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()
    is_direktur = bool(unit and unit.level == 1)

    result = []

    # ── 1. Surat internal ──
    surat_internal = db.query(models.Surat).filter(
        models.Surat.nip_tujuan  == nip,
        models.Surat.jenis_surat == "internal",
        models.Surat.status      == "selesai"
    ).order_by(models.Surat.id.desc()).all()

    for s in surat_internal:
        # Lewati jika sudah ditindaklanjuti (ada surat lanjutan/balasan atau disposisi akar)
        sudah_lanjutan = db.query(models.Surat).filter(models.Surat.surat_asal_id == s.id).first()
        sudah_disposisi = db.query(models.Disposisi).filter(
            models.Disposisi.surat_id == s.id,
            models.Disposisi.disposisi_asal_id.is_(None)
        ).first()
        if sudah_lanjutan or sudah_disposisi:
            continue

        pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == s.nip_pembuat).first()

        surat_asal = None
        if s.surat_asal_id:
            surat_asal = db.query(models.Surat).filter(models.Surat.id == s.surat_asal_id).first()

        # Penerima berhak menilai utas jika dia pembuat surat pertama (dan ini bukan surat pertama)
        root = cari_surat_root(s.id, db)
        bisa_dinilai  = bool(root and root.nip_pembuat == nip and s.id != root.id)
        sudah_dinilai = False
        if bisa_dinilai:
            sudah_dinilai = db.query(models.PenilaianLayanan).filter(
                models.PenilaianLayanan.surat_root_id == root.id
            ).first() is not None
            if sudah_dinilai:
                continue   # utas sudah dinilai & ditutup: pindah ke Riwayat Saya

        result.append({
            "surat_id"        : s.id,
            "jenis_surat"     : s.jenis_surat,
            "nomor_surat"     : s.nomor_surat,
            "perihal"         : s.perihal,
            "tanggal_dibuat"  : str(s.tanggal_dibuat),
            "tanggal_surat"   : s.tanggal_surat,
            "status"          : s.status,
            "nama_pengirim"   : pembuat.nama    if pembuat else "—",
            "jabatan_pengirim": pembuat.jabatan if pembuat else "—",
            "kategori"        : "disposisi" if is_direktur else "diterima",
            "surat_asal_id"   : s.surat_asal_id,
            "perihal_asal"    : surat_asal.perihal if surat_asal else None,
            "bisa_dinilai"    : bisa_dinilai,
            "sudah_dinilai"   : sudah_dinilai,
        })

    # ── 2. Surat eksternal (khusus Direktur) ──
    if is_direktur:
        surat_eksternal = db.query(models.Surat).filter(
            models.Surat.jenis_surat == "eksternal",
            models.Surat.status      == "masuk"
        ).order_by(models.Surat.id.desc()).all()

        for s in surat_eksternal:
            sudah_disposisi = db.query(models.Disposisi).filter(
                models.Disposisi.surat_id == s.id,
                models.Disposisi.disposisi_asal_id.is_(None)
            ).first()
            if sudah_disposisi:
                continue

            pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == s.nip_pembuat).first()
            result.append({
                "surat_id"        : s.id,
                "jenis_surat"     : s.jenis_surat,
                "nomor_surat"     : s.nomor_surat,
                "perihal"         : s.perihal,
                "tanggal_dibuat"  : str(s.tanggal_dibuat),
                "tanggal_surat"   : s.tanggal_surat,
                "status"          : s.status,
                "asal_surat"      : s.asal_surat,
                "nama_pengirim"   : pembuat.nama    if pembuat else "—",
                "jabatan_pengirim": pembuat.jabatan if pembuat else "—",
                "kategori"        : "disposisi"
            })

    # ── 3. Permohonan selesai — kembali ke pembuat ──
    surat_permohonan = db.query(models.Surat).filter(
        models.Surat.nip_pembuat == nip,
        models.Surat.jenis_surat == "permohonan",
        models.Surat.status      == "selesai"
    ).order_by(models.Surat.id.desc()).all()

    for s in surat_permohonan:
        result.append({
            "surat_id"        : s.id,
            "jenis_surat"     : s.jenis_surat,
            "nomor_surat"     : s.nomor_surat,
            "perihal"         : s.perihal,
            "tanggal_dibuat"  : str(s.tanggal_dibuat),
            "tanggal_surat"   : s.tanggal_surat,
            "status"          : s.status,
            "nama_pengirim"   : "Direktur PPNP",
            "jabatan_pengirim": "Telah ditandatangani & diterbitkan",
            "kategori"        : "selesai"
        })

    result.sort(key=lambda x: x["tanggal_dibuat"], reverse=True)
    return result


@app.get("/api/riwayat-tindak-lanjut")
def get_riwayat_tindak_lanjut(nip: str, db: Session = Depends(get_db)):
    """
    Surat yang PERNAH masuk ke pegawai ini dan SUDAH ditindaklanjuti — diteruskan/dibalas
    sebagai surat lanjutan (internal), atau didisposisikan (eksternal / internal ke Direktur).
    """
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        return []

    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()
    is_direktur = bool(unit and unit.level == 1)

    result = []

    # ── 1. Surat internal yang diterima nip ini ──
    surat_internal = db.query(models.Surat).filter(
        models.Surat.nip_tujuan  == nip,
        models.Surat.jenis_surat == "internal",
        models.Surat.status      == "selesai"
    ).all()

    for s in surat_internal:
        lanjutan = db.query(models.Surat).filter(models.Surat.surat_asal_id == s.id).first()
        disposisi_root = db.query(models.Disposisi).filter(
            models.Disposisi.surat_id == s.id,
            models.Disposisi.disposisi_asal_id.is_(None)
        ).first()
        utas_ditutup = False
        root = cari_surat_root(s.id, db)
        if root and root.nip_pembuat == nip and s.id != root.id:
            utas_ditutup = db.query(models.PenilaianLayanan).filter(
                models.PenilaianLayanan.surat_root_id == root.id
            ).first() is not None
        if not lanjutan and not disposisi_root and not utas_ditutup:
            continue  # belum ditindaklanjuti -> masih tampil di "Surat Diterima"
        if disposisi_root and not lanjutan and not utas_ditutup:
            continue  # disposisi awal sudah dicatat di riwayat-saya-lengkap (poin 5b)

        pembuat = db.query(models.Pegawai).filter(models.Pegawai.nip == s.nip_pembuat).first()
        if lanjutan:
            aksi = f'Diteruskan sebagai surat lanjutan: "{lanjutan.perihal}"'
        elif not disposisi_root:
            aksi = "Utas dinilai & ditutup"
        else:
            penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == disposisi_root.nip_ke).first()
            aksi = f"Didisposisikan ke {penerima.nama if penerima else '—'}"

        result.append({
            "surat_id"      : s.id,
            "jenis_surat"   : s.jenis_surat,
            "perihal"       : s.perihal,
            "nomor_surat"   : s.nomor_surat,
            "tanggal_dibuat": str(s.tanggal_dibuat),
            "nama_pengirim" : pembuat.nama if pembuat else "—",
            "aksi"          : aksi,
        })

    result.sort(key=lambda x: x["tanggal_dibuat"], reverse=True)
    return result

@app.get("/api/perlu-tindakan")
def get_perlu_tindakan(nip: str, db: Session = Depends(get_db)):
    """Gabungan semua item yang membutuhkan tindakan pegawai ini (tab "Perlu Tindakan")."""
    hasil = []

    # 1. TTE / paraf
    for item in get_inbox(nip=nip, db=db):
        approved_steps = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == item["surat_id"],
            models.PersetujuanSurat.status_persetujuan == "disetujui"
        ).order_by(models.PersetujuanSurat.urutan).all()

        chain_diketahui = []
        for step in approved_steps:
            pejabat_step = db.query(models.Pegawai).filter(
                models.Pegawai.nip == step.nip_penyetuju
            ).first()
            chain_diketahui.append({
                "jenis_ttd": step.jenis_ttd,
                "nama"     : pejabat_step.nama    if pejabat_step else "—",
                "jabatan"  : pejabat_step.jabatan if pejabat_step else "—",
                "catatan"  : step.catatan,
                "tanggal"  : str(step.tanggal_proses) if step.tanggal_proses else None,
            })

        hasil.append({
            "tanggal"        : item["tanggal_dibuat"],
            "tipe_aksi"      : "tte",
            "label_aksi"     : {"Paraf": "✍️ Paraf", "Mengetahui": "👁️ Mengetahui"}.get(item["jenis_ttd"], "🔏 TTE") + f" — Urutan ke-{item['urutan']}",
            "jenis_surat"    : item["jenis_surat"],
            "perihal"        : item["perihal"],
            "nomor_surat"    : item["nomor_surat"],
            "pihak_terkait"  : item["nama_pembuat"],
            "jabatan_terkait": item["jabatan_pembuat"],
            "surat_id"       : item["surat_id"],
            "ref_id"         : item["persetujuan_id"],
            "extra"          : {"jenis_ttd": item["jenis_ttd"],
                                "chain_diketahui": chain_diketahui,
                                "jenis_tugas": item.get("jenis_tugas")},   # ← UBAH
        })

    # 2. Disposisi baru (surat masuk yang perlu didisposisikan Direktur)
    for item in get_surat_masuk(nip=nip, db=db):
        if item["kategori"] == "disposisi":
            hasil.append({
                "tanggal"        : item["tanggal_dibuat"],
                "tipe_aksi"      : "disposisi_baru",
                "label_aksi"     : "📋 Perlu Disposisi",
                "jenis_surat"    : item["jenis_surat"],
                "perihal"        : item["perihal"],
                "nomor_surat"    : item.get("nomor_surat"),
                "pihak_terkait"  : item.get("nama_pengirim", "—"),
                "jabatan_terkait": item.get("jabatan_pengirim", ""),
                "surat_id"       : item["surat_id"],
                "ref_id"         : None,
                "extra"          : {},
            })

    # 3. Disposisi lanjutan (perlu diteruskan / ditugaskan / diarsipkan)
    for item in get_disposisi_masuk(nip=nip, db=db):
        hasil.append({
            "tanggal"        : item["tanggal"],
            "tipe_aksi"      : "disposisi_lanjut",
            "label_aksi"     : "📤 Perlu Diteruskan/Ditugaskan",
            "jenis_surat"    : None,
            "perihal"        : item["perihal"],
            "nomor_surat"    : item["nomor_surat"],
            "pihak_terkait"  : item["nama_pengirim"],
            "jabatan_terkait": "",
            "surat_id"       : item["surat_id"],
            "ref_id"         : item["disposisi_id"],
            "extra"          : {"catatan": item["catatan"]},
        })

    # 4. Tugas yang perlu saya laporkan atau tanda tangani (saya = penerima tugas)
    for item in get_tugas_saya(nip=nip, db=db):
        if item["status"] == "proses" and not item["sudah_lapor"] and not item.get("saya_sudah_ttd"):
            hasil.append({
                "tanggal"        : item["tanggal_ditugaskan"],
                "tipe_aksi"      : "tugas_lapor",
                "label_aksi"     : ("📅 Undangan Rapat" if item.get("jenis_tugas") == "rapat"
                                    else ("✍️ Perlu TTE Laporan" if item.get("laporan_pd_id") else "📝 Perlu Lapor Tugas")),
                "jenis_surat"    : None,
                "perihal"        : item["perihal"],
                "nomor_surat"    : (item.get("rapat_ket_waktu") if item.get("jenis_tugas") == "rapat"
                                    else (f"⏰ Batas lapor: {item['deadline'][:10]} (pengingat)" if item["deadline"] else None)),
                "pihak_terkait"  : item["nama_pemberi"],
                "jabatan_terkait": "",
                "surat_id"       : item["surat_id"],
                "ref_id"         : item["penugasan_id"],
                "extra"          : {"catatan": item["deskripsi_tugas"],
                                    "surat_tugas_id": item["surat_tugas_id"],
                                    "surat_tugas_status": item["surat_tugas_status"],
                                    "jenis_tugas": item.get("jenis_tugas"),
                                    "rapat": {k: item.get(k) for k in
                                              ("rapat_notulen", "rapat_status_saya",
                                               "rapat_absen_buka", "rapat_ket_absen",
                                               "rapat_laporan_status", "rapat_catatan_jadwal")},
                                    "pd": {k: item.get(k) for k in
                                           ("laporan_pd_id", "laporan_pd_file", "saya_penyusun",
                                            "jml_ttd", "jml_peserta")}},
            })

    # 5. Laporan yang perlu saya nilai (saya = pemberi tugas)
    for item in get_tugas_diberikan(nip=nip, db=db):
        if item["sudah_lapor"] and not item["sudah_dinilai"]:
            hasil.append({
                "tanggal"        : item["tanggal_lapor"] or item["tanggal_ditugaskan"],
                "tipe_aksi"      : "tugas_nilai",
                "label_aksi"     : "⭐ Perlu Dinilai",
                "jenis_surat"    : None,
                "perihal"        : item["perihal"],
                "nomor_surat"    : ("⚠️ Lapor melewati batas, pertimbangkan dalam nilai" if item["terlambat"]
                                    else ("✅ Lapor tepat waktu" if item["deadline"] else None)),
                "pihak_terkait"  : item["nama_penerima"],
                "jabatan_terkait": "",
                "surat_id"       : item["surat_id"],
                "ref_id"         : item["penugasan_id"],
                "extra"          : {"isi_laporan"   : item["isi_laporan"],
                                    "file_bukti"    : item["file_bukti"],
                                    "surat_tugas_id": item.get("surat_tugas_id"),
                                    "jenis_tugas"   : item.get("jenis_tugas")},
            })

    # 6. Notulensi rapat yang menunggu TTE saya (saya = pimpinan rapat)
    for lap in db.query(models.LaporanRapat).filter(
        models.LaporanRapat.status == "menunggu_ttd"
    ).all():
        surat_r = db.query(models.Surat).filter(models.Surat.id == lap.surat_id).first()
        if not surat_r or surat_r.nip_pimpinan_rapat != nip:
            continue
        peserta_r = _daftar_peserta_rapat(lap, db)
        nama_notulen = next((p["nama"] for p in peserta_r if p["is_notulen"]), "—")
        hasil.append({
            "tanggal"        : str(lap.tanggal_kirim or lap.tanggal_dibuat),
            "tipe_aksi"      : "notulen_ttd",
            "label_aksi"     : "🔏 Notulensi Perlu TTE",
            "jenis_surat"    : None,
            "perihal"        : surat_r.agenda_rapat or surat_r.perihal,
            "nomor_surat"    : f"Notulen: {nama_notulen}",
            "pihak_terkait"  : nama_notulen,
            "jabatan_terkait": "Notulen rapat",
            "surat_id"       : surat_r.id,
            "ref_id"         : lap.id,
            "extra"          : {"jenis_tugas": "rapat",
                                "url_notulen": (f"/api/rapat/notulen-file/{lap.id}?nip={nip}"
                                                if lap.file_notulen else None),
                                "file_daftar_hadir": lap.file_daftar_hadir},
        })

    hasil.sort(key=lambda x: x["tanggal"] or "", reverse=True)
    return hasil

@app.get("/api/riwayat-saya-lengkap")
def get_riwayat_saya_lengkap(nip: str, db: Session = Depends(get_db)):
    """Gabungan semua riwayat aksi pegawai ini (tab "Riwayat Saya")."""
    hasil = []

    # 1. TTE / paraf
    for item in get_riwayat_ttd(nip=nip, db=db):
        hasil.append({
            "tanggal"        : item["tanggal_proses"],
            "tipe_aksi"      : "tte",
            "label_aksi"     : {"Paraf": "✍️ Paraf", "Mengetahui": "👁️ Mengetahui"}.get(item["jenis_ttd"], "🔏 TTE Utama"),
            "jenis_surat"    : item["jenis_surat"],
            "perihal"        : item["perihal"],
            "nomor_surat"    : item["nomor_surat"],
            "pihak_terkait"  : item["nama_pembuat"],
            "jabatan_terkait": item["jabatan_pembuat"],
            "surat_id"       : item["surat_id"],
            "catatan"        : item["catatan"],
        })

    # 2. Diteruskan / didisposisikan (surat yang pernah masuk ke saya)
    for item in get_riwayat_tindak_lanjut(nip=nip, db=db):
        hasil.append({
            "tanggal"        : item["tanggal_dibuat"],
            "tipe_aksi"      : "disposisi",
            "label_aksi"     : "📤 Diteruskan/Disposisi",
            "jenis_surat"    : item["jenis_surat"],
            "perihal"        : item["perihal"],
            "nomor_surat"    : item["nomor_surat"],
            "pihak_terkait"  : item["nama_pengirim"],
            "jabatan_terkait": "",
            "surat_id"       : item["surat_id"],
            "catatan"        : item["aksi"],
        })

    # 3. Tugas yang saya kerjakan (saya = penerima): sudah TTE laporan, sudah lapor, atau sudah dinilai
    for item in get_tugas_saya(nip=nip, db=db):
        if item["status"] == "selesai" and item.get("jenis_tugas") == "rapat":
            label = f"📅 Rapat — {item.get('rapat_label_status') or 'Selesai'}"
        elif item["status"] == "selesai":
            label = f"📝 Tugas Selesai — ⭐ {item['skala_nilai'].replace('_', ' ') if item['skala_nilai'] else '—'}"
        elif item["sudah_lapor"]:
            label = "📝 Laporan Terkirim — menunggu penilaian"
        elif item.get("saya_sudah_ttd"):
            label = f"✍️ Sudah TTE Laporan — menunggu peserta lain ({item.get('jml_ttd', 0)}/{item.get('jml_peserta', 0)})"
        else:
            continue   # belum lapor/TTE: masih di "Perlu Tindakan"
        hasil.append({
            "tanggal"        : item.get("tanggal_lapor") or item["tanggal_ditugaskan"],
            "tipe_aksi"      : "tugas",
            "label_aksi"     : label,
            "jenis_surat"    : None,
            "perihal"        : item["perihal"],
            "nomor_surat"    : None,
            "pihak_terkait"  : item["nama_pemberi"],
            "jabatan_terkait": "",
            "surat_id"       : item["surat_id"],
            "catatan"        : item["deskripsi_tugas"],
            "penugasan_id"   : item["penugasan_id"],
            "laporan_pd_id"  : item.get("laporan_pd_id"),
            "laporan_pd_final": item.get("laporan_pd_status") == "final",
            "saya_penyusun"  : item.get("saya_penyusun"),
            "file_laporan"   : item.get("laporan_pd_file"),
        })

    # 4. Tugas yang saya berikan (saya = pemberi): tercatat sejak diberikan
    for item in get_tugas_diberikan(nip=nip, db=db):
        if item["sudah_dinilai"]:
            label   = f"📋 Memberi Tugas — ⭐ {item['skala_nilai'].replace('_', ' ') if item['skala_nilai'] else '—'}"
            catatan = item["catatan_nilai"] or item["deskripsi_tugas"]
        elif item["sudah_lapor"]:
            continue   # menunggu nilai saya: tampil di "Perlu Tindakan"
        else:
            label   = "📋 Memberi Tugas — menunggu laporan"
            catatan = item["deskripsi_tugas"]
        hasil.append({
            "tanggal"        : item["tanggal_lapor"] or item["tanggal_ditugaskan"],
            "tipe_aksi"      : "tugas",
            "label_aksi"     : label,
            "jenis_surat"    : None,
            "perihal"        : item["perihal"],
            "nomor_surat"    : None,
            "pihak_terkait"  : item["nama_penerima"],
            "jabatan_terkait": "",
            "surat_id"       : item["surat_id"],
            "catatan"        : catatan,
            "penugasan_id"   : item["penugasan_id"],
            "bisa_dikoreksi" : item.get("bisa_dikoreksi", False) and not item["sudah_lapor"],
            "deskripsi_tugas": item["deskripsi_tugas"],
            "deadline"       : item["deadline"],
        })

    # 5. Disposisi yang saya kirim (awal & lanjutan), digabung per surat
    kelompok_disposisi = {}
    for d in db.query(models.Disposisi).filter(
        models.Disposisi.nip_dari == nip
    ).order_by(models.Disposisi.id).all():
        kelompok_disposisi.setdefault((d.surat_id, d.disposisi_asal_id), []).append(d)

    for (surat_id_k, asal_id), daftar_d in kelompok_disposisi.items():
        surat = db.query(models.Surat).filter(models.Surat.id == surat_id_k).first()
        penerima_list = []
        for d in daftar_d:
            penerima = db.query(models.Pegawai).filter(models.Pegawai.nip == d.nip_ke).first()
            penerima_list.append({
                "disposisi_id": d.id,
                "nama"        : penerima.nama if penerima else "—",
                "jabatan"     : penerima.jabatan if penerima else "",
                "catatan"     : d.catatan,
                "bisa_ditarik": disposisi_bisa_ditarik(d, db),
            })

        jml = len(penerima_list)
        ada_tarik = any(x["bisa_ditarik"] for x in penerima_list)
        if asal_id is None:
            label = "📤 Disposisi Awal" + (" — menunggu diproses" if ada_tarik else "")
        else:
            label = "📤 Meneruskan Disposisi"
        if jml > 1:
            label += f" ({jml} penerima)"

        hasil.append({
            "tanggal"        : str(daftar_d[0].tanggal),
            "tipe_aksi"      : "disposisi",
            "label_aksi"     : label,
            "jenis_surat"    : surat.jenis_surat if surat else None,
            "perihal"        : surat.perihal if surat else "—",
            "nomor_surat"    : surat.nomor_surat if surat else None,
            "pihak_terkait"  : ", ".join(x["nama"] for x in penerima_list),
            "jabatan_terkait": "",
            "surat_id"       : surat_id_k,
            "catatan"        : " • ".join(f"{x['nama']}: {x['catatan']}" for x in penerima_list) if jml > 1
                               else penerima_list[0]["catatan"],
            "penerima_list"  : penerima_list,
        })

    # 6. Disposisi yang saya arsipkan
    for d in db.query(models.Disposisi).filter(
        models.Disposisi.nip_ke == nip,
        models.Disposisi.status == "diarsipkan"
    ).all():
        surat    = db.query(models.Surat).filter(models.Surat.id == d.surat_id).first()
        pengirim = db.query(models.Pegawai).filter(models.Pegawai.nip == d.nip_dari).first()
        hasil.append({
            "tanggal"        : str(d.tanggal),
            "tipe_aksi"      : "disposisi",
            "label_aksi"     : "🗄️ Mengarsipkan Disposisi",
            "jenis_surat"    : surat.jenis_surat if surat else None,
            "perihal"        : surat.perihal if surat else "—",
            "nomor_surat"    : surat.nomor_surat if surat else None,
            "pihak_terkait"  : pengirim.nama if pengirim else "—",
            "jabatan_terkait": pengirim.jabatan if pengirim else "",
            "surat_id"       : d.surat_id,
            "catatan"        : d.catatan,
        })

    # 7. Penilaian layanan yang saya berikan
    for p in db.query(models.PenilaianLayanan).filter(models.PenilaianLayanan.nip_penilai == nip).all():
        root = db.query(models.Surat).filter(models.Surat.id == p.surat_root_id).first()
        hasil.append({
            "tanggal"        : str(p.tanggal_nilai),
            "tipe_aksi"      : "nilai_diberikan",
            "label_aksi"     : f"⭐ Memberi Nilai ({'★' * p.bintang}{'☆' * (5 - p.bintang)})",
            "jenis_surat"    : root.jenis_surat if root else None,
            "perihal"        : root.perihal if root else "—",
            "nomor_surat"    : None,
            "pihak_terkait"  : "—",
            "jabatan_terkait": "",
            "surat_id"       : root.id if root else None,
            "catatan"        : p.catatan,
        })

    # 9. Notulensi & daftar hadir rapat yang sudah final
    #    Notulen (PDF): pimpinan rapat + Direktur. Daftar hadir: semua yang terlibat + Direktur.
    is_direktur = _is_direktur(nip, db)
    for lap in db.query(models.LaporanRapat).filter(
        models.LaporanRapat.status == "final"
    ).all():
        surat_r = db.query(models.Surat).filter(models.Surat.id == lap.surat_id).first()
        if not surat_r:
            continue
        peserta_r = _daftar_peserta_rapat(lap, db)
        notulen_r = next((p for p in peserta_r if p["is_notulen"]), None)
        jadi_pimpinan = surat_r.nip_pimpinan_rapat == nip
        jadi_notulen  = bool(notulen_r and notulen_r["nip"] == nip)
        jadi_peserta  = any(p["nip"] == nip for p in peserta_r)
        if not (jadi_pimpinan or jadi_notulen or jadi_peserta or is_direktur):
            continue
        boleh_notulen = jadi_pimpinan or is_direktur

        if jadi_pimpinan:
            label = "🔏 Menandatangani Notulensi & Daftar Hadir Rapat"
            pihak = notulen_r["nama"] if notulen_r else "—"
            jabatan_pihak = "Notulen rapat"
        elif is_direktur:
            label = "📋 Notulen & Daftar Hadir Rapat — Final (akses Direktur)"
            pihak = _nama_pegawai(surat_r.nip_pimpinan_rapat, db)
            jabatan_pihak = "Pimpinan rapat"
        elif jadi_notulen:
            label = "📋 Daftar Hadir Rapat — Final (notulen diarsipkan pimpinan)"
            pihak = _nama_pegawai(surat_r.nip_pimpinan_rapat, db)
            jabatan_pihak = "Pimpinan rapat"
        else:
            label = "📋 Daftar Hadir Rapat — Final"
            pihak = _nama_pegawai(surat_r.nip_pimpinan_rapat, db)
            jabatan_pihak = "Pimpinan rapat"

        hasil.append({
            "tanggal"          : str(lap.tanggal_final or lap.tanggal_kirim or lap.tanggal_dibuat),
            "tipe_aksi"        : "tte" if jadi_pimpinan else "tugas",
            "label_aksi"       : label,
            "jenis_surat"      : None,
            "perihal"          : surat_r.agenda_rapat or surat_r.perihal,
            "nomor_surat"      : None,
            "pihak_terkait"    : pihak,
            "jabatan_terkait"  : jabatan_pihak,
            "surat_id"         : surat_r.id,
            "catatan"          : "Notulensi final dan terkunci" if boleh_notulen else "Daftar hadir rapat (sudah di-TTE pimpinan)",
            "url_notulen"      : (f"/api/rapat/notulen-file/{lap.id}?nip={nip}"
                                  if boleh_notulen and lap.file_notulen else None),
            "file_daftar_hadir": lap.file_daftar_hadir,
        })

    # 8. Penilaian layanan yang saya terima
    for item in get_penilaian_layanan_diterima(nip=nip, db=db):
        hasil.append({
            "tanggal"        : item["tanggal_nilai"],
            "tipe_aksi"      : "nilai_diterima",
            "label_aksi"     : f"🌟 Menerima Nilai ({'★' * item['bintang']}{'☆' * (5 - item['bintang'])})",
            "jenis_surat"    : None,
            "perihal"        : item["perihal_utas"],
            "nomor_surat"    : None,
            "pihak_terkait"  : item["nama_penilai"],
            "jabatan_terkait": item["jabatan_penilai"],
            "surat_id"       : None,
            "surat_ref_id"   : item["surat_root_id"],   # hanya untuk pengelompokan kasus
            "catatan"        : item["catatan"],
        })


    hasil.sort(key=lambda x: x["tanggal"] or "", reverse=True)
    return hasil

# ==========================================================================
# 15. API DELEGASI MANAJEMEN SURAT (level 4 -> staf)
# ==========================================================================
class DelegasiCreate(BaseModel):
    nip_pemberi: str
    nip_staf: str


class DelegasiCabut(BaseModel):
    nip_pemberi: str


def punya_delegasi_aktif(nip: str, db: Session) -> bool:
    return db.query(models.DelegasiSurat).filter(
        models.DelegasiSurat.nip_staf == nip,
        models.DelegasiSurat.aktif == True
    ).first() is not None


def pastikan_boleh_kelola_surat(nip: str, db: Session):
    """Staf hanya boleh membuat/mengirim surat jika punya delegasi aktif."""
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        raise HTTPException(404, "Pegawai tidak ditemukan")
    if pegawai.role_ppnp == "staf" and not punya_delegasi_aktif(nip, db):
        raise HTTPException(403, "Anda belum mendapat delegasi manajemen surat dari atasan")


def _ambil_pejabat_level4(nip: str, db: Session):
    pemberi = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pemberi:
        raise HTTPException(404, "Pegawai tidak ditemukan")
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pemberi.kode_unit
    ).first()
    if pemberi.role_ppnp == "staf" or not unit or unit.level not in (3, 4):
        raise HTTPException(403, "Hanya pejabat level 3 dan 4 yang boleh mengelola delegasi")
    return pemberi


@app.post("/api/delegasi")
def beri_delegasi(req: DelegasiCreate, db: Session = Depends(get_db)):
    pemberi = _ambil_pejabat_level4(req.nip_pemberi, db)
    staf = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_staf).first()
    if not staf or staf.role_ppnp != "staf":
        raise HTTPException(400, "Penerima delegasi harus berstatus staf")
    if staf.kode_unit != pemberi.kode_unit:
        raise HTTPException(400, "Staf harus berada di unit yang sama")
    if punya_delegasi_aktif(staf.nip, db):
        raise HTTPException(400, "Staf ini sudah memiliki delegasi aktif")

    db.add(models.DelegasiSurat(nip_pemberi=pemberi.nip, nip_staf=staf.nip, aktif=True))
    db.commit()
    return {"message": f"Delegasi diberikan kepada {staf.nama}"}


@app.post("/api/delegasi/{delegasi_id}/cabut")
def cabut_delegasi(delegasi_id: int, req: DelegasiCabut, db: Session = Depends(get_db)):
    _ambil_pejabat_level4(req.nip_pemberi, db)
    d = db.query(models.DelegasiSurat).filter(models.DelegasiSurat.id == delegasi_id).first()
    if not d or not d.aktif:
        raise HTTPException(404, "Delegasi aktif tidak ditemukan")
    if d.nip_pemberi != req.nip_pemberi:
        raise HTTPException(403, "Hanya pemberi delegasi yang boleh mencabutnya")
    d.aktif = False
    d.tanggal_dicabut = datetime.utcnow()
    db.commit()
    return {"message": "Delegasi dicabut"}


@app.get("/api/delegasi")
def daftar_delegasi(nip: str, db: Session = Depends(get_db)):
    """Staf di unit pejabat level 4 beserta status delegasinya."""
    pemberi = _ambil_pejabat_level4(nip, db)
    hasil = []
    for s in db.query(models.Pegawai).filter(
        models.Pegawai.kode_unit == pemberi.kode_unit,
        models.Pegawai.role_ppnp == "staf"
    ).all():
        d = db.query(models.DelegasiSurat).filter(
            models.DelegasiSurat.nip_staf == s.nip,
            models.DelegasiSurat.aktif == True
        ).first()
        hasil.append({
            "nip": s.nip, "nama": s.nama, "jabatan": s.jabatan,
            "delegasi_id": d.id if d else None, "aktif": d is not None
        })
    return hasil


@app.get("/api/delegasi-saya")
def delegasi_saya(nip: str, db: Session = Depends(get_db)):
    return {"punya_delegasi": punya_delegasi_aktif(nip, db)}



# ==========================================================================
# 16. SURAT TUGAS -> PENUGASAN OTOMATIS PER PEGAWAI
# ==========================================================================
BATAS_LAPOR_HARI = 7   # batas lapor = tanggal selesai pelaksanaan + N hari (pengingat, tidak mengunci)

def validasi_rapat(req, db: Session):
    """Validasi khusus Surat Tugas jenis Rapat."""
    if req.jenis_surat != "permohonan":
        raise HTTPException(400, "Jenis tugas rapat hanya untuk surat tugas (permohonan)")
    daftar = list(dict.fromkeys(req.pegawai_ditugaskan or []))
    if not daftar:
        raise HTTPException(400, "Undangan rapat wajib memilih minimal 1 peserta")
    if not (req.agenda_rapat or "").strip():
        raise HTTPException(400, "Agenda rapat wajib diisi")
    if not (req.tempat_tugas or "").strip():
        raise HTTPException(400, "Tempat rapat wajib diisi")
    if not req.tanggal_tugas_mulai:
        raise HTTPException(400, "Tanggal rapat wajib diisi")

    pola_jam = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    jam_mulai   = (req.jam_mulai or "").strip()
    jam_selesai = (req.jam_selesai or "").strip()
    if not pola_jam.match(jam_mulai):
        raise HTTPException(400, "Jam mulai wajib diisi (format HH:MM)")
    if jam_selesai:
        if not pola_jam.match(jam_selesai):
            raise HTTPException(400, "Format jam selesai harus HH:MM")
        if jam_selesai <= jam_mulai:
            raise HTTPException(400, "Jam selesai harus setelah jam mulai")

    if not req.nip_pimpinan_rapat:
        raise HTTPException(400, "Pimpinan rapat wajib dipilih")
    pimpinan = db.query(models.Pegawai).filter(
        models.Pegawai.nip == req.nip_pimpinan_rapat
    ).first()
    if not pimpinan:
        raise HTTPException(404, "Pimpinan rapat tidak ditemukan")
    if pimpinan.role_ppnp == "staf":
        raise HTTPException(400, "Pimpinan rapat harus pejabat (bukan staf)")

    nip_notulen = (req.nip_notulen or "").strip()
    if not nip_notulen:
        raise HTTPException(400, "Notulen wajib dipilih")
    if nip_notulen not in daftar:
        raise HTTPException(400, "Notulen harus salah satu peserta rapat")

def validasi_surat_tugas(req, db: Session):
    """Validasi daftar pegawai & tanggal pelaksanaan surat tugas."""
    if req.jenis_tugas and req.jenis_tugas not in JENIS_TUGAS_VALID:
        raise HTTPException(400, "Jenis surat tugas tidak dikenali")
    if req.jenis_tugas == "rapat":
        validasi_rapat(req, db)
        req.tanggal_tugas_selesai = req.tanggal_tugas_mulai   # rapat satu hari
    if req.jenis_tugas == "perjalanan_dinas":
        if not (req.pegawai_ditugaskan or []):
            raise HTTPException(400, "Surat perjalanan dinas wajib memilih pegawai yang ditugaskan")
        if not (req.tempat_tugas or "").strip():
            raise HTTPException(400, "Tempat pelaksanaan wajib diisi untuk perjalanan dinas")
    daftar = req.pegawai_ditugaskan or []
    if not daftar:
        if req.penugasan_asal_id:
            raise HTTPException(400, "Surat tugas wajib memilih minimal 1 pegawai yang ditugaskan")
        return
    if req.jenis_surat != "permohonan":
        raise HTTPException(400, "Daftar pegawai ditugaskan hanya untuk surat permohonan (surat tugas)")
    if not req.tanggal_tugas_selesai:
        raise HTTPException(400, "Tanggal selesai pelaksanaan wajib diisi jika ada pegawai yang ditugaskan")

    for tgl in [req.tanggal_tugas_mulai, req.tanggal_tugas_selesai]:
        if tgl:
            try:
                datetime.strptime(tgl, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Format tanggal pelaksanaan harus YYYY-MM-DD")
    if req.tanggal_tugas_mulai and req.tanggal_tugas_mulai > req.tanggal_tugas_selesai:
        raise HTTPException(400, "Tanggal mulai tidak boleh setelah tanggal selesai")

    for nip in set(daftar):
        if not db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first():
            raise HTTPException(404, f"Pegawai dengan NIP {nip} tidak ditemukan")

def simpan_penerima_surat_tugas(surat, daftar_nip, db: Session, nip_notulen: Optional[str] = None):
    for nip in dict.fromkeys(daftar_nip or []):   # hilangkan duplikat, urutan tetap
        db.add(models.PenerimaSuratTugas(
            surat_id=surat.id, nip_pegawai=nip,
            is_notulen=bool(nip_notulen and nip == nip_notulen)
        ))


def daftar_pegawai_ditugaskan(surat_id: int, db: Session) -> list:
    hasil = []
    for r in db.query(models.PenerimaSuratTugas).filter(
        models.PenerimaSuratTugas.surat_id == surat_id
    ).all():
        p = db.query(models.Pegawai).filter(models.Pegawai.nip == r.nip_pegawai).first()
        hasil.append({"nip": r.nip_pegawai, "nama": p.nama if p else r.nip_pegawai,
                      "is_notulen": bool(r.is_notulen)})
    return hasil

def cari_atasan_langsung(nip: str, db: Session) -> Optional[str]:
    """
    Atasan langsung = pejabat (non-staf) terdekat.
    - Staf   : pejabat di unitnya sendiri.
    - Pejabat: naik ke unit induk, cari pejabat di sana (terus naik jika kosong).
    """
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        return None
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()
    if not unit:
        return None

    kode = unit.kode_unit if pegawai.role_ppnp == "staf" else unit.kode_induk
    while kode:
        u = db.query(models.UnitOrganisasi).filter(models.UnitOrganisasi.kode_unit == kode).first()
        if not u:
            break
        pejabat = db.query(models.Pegawai).filter(
            models.Pegawai.kode_unit == kode,
            models.Pegawai.role_ppnp != "staf",
            models.Pegawai.nip != nip
        ).first()
        if pejabat:
            return pejabat.nip
        kode = u.kode_induk
    return None


def buat_penugasan_dari_surat_tugas(surat, db: Session):
    """Setelah surat tugas SELESAI (TTE Direktur), buat 1 penugasan per pegawai. Aman dipanggil ulang."""
    if not surat or surat.jenis_surat != "permohonan":
        return
    penerima_list = db.query(models.PenerimaSuratTugas).filter(
        models.PenerimaSuratTugas.surat_id == surat.id
    ).all()
    if not penerima_list:
        return

    # ── Rapat: pemberi tugas = pimpinan rapat, tanpa deadline & tanpa penilaian ──
    if surat.jenis_tugas == "rapat":
        agenda = surat.agenda_rapat or surat.perihal
        for pn in penerima_list:
            if db.query(models.Penugasan).filter(
                models.Penugasan.surat_id == surat.id,
                models.Penugasan.nip_penerima == pn.nip_pegawai
            ).first():
                continue
            if pn.is_notulen:
                deskripsi = f"Menghadiri rapat dan menyusun notulensi: {agenda}."
            else:
                deskripsi = f"Menghadiri rapat: {agenda}. Konfirmasi kehadiran saat rapat berlangsung."
            db.add(models.Penugasan(
                surat_id        = surat.id,
                disposisi_id    = None,
                nip_pemberi     = surat.nip_pimpinan_rapat,
                nip_penerima    = pn.nip_pegawai,
                deskripsi_tugas = deskripsi,
                deadline        = None,
                status          = "proses",
            ))
        return

    deadline = None
    if surat.tanggal_tugas_selesai:
        try:
            deadline = datetime.strptime(surat.tanggal_tugas_selesai, "%Y-%m-%d") + timedelta(days=BATAS_LAPOR_HARI)
        except ValueError:
            deadline = None

    direktur_unit = db.query(models.UnitOrganisasi).filter(models.UnitOrganisasi.level == 1).first()
    nip_fallback = direktur_unit.nip_pimpinan if direktur_unit else None

    periode = ""
    if surat.tanggal_tugas_mulai and surat.tanggal_tugas_selesai:
        periode = f" Pelaksanaan: {surat.tanggal_tugas_mulai} s.d. {surat.tanggal_tugas_selesai}."
    elif surat.tanggal_tugas_selesai:
        periode = f" Pelaksanaan sampai: {surat.tanggal_tugas_selesai}."
    deskripsi = (f"Melaksanakan penugasan sesuai Surat Tugas No. {surat.nomor_surat or '-'}: "
                 f"{surat.perihal}.{periode} Kirim laporan (PDF) setelah tugas selesai.")

    for pn in penerima_list:
        sudah_ada = db.query(models.Penugasan).filter(
            models.Penugasan.surat_id == surat.id,
            models.Penugasan.nip_penerima == pn.nip_pegawai
        ).first()
        if sudah_ada:
            continue

        nip_penilai = cari_atasan_langsung(pn.nip_pegawai, db) or nip_fallback
        if not nip_penilai:
            continue   # tidak ada penilai sama sekali, lewati

        db.add(models.Penugasan(
            surat_id        = surat.id,
            disposisi_id    = None,
            nip_pemberi     = nip_penilai,       # atasan langsung = penilai laporan
            nip_penerima    = pn.nip_pegawai,
            deskripsi_tugas = deskripsi,
            deadline        = deadline,
            status          = "proses",
        ))


@app.get("/api/pegawai-daftar")
def get_pegawai_daftar(db: Session = Depends(get_db)):
    """Daftar semua pegawai, untuk dipilih sebagai pelaksana surat tugas."""
    unit_map = {u.kode_unit: u.nama_unit for u in db.query(models.UnitOrganisasi).all()}
    return [
        {
            "nip"      : p.nip,
            "nama"     : p.nama,
            "jabatan"  : p.jabatan,
            "nama_unit": unit_map.get(p.kode_unit, "—"),
            "role_ppnp": p.role_ppnp,
        }
        for p in db.query(models.Pegawai).order_by(models.Pegawai.nama).all()
    ]

def info_surat_tugas(penugasan_id: int, db: Session) -> dict:
    """Surat tugas (permohonan) yang dibuat dari sebuah penugasan, jika ada."""
    s = db.query(models.Surat).filter(
        models.Surat.penugasan_asal_id == penugasan_id
    ).order_by(models.Surat.id.desc()).first()
    return {
        "surat_tugas_id"    : s.id     if s else None,
        "surat_tugas_status": s.status if s else None,
    }



# ==========================================================================
# 17. ATASAN TUJUAN untuk tombol "Lanjutkan ke Atasan"
# ==========================================================================
@app.get("/api/atasan-tujuan")
def get_atasan_tujuan(nip: str, db: Session = Depends(get_db)):
    """
    Pimpinan unit induk terdekat dari unit pegawai ini.
    ada=False jika tidak ada atasan atau atasan itu tidak boleh dituju
    (unit tanpa kop sendiri tidak boleh bersurat ke level 1-2).
    """
    kosong = {"ada": False, "nip": None, "nama": None, "nama_unit": None}

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai:
        return kosong
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()
    if not unit:
        return kosong

    punya_kop = bool(unit.punya_kop_sendiri)
    kode = unit.kode_induk
    dikunjungi = set()
    while kode and kode not in dikunjungi:
        dikunjungi.add(kode)
        u = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == kode
        ).first()
        if not u:
            break
        if u.nip_pimpinan and u.nip_pimpinan != nip:
            if punya_kop or u.level > 2:
                return {"ada": True, "nip": u.nip_pimpinan,
                        "nama": u.nama_pimpinan, "nama_unit": u.nama_unit}
            return kosong
        kode = u.kode_induk
    return kosong

def jenis_paraf_unit(unit) -> str:
    """Level 4 berstatus Ketua Tim cukup 'Mengetahui'; selain itu 'Paraf'."""
    if unit and unit.level == 4:
        teks = f"{unit.nama_unit or ''} {unit.jabatan_surat or ''}".lower()
        if "tim kerja" in teks or "ketua tim" in teks:
            return "Mengetahui"
    return "Paraf"



# ==========================================================================
# 18. TARIK KEMBALI DISPOSISI (salah penerima)
# ==========================================================================
class TarikDisposisiRequest(BaseModel):
    nip_pengirim: str


def disposisi_bisa_ditarik(d, db: Session) -> bool:
    """Hanya jika penerima belum memproses: belum diteruskan, ditugaskan, atau diarsipkan."""
    if d.status != "diteruskan":
        return False
    if db.query(models.Disposisi).filter(models.Disposisi.disposisi_asal_id == d.id).first():
        return False
    if db.query(models.Penugasan).filter(models.Penugasan.disposisi_id == d.id).first():
        return False
    return True


@app.post("/api/disposisi/{disposisi_id}/tarik")
def tarik_disposisi(disposisi_id: int, req: TarikDisposisiRequest, db: Session = Depends(get_db)):
    d = db.query(models.Disposisi).filter(models.Disposisi.id == disposisi_id).first()
    if not d:
        raise HTTPException(404, "Disposisi tidak ditemukan")
    if d.nip_dari != req.nip_pengirim:
        raise HTTPException(403, "Hanya pengirim disposisi yang boleh menariknya")
    if not disposisi_bisa_ditarik(d, db):
        raise HTTPException(400, "Disposisi sudah diproses penerima, tidak bisa ditarik lagi")

    surat_id = d.surat_id
    db.delete(d)
    db.flush()

    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    sisa_disposisi = db.query(models.Disposisi).filter(models.Disposisi.surat_id == surat_id).count()
    sisa_tugas     = db.query(models.Penugasan).filter(models.Penugasan.surat_id == surat_id).count()
    if surat and sisa_disposisi == 0 and sisa_tugas == 0:
        # kembali ke keadaan awal: menunggu didisposisikan Direktur
        surat.status = "masuk" if surat.jenis_surat == "eksternal" else "selesai"
    else:
        hitung_ulang_status_surat(surat_id, db)

    db.commit()
    return {"message": "Disposisi berhasil ditarik. Silakan disposisikan ulang ke penerima yang benar."}


def nama_pemberi_tampil(pemberi, surat, db: Session) -> str:
    # Rapat: pemberi tugas yang tersimpan sudah benar (pimpinan rapat, atau pejabat yang mewakilkan)
    if surat and surat.jenis_tugas == "rapat":
        return pemberi.nama if pemberi else "—"
    """Surat tugas (permohonan): perintah datang dari penandatangan TTE Utama (Direktur).
    Penugasan biasa: pemberi tugas sebenarnya."""
    if surat and surat.jenis_surat == "permohonan":
        tte = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id  == surat.id,
            models.PersetujuanSurat.jenis_ttd == "TTE Utama"
        ).first()
        if tte:
            penanda = db.query(models.Pegawai).filter(models.Pegawai.nip == tte.nip_penyetuju).first()
            if penanda:
                return penanda.nama
    return pemberi.nama if pemberi else "—"

# ==========================================================================
# 19. LAPORAN PERJALANAN DINAS (1 laporan bersama, semua peserta menandatangani)
# ==========================================================================
from html import escape as _esc
from sqlalchemy.exc import IntegrityError

JENIS_TUGAS_VALID = ["perjalanan_dinas", "rapat"]
HARI_ID  = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def tanggal_indo(teks, dengan_hari=False) -> str:
    try:
        d = datetime.strptime(str(teks)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return "-"
    hasil = f"{d.day} {BULAN_ID[d.month - 1]} {d.year}"
    return f"{HARI_ID[d.weekday()]}, {hasil}" if dengan_hari else hasil


def waktu_ttd_str(dt) -> str:
    """Waktu TTE dalam WIB, contoh: '2 Okt 2026 10:15'."""
    if not dt:
        return ""
    w = dt + timedelta(hours=7)
    return f"{w.day} {BULAN_ID[w.month - 1][:3]} {w.year} {w.hour:02d}:{w.minute:02d}"


def _penugasan_milik(penugasan_id: int, nip: str, db: Session):
    p = db.query(models.Penugasan).filter(models.Penugasan.id == penugasan_id).first()
    if not p:
        raise HTTPException(404, "Penugasan tidak ditemukan")
    if p.nip_penerima != nip:
        raise HTTPException(403, "Penugasan ini bukan untuk Anda")
    return p


def _surat_pd(penugasan, db: Session):
    surat = db.query(models.Surat).filter(models.Surat.id == penugasan.surat_id).first()
    if not surat or surat.jenis_tugas != "perjalanan_dinas":
        raise HTTPException(400, "Penugasan ini bukan perjalanan dinas")
    return surat


def _laporan_pd_surat(surat_id: int, db: Session):
    return db.query(models.LaporanPD).filter(models.LaporanPD.surat_id == surat_id).first()


def daftar_peserta_pd(surat, laporan, db: Session) -> list:
    """Peserta laporan. Jika laporan sudah ada: dari laporan; jika belum: dari penugasan surat tugas."""
    hasil = []
    if laporan:
        for r in db.query(models.LaporanPDPeserta).filter(
            models.LaporanPDPeserta.laporan_id == laporan.id
        ).order_by(models.LaporanPDPeserta.id).all():
            pg = db.query(models.Pegawai).filter(models.Pegawai.nip == r.nip_pegawai).first()
            hasil.append({"nip": r.nip_pegawai, "nama": pg.nama if pg else r.nip_pegawai,
                          "penugasan_id": r.penugasan_id, "sudah_ttd": bool(r.sudah_ttd),
                          "tanggal_ttd": r.tanggal_ttd})
    else:
        for t in db.query(models.Penugasan).filter(
            models.Penugasan.surat_id == surat.id
        ).order_by(models.Penugasan.id).all():
            pg = db.query(models.Pegawai).filter(models.Pegawai.nip == t.nip_penerima).first()
            hasil.append({"nip": t.nip_penerima, "nama": pg.nama if pg else t.nip_penerima,
                          "penugasan_id": t.id, "sudah_ttd": False, "tanggal_ttd": None})
    return hasil


def data_laporan_pd(surat, peserta: list) -> dict:
    mulai, selesai = surat.tanggal_tugas_mulai, surat.tanggal_tugas_selesai
    awal = mulai or selesai
    if awal and selesai and awal[:10] != selesai[:10]:
        hari_tanggal = f"{tanggal_indo(awal, True)} s.d. {tanggal_indo(selesai, True)}"
    else:
        hari_tanggal = tanggal_indo(awal or selesai, True)
    return {
        "tahun"        : (awal or datetime.utcnow().strftime("%Y"))[:4],
        "nomor_surat"  : surat.nomor_surat or "-",
        "tanggal_surat": tanggal_indo(surat.tanggal_surat),
        "hari_tanggal" : hari_tanggal,
        "peserta"      : peserta,
        "kegiatan"     : re.sub(r"^Surat Tugas:\s*", "", surat.perihal or "", flags=re.I),
        "tempat"       : surat.tempat_tugas or "",
    }


def buat_pdf_laporan_pd(data: dict, hasil_kegiatan: str, tempat: str, tanggal_laporan: str,
                        path_fisik: str, url_verifikasi: Optional[str] = None):
    e = lambda t: _esc(str(t or "")).replace("\n", "<br>")
    arsip = pymupdf.Archive()

    # Kop: kop surat Direktur (uploads/assets); jika tidak ada, kop teks
    nama_kop = MAPPING_KOP.get("D001")
    path_kop = f"uploads/assets/{nama_kop}" if nama_kop else None
    if path_kop and os.path.exists(path_kop):
        with open(path_kop, "rb") as f:
            arsip.add(f.read(), "kop.png")
        kop = '<img src="kop.png" width="483"><hr>'
    else:
        logo = ""
        if os.path.exists(PATH_LOGO):
            with open(PATH_LOGO, "rb") as f:
                arsip.add(f.read(), "logo.png")
            logo = '<img src="logo.png" width="60">'
        kop = f"""
        <table class="kop"><tr>
          <td width="70">{logo}</td>
          <td class="tengah">KEMENTERIAN PENDIDIKAN TINGGI, SAINS, DAN TEKNOLOGI<br>
          <b class="besar">POLITEKNIK PERTANIAN NEGERI PAYAKUMBUH</b><br>
          Jalan Raya Negara KM.7 Tanjung Pati 26271 Kecamatan Harau<br>
          Kabupaten Limapuluh Kota - Sumatera Barat<br>
          Telepon. (0752) 77 54192 - Faksimili (0752) 7750220<br>
          Surel : sekretariat@politanipyk.ac.id Laman : http://politanipyk.ac.id</td>
        </tr></table><hr>"""

    # QR TTE (sama untuk semua baris yang sudah menandatangani)
    ada_ttd = bool(url_verifikasi) and any(p.get("tanggal_ttd") for p in data["peserta"])
    if ada_ttd:
        arsip.add(buat_qr_png(url_verifikasi), "qr.png")

    baris = ""
    for i, p in enumerate(data["peserta"], 1):
        if ada_ttd and p.get("tanggal_ttd"):
            sel = (f'<img src="qr.png" width="50"><br>'
                   f'<span class="kecil">SAH - TTE: {e(waktu_ttd_str(p["tanggal_ttd"]))}</span>')
        else:
            sel = "&nbsp;"
        baris += (f"<tr><td class='c'>{i}</td>"
                  f"<td>{e(p['nama'])}<br><span class='kecil'>NIP. {e(p['nip'])}</span></td>"
                  f"<td class='c'>{sel}</td></tr>")
    peserta_teks = "<br>".join(f"{i}. {e(p['nama'])}" for i, p in enumerate(data["peserta"], 1))

    html = f"""
    {kop}
    <h3 class="tengah">LAPORAN PERJALANAN DINAS</h3>
    <p><b>DASAR</b></p>
    <table class="dasar" width="100%">
      <tr><td width="22">1.</td><td width="105">Peraturan</td><td width="10">:</td>
          <td width="18">1.</td>
          <td>DIPA Politeknik Pertanian Negeri Payakumbuh Tahun {e(data['tahun'])}</td></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>
          <td>2.</td>
          <td>Surat Tugas Direktur Politeknik Pertanian Negeri Payakumbuh Nomor: {e(data['nomor_surat'])} tanggal {e(data['tanggal_surat'])}</td></tr>
      <tr><td>2.</td><td>Hari/Tanggal</td><td>:</td><td colspan="2">{e(data['hari_tanggal'])}</td></tr>
      <tr><td>3.</td><td>Peserta</td><td>:</td><td colspan="2">{peserta_teks}</td></tr>
      <tr><td>4.</td><td>Kegiatan/Acara</td><td>:</td><td colspan="2">{e(data['kegiatan'])}</td></tr>
      <tr><td>5.</td><td>Tempat</td><td>:</td><td colspan="2">{e(tempat)}</td></tr>
    </table>
    <p><b>HASIL KEGIATAN</b></p>
    <p class="isi">{e(hasil_kegiatan)}</p>
    <p class="kanan">Tanjung Pati, {e(tanggal_laporan)}</p>
    <p>Yang Melaksanakan Perjalanan</p>
    <table class="ttd"><tr><th width="30">NO</th><th>NAMA</th><th width="150">TANDA TANGAN</th></tr>{baris}</table>
    """
    css = """
    body { font-family: serif; font-size: 11pt; }
    .tengah { text-align: center; } .kanan { text-align: right; }
    .besar { font-size: 14pt; } .kecil { font-size: 8pt; }
    .kop td { font-size: 9pt; }
    .isi { text-align: justify; line-height: 1.4; }
    .dasar td { vertical-align: top; padding: 2px; }
    .ttd { border: 1px solid black; border-collapse: collapse; width: 100%; }
    .ttd th, .ttd td { border: 1px solid black; padding: 8px; }
    .c { text-align: center; }
    """
    story = pymupdf.Story(html=html, user_css=css, archive=arsip)
    writer = pymupdf.DocumentWriter(path_fisik)
    media = pymupdf.paper_rect("a4")
    area = media + (56, 40, -56, -50)
    lagi = 1
    while lagi:
        dev = writer.begin_page(media)
        lagi, _ = story.place(area)
        story.draw(dev)
        writer.end_page()
    writer.close()


def _tulis_pdf_laporan(laporan, surat, db: Session):
    """Susun ulang PDF dari data terbaru (dipanggil saat dibuat, diedit, dan setiap ada TTE)."""
    peserta = daftar_peserta_pd(surat, laporan, db)
    data = data_laporan_pd(surat, peserta)
    wib = (laporan.tanggal_dibuat or datetime.utcnow()) + timedelta(hours=7)
    tgl_laporan = tanggal_indo(wib.strftime("%Y-%m-%d"))
    kode = f"LPD-{laporan.id}"
    url = f"{BASE_URL}/verifikasi-laporan?kode={kode}&sig={buat_signature(kode)}"

    sekarang = datetime.utcnow()
    folder = f"bukti_tugas/{sekarang.strftime('%Y')}/{sekarang.strftime('%m')}"
    os.makedirs(f"uploads/{folder}", exist_ok=True)
    path_rel = f"{folder}/laporan_pd_{laporan.id}_{int(time.time())}.pdf"

    buat_pdf_laporan_pd(data, laporan.hasil_kegiatan, laporan.tempat, tgl_laporan,
                        f"uploads/{path_rel}", url)
    _gabung_lampiran_pdf(f"uploads/{path_rel}", laporan.id, db)
    hapus_file_upload(laporan.file_pdf)   # buang versi lama
    laporan.file_pdf = path_rel

class LampiranPDItem(BaseModel):
    file: str
    nama: str


def _keluarkan_peserta_pd(laporan, daftar_nip, nip_editor: str, db: Session):
    """Coret peserta yang tidak jadi berangkat: namanya dihapus dari laporan, tugasnya dibatalkan."""
    for nip_k in set(daftar_nip or []):
        if nip_k == nip_editor:
            raise HTTPException(400, "Anda tidak bisa mengeluarkan diri sendiri dari laporan")
        r = db.query(models.LaporanPDPeserta).filter(
            models.LaporanPDPeserta.laporan_id == laporan.id,
            models.LaporanPDPeserta.nip_pegawai == nip_k
        ).first()
        if not r:
            continue
        if r.penugasan_id:
            pen = db.query(models.Penugasan).filter(models.Penugasan.id == r.penugasan_id).first()
            sudah_lapor = db.query(models.LaporanTugas).filter(
                models.LaporanTugas.penugasan_id == r.penugasan_id
            ).first()
            if pen and pen.status == "proses" and not sudah_lapor:
                db.delete(pen)
        if nip_k == laporan.nip_penyusun:
            laporan.nip_penyusun = nip_editor      # peran penyusun pindah ke yang mengedit
        db.delete(r)
    db.flush()


def _simpan_lampiran_pd(laporan, daftar, db: Session):
    """Samakan lampiran laporan dengan daftar yang dikirim (tambah yang baru, hapus yang dibuang)."""
    awalan = "bukti_tugas/lampiran_pd/"
    unik = {}
    for it in (daftar or []):
        if not it.file.startswith(awalan) or ".." in it.file:
            raise HTTPException(400, "Lampiran tidak valid")
        if not os.path.exists(f"uploads/{it.file}"):
            raise HTTPException(400, f"Lampiran '{it.nama}' tidak ditemukan di server, unggah ulang")
        unik[it.file] = it.nama
    if len(unik) > 10:
        raise HTTPException(400, "Maksimal 10 lampiran")

    lama = db.query(models.LaporanPDLampiran).filter(
        models.LaporanPDLampiran.laporan_id == laporan.id
    ).all()
    for l in lama:
        if l.file_path not in unik:
            hapus_file_upload(l.file_path)
            db.delete(l)
    path_lama = {l.file_path for l in lama}
    for path, nama in unik.items():
        if path not in path_lama:
            db.add(models.LaporanPDLampiran(laporan_id=laporan.id, file_path=path, nama_asli=nama))
    db.flush()


def _gabung_lampiran_pdf(path_fisik: str, laporan_id: int, db: Session):
    """Tempelkan semua lampiran (foto/PDF) di belakang PDF laporan, jadi satu berkas."""
    rows = db.query(models.LaporanPDLampiran).filter(
        models.LaporanPDLampiran.laporan_id == laporan_id
    ).order_by(models.LaporanPDLampiran.id).all()
    if not rows:
        return
    doc = pymupdf.open(path_fisik)
    for i, r in enumerate(rows, 1):
        fisik = f"uploads/{r.file_path}"
        if not os.path.exists(fisik):
            continue
        awal = len(doc)
        try:
            if fisik.lower().endswith(".pdf"):
                src = pymupdf.open(fisik)
                doc.insert_pdf(src)
                src.close()
            else:
                page = doc.new_page(width=595, height=842)
                page.insert_image(pymupdf.Rect(36, 56, 559, 806), filename=fisik)
        except Exception as ex:
            print(f"⚠️ Lampiran dilewati ({r.nama_asli}): {ex}")
            continue
        if len(doc) > awal:
            doc[awal].insert_text((36, 28), f"Lampiran {i}: {r.nama_asli}"[:110],
                                  fontsize=9, fontname="helv", color=(0.3, 0.3, 0.3))
    _simpan_pdf_di_tempat(doc, path_fisik)


def info_laporan_pd(p, db: Session) -> dict:
    """Status laporan perjalanan dinas bersama untuk sebuah penugasan (kosong jika bukan perjalanan dinas)."""
    surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
    if not surat or surat.jenis_tugas != "perjalanan_dinas":
        return {}
    laporan = _laporan_pd_surat(surat.id, db)
    if not laporan:
        return {"laporan_pd_id": None}
    peserta = db.query(models.LaporanPDPeserta).filter(
        models.LaporanPDPeserta.laporan_id == laporan.id
    ).all()
    saya = next((x for x in peserta if x.nip_pegawai == p.nip_penerima), None)
    return {
        "laporan_pd_id"    : laporan.id,
        "laporan_pd_status": laporan.status,
        "laporan_pd_file"  : laporan.file_pdf,
        "saya_sudah_ttd"   : bool(saya and saya.sudah_ttd),
        "saya_penyusun"    : laporan.nip_penyusun == p.nip_penerima,
        "jml_ttd"          : sum(1 for x in peserta if x.sudah_ttd),
        "jml_peserta"      : len(peserta),
    }

class LaporanPDCreate(BaseModel):
    penugasan_id: int
    nip_pelapor: str
    tempat: str
    hasil_kegiatan: str
    nip_dikeluarkan: List[str] = []
    lampiran: List[LampiranPDItem] = []


class LaporanPDEdit(BaseModel):
    nip: str
    tempat: str
    hasil_kegiatan: str
    nip_dikeluarkan: List[str] = []
    lampiran: List[LampiranPDItem] = []


@app.post("/api/upload/lampiran-pd")
def upload_lampiran_pd(file: UploadFile = File(...)):
    """Unggah bukti perjalanan (tiket, kwitansi menginap, struk BBM, dll)."""
    nama = file.filename or ""
    ext = nama.rsplit(".", 1)[-1].lower() if "." in nama else ""
    if ext not in ["pdf", "jpg", "jpeg", "png"]:
        raise HTTPException(400, "Format lampiran harus PDF, JPG, atau PNG")

    file.file.seek(0, 2)
    ukuran = file.file.tell()
    file.file.seek(0)
    if ukuran == 0:
        raise HTTPException(400, "File kosong")
    if ukuran > 10 * 1024 * 1024:
        raise HTTPException(400, "Ukuran lampiran maksimal 10 MB")

    now = datetime.utcnow()
    folder = f"bukti_tugas/lampiran_pd/{now.strftime('%Y')}/{now.strftime('%m')}"
    os.makedirs(f"uploads/{folder}", exist_ok=True)
    bersih = re.sub(r"[^a-zA-Z0-9_\-]", "_", nama.rsplit(".", 1)[0])[:60] or "lampiran"
    path_rel = f"{folder}/{int(time.time() * 1000)}_{bersih}.{ext}"
    simpan_file_upload(file, path_rel)

    # pastikan isinya benar-benar bisa dibaca
    try:
        if ext == "pdf":
            d = pymupdf.open(f"uploads/{path_rel}")
            ok = d.page_count > 0 and not d.needs_pass
            d.close()
        else:
            pymupdf.Pixmap(f"uploads/{path_rel}")
            ok = True
    except Exception:
        ok = False
    if not ok:
        hapus_file_upload(path_rel)
        raise HTTPException(400, "File tidak dapat dibaca atau dikunci dengan password")

    return {"nama_file": path_rel, "nama_asli": nama}


@app.get("/api/laporan-pd/data")
def get_data_laporan_pd(penugasan_id: int, nip: str, db: Session = Depends(get_db)):
    p = _penugasan_milik(penugasan_id, nip, db)
    surat = _surat_pd(p, db)
    laporan = _laporan_pd_surat(surat.id, db)
    data = data_laporan_pd(surat, daftar_peserta_pd(surat, laporan, db))
    data["laporan"] = None if not laporan else {
        "id": laporan.id, "status": laporan.status, "nip_penyusun": laporan.nip_penyusun,
        "tempat": laporan.tempat, "hasil_kegiatan": laporan.hasil_kegiatan,
        "file_pdf": laporan.file_pdf,
    }
    data["saya_penyusun"] = bool(laporan and laporan.nip_penyusun == nip)
    data["lampiran"] = [] if not laporan else [
        {"path": l.file_path, "nama": l.nama_asli}
        for l in db.query(models.LaporanPDLampiran).filter(
            models.LaporanPDLampiran.laporan_id == laporan.id
        ).order_by(models.LaporanPDLampiran.id).all()
    ]
    return data


@app.post("/api/laporan-pd")
def buat_laporan_pd(req: LaporanPDCreate, db: Session = Depends(get_db)):
    """Peserta pertama yang bertindak menjadi penyusun. Laporan berstatus draft sampai semua TTE."""
    p = _penugasan_milik(req.penugasan_id, req.nip_pelapor, db)
    if p.status != "proses":
        raise HTTPException(400, "Penugasan ini sudah tidak dalam status proses")
    surat = _surat_pd(p, db)
    if _laporan_pd_surat(surat.id, db):
        raise HTTPException(400, "Laporan sudah disusun oleh rekan Anda. Silakan buka lagi lalu tanda tangani.")

    tempat = (req.tempat or "").strip()
    hasil  = (req.hasil_kegiatan or "").strip()
    if not tempat:
        raise HTTPException(400, "Tempat wajib diisi")
    if not hasil:
        raise HTTPException(400, "Hasil kegiatan wajib diisi")

    laporan = models.LaporanPD(surat_id=surat.id, nip_penyusun=req.nip_pelapor,
                               tempat=tempat, hasil_kegiatan=hasil, status="draft")
    db.add(laporan)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Laporan sudah disusun oleh rekan Anda. Silakan buka lagi lalu tanda tangani.")

    for t in db.query(models.Penugasan).filter(models.Penugasan.surat_id == surat.id).all():
        db.add(models.LaporanPDPeserta(laporan_id=laporan.id, nip_pegawai=t.nip_penerima, penugasan_id=t.id))
    db.flush()
    _keluarkan_peserta_pd(laporan, req.nip_dikeluarkan, req.nip_pelapor, db)
    _simpan_lampiran_pd(laporan, req.lampiran, db)

    _tulis_pdf_laporan(laporan, surat, db)
    db.commit()
    return {"message": "Laporan disusun. Silakan tanda tangani; peserta lain akan diminta menandatangani juga."}


@app.put("/api/laporan-pd/{laporan_id}")
def edit_laporan_pd(laporan_id: int, req: LaporanPDEdit, db: Session = Depends(get_db)):
    laporan = db.query(models.LaporanPD).filter(models.LaporanPD.id == laporan_id).first()
    if not laporan:
        raise HTTPException(404, "Laporan tidak ditemukan")
    if laporan.status != "draft":
        raise HTTPException(400, "Laporan sudah final dan terkunci")
    peserta_saya = db.query(models.LaporanPDPeserta).filter(
        models.LaporanPDPeserta.laporan_id == laporan.id,
        models.LaporanPDPeserta.nip_pegawai == req.nip
    ).first()
    if not peserta_saya:
        raise HTTPException(403, "Hanya peserta laporan ini yang boleh mengubahnya")

    tempat = (req.tempat or "").strip()
    hasil  = (req.hasil_kegiatan or "").strip()
    if not tempat or not hasil:
        raise HTTPException(400, "Tempat dan hasil kegiatan wajib diisi")

    _keluarkan_peserta_pd(laporan, req.nip_dikeluarkan, req.nip, db)
    laporan.tempat = tempat
    laporan.hasil_kegiatan = hasil
    _simpan_lampiran_pd(laporan, req.lampiran, db)

    # Isi berubah: tanda tangan yang sudah masuk dianggap belum sah, semua menandatangani ulang
    for r in db.query(models.LaporanPDPeserta).filter(models.LaporanPDPeserta.laporan_id == laporan.id).all():
        r.sudah_ttd = False
        r.tanggal_ttd = None
    db.flush()

    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    _tulis_pdf_laporan(laporan, surat, db)
    db.commit()
    return {"message": "Laporan diperbarui. Semua peserta perlu menandatangani ulang."}

@app.post("/api/laporan-pd/{laporan_id}/ttd")
def ttd_laporan_pd(laporan_id: int, nip: str = Form(...), pin: str = Form(...),
                   db: Session = Depends(get_db)):
    laporan = db.query(models.LaporanPD).filter(models.LaporanPD.id == laporan_id).first()
    if not laporan:
        raise HTTPException(404, "Laporan tidak ditemukan")
    if laporan.status != "draft":
        raise HTTPException(400, "Laporan sudah final")

    r = db.query(models.LaporanPDPeserta).filter(
        models.LaporanPDPeserta.laporan_id == laporan.id,
        models.LaporanPDPeserta.nip_pegawai == nip
    ).first()
    if not r:
        raise HTTPException(403, "Anda bukan peserta pada laporan ini")
    if r.sudah_ttd:
        raise HTTPException(400, "Anda sudah menandatangani laporan ini")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai or not pegawai.tte_aktif or not pegawai.pin_tte:
        raise HTTPException(400, "TTE belum diregistrasi. Daftar di halaman Profil dulu.")
    if not verify_pin(pin, pegawai.pin_tte):
        raise HTTPException(401, "PIN salah!")

    sekarang = datetime.utcnow()
    r.sudah_ttd = True
    r.tanggal_ttd = sekarang
    db.flush()

    semua = db.query(models.LaporanPDPeserta).filter(models.LaporanPDPeserta.laporan_id == laporan.id).all()
    final = all(x.sudah_ttd for x in semua)
    if final:
        laporan.status = "final"
        laporan.tanggal_final = sekarang

    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    _tulis_pdf_laporan(laporan, surat, db)

    if final:
        # Laporan final = laporan tiap peserta terkirim; atasan masing-masing baru bisa menilai
        for x in semua:
            if x.penugasan_id and not db.query(models.LaporanTugas).filter(
                models.LaporanTugas.penugasan_id == x.penugasan_id
            ).first():
                db.add(models.LaporanTugas(
                    penugasan_id  = x.penugasan_id,
                    isi_laporan   = laporan.hasil_kegiatan,
                    file_bukti    = laporan.file_pdf,
                    tanggal_lapor = x.tanggal_ttd,   # keterlambatan dihitung dari TTE masing-masing
                ))
    db.commit()

    jml = sum(1 for x in semua if x.sudah_ttd)
    return {
        "message"    : ("Semua peserta sudah menandatangani. Laporan final dan diteruskan ke atasan untuk dinilai."
                        if final else f"TTE berhasil ({jml} dari {len(semua)} peserta). Menunggu peserta lain."),
        "status"     : "final" if final else "berlanjut",
        "jml_ttd"    : jml,
        "jml_peserta": len(semua),
    }


@app.get("/verifikasi-laporan", response_class=HTMLResponse)
def verifikasi_laporan(kode: str = None, sig: str = None, db: Session = Depends(get_db)):
    """Halaman publik verifikasi laporan perjalanan dinas (dibuka lewat QR)."""
    def halaman(ok: bool, isi: str):
        warna = "#27ae60" if ok else "#e74c3c"
        return HTMLResponse(f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Verifikasi Laporan</title>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f4f7f6;margin:0;padding:24px;}}
.kotak{{max-width:560px;margin:auto;background:white;border-radius:12px;padding:24px;
box-shadow:0 4px 12px rgba(0,0,0,.08);border-top:6px solid {warna};}}
h2{{margin-top:0;color:{warna};}} .baris{{margin:6px 0;font-size:.92rem;color:#2c3e50;}}
.baris b{{color:#7f8c8d;font-weight:600;}}</style></head>
<body><div class="kotak">{isi}</div></body></html>""")

    if kode and sig and cek_signature(kode, sig) and kode.startswith("LRP-"):
        try:
            rid = int(kode[4:])
        except ValueError:
            return halaman(False, "<h2>❌ Tidak valid</h2><p>Kode tidak dikenali.</p>")
        lap = db.query(models.LaporanRapat).filter(models.LaporanRapat.id == rid).first()
        surat_r = db.query(models.Surat).filter(models.Surat.id == lap.surat_id).first() if lap else None
        if not lap or not surat_r:
            return halaman(False, "<h2>❌ Tidak ditemukan</h2><p>Notulensi tidak ada dalam sistem.</p>")
        info_r = _info_rapat(surat_r)
        pimpinan_r = _pimpinan_rapat(surat_r, db)
        er = lambda t: _esc(str(t or ""))
        final_r = lap.status == "final"
        isi_r = (f"<h2>{'✅ Notulensi Sah' if final_r else '⏳ Notulensi Belum Final'}</h2>"
                 f"<div class='baris'><b>Jenis:</b> Notulen &amp; Daftar Hadir Rapat</div>"
                 f"<div class='baris'><b>Agenda:</b> {er(info_r['agenda'])}</div>"
                 f"<div class='baris'><b>Hari/Tanggal:</b> {er(info_r['hari_tanggal'])}</div>"
                 f"<div class='baris'><b>Tempat:</b> {er(info_r['tempat'])}</div>"
                 f"<div class='baris'><b>Pimpinan Rapat:</b> {er(pimpinan_r['nama'])}</div>"
                 + (f"<div class='baris'><b>Ditandatangani:</b> {er(waktu_ttd_str(lap.tanggal_final))} WIB</div>"
                    if final_r and lap.tanggal_final else ""))
        return halaman(final_r, isi_r)

    if not kode or not sig or not cek_signature(kode, sig) or not kode.startswith("LPD-"):
        return halaman(False, "<h2>❌ Tidak valid</h2><p>Kode verifikasi tidak valid atau telah dimanipulasi.</p>")
    try:
        lid = int(kode[4:])
    except ValueError:
        return halaman(False, "<h2>❌ Tidak valid</h2><p>Kode tidak dikenali.</p>")

    laporan = db.query(models.LaporanPD).filter(models.LaporanPD.id == lid).first()
    if not laporan:
        return halaman(False, "<h2>❌ Tidak ditemukan</h2><p>Laporan tidak ada dalam sistem.</p>")
    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    peserta = daftar_peserta_pd(surat, laporan, db)
    data = data_laporan_pd(surat, peserta)
    e = lambda t: _esc(str(t or ""))

    final = laporan.status == "final"
    daftar = "".join(
        f"<div class='baris'>{'✅' if p['sudah_ttd'] else '⏳'} {e(p['nama'])}"
        f"{' — TTE ' + e(waktu_ttd_str(p['tanggal_ttd'])) + ' WIB' if p['sudah_ttd'] else ' — belum menandatangani'}</div>"
        for p in peserta)
    judul = "✅ Laporan Sah" if final else "⏳ Laporan Belum Final"
    isi = (f"<h2>{judul}</h2>"
           f"<div class='baris'><b>Jenis:</b> Laporan Perjalanan Dinas</div>"
           f"<div class='baris'><b>Kegiatan:</b> {e(data['kegiatan'])}</div>"
           f"<div class='baris'><b>Surat Tugas:</b> Nomor {e(data['nomor_surat'])} tanggal {e(data['tanggal_surat'])}</div>"
           f"<div class='baris'><b>Tempat:</b> {e(laporan.tempat)}</div>"
           f"<div class='baris'><b>Hari/Tanggal:</b> {e(data['hari_tanggal'])}</div>"
           f"<h4 style='margin-bottom:6px;'>Peserta yang menandatangani</h4>{daftar}")
    return halaman(final, isi)



# ==========================================================================
# 20. RAPAT: ABSENSI & PERWAKILAN
# ==========================================================================
# True = abaikan jadwal rapat saat absen (HANYA untuk uji coba, kembalikan ke False)
ABSEN_RAPAT_BEBAS_WAKTU = False

LABEL_HADIR = {
    "belum"      : "Belum absen",
    "hadir"      : "✅ Hadir",
    "izin"       : "📝 Izin",
    "tidak_hadir": "❌ Tidak hadir",
    "diwakilkan" : "👥 Diwakilkan",
}


def _sekarang_wib() -> datetime:
    return datetime.utcnow() + timedelta(hours=7)


def _laporan_rapat_surat(surat_id: int, db: Session):
    return db.query(models.LaporanRapat).filter(models.LaporanRapat.surat_id == surat_id).first()


def _surat_rapat(penugasan, db: Session):
    surat = db.query(models.Surat).filter(models.Surat.id == penugasan.surat_id).first()
    if not surat or surat.jenis_tugas != "rapat":
        raise HTTPException(400, "Penugasan ini bukan undangan rapat")
    return surat


def _baris_peserta_rapat(laporan_id: int, penugasan_id: int, db: Session):
    return db.query(models.LaporanRapatPeserta).filter(
        models.LaporanRapatPeserta.laporan_id == laporan_id,
        models.LaporanRapatPeserta.penugasan_id == penugasan_id
    ).first()


def _nama_pegawai(nip, db: Session) -> str:
    p = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    return p.nama if p else str(nip)


def kode_unit_turunan(kode_unit: str, db: Session) -> set:
    """Unit ini + seluruh unit di bawahnya (termasuk jalur induk tambahan)."""
    hasil = {kode_unit}
    tambahan = db.query(models.UnitIndukTambahan).all()
    antrian = [kode_unit]
    while antrian:
        k = antrian.pop(0)
        anak = [u.kode_unit for u in db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_induk == k).all()]
        anak += [t.kode_unit_anak for t in tambahan if t.kode_induk_tambahan == k]
        for a in anak:
            if a not in hasil:
                hasil.add(a)
                antrian.append(a)
    return hasil


def pastikan_laporan_rapat(surat, db: Session):
    """Buat laporan rapat + baris peserta bila belum ada (aman dipanggil berulang)."""
    laporan = _laporan_rapat_surat(surat.id, db)
    if laporan:
        return laporan
    laporan = models.LaporanRapat(surat_id=surat.id, status="draft")
    db.add(laporan)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _laporan_rapat_surat(surat.id, db)

    nip_notulen = {r.nip_pegawai for r in db.query(models.PenerimaSuratTugas).filter(
        models.PenerimaSuratTugas.surat_id == surat.id,
        models.PenerimaSuratTugas.is_notulen == True
    ).all()}
    for t in db.query(models.Penugasan).filter(models.Penugasan.surat_id == surat.id).all():
        db.add(models.LaporanRapatPeserta(
            laporan_id=laporan.id, nip_pegawai=t.nip_penerima, penugasan_id=t.id,
            is_notulen=t.nip_penerima in nip_notulen
        ))
    db.flush()
    return laporan


def _waktu_rapat(surat):
    """(mulai, selesai) dalam WIB. Jam selesai kosong = sampai 23:59 hari itu."""
    try:
        tgl = datetime.strptime((surat.tanggal_tugas_mulai or "")[:10], "%Y-%m-%d")
        h, m = map(int, (surat.jam_mulai or "").split(":"))
        mulai = tgl.replace(hour=h, minute=m)
        if surat.jam_selesai:
            h2, m2 = map(int, surat.jam_selesai.split(":"))
            selesai = tgl.replace(hour=h2, minute=m2)
        else:
            selesai = tgl.replace(hour=23, minute=59)
        return mulai, selesai
    except (ValueError, TypeError):
        return None, None


def jendela_absen_rapat(surat, laporan):
    """Return (boleh_absen, keterangan)."""
    if laporan and laporan.status != "draft":
        return False, "Notulensi sudah dikirim, absensi ditutup"
    if ABSEN_RAPAT_BEBAS_WAKTU:
        return True, "Mode uji: jadwal diabaikan"
    mulai, selesai = _waktu_rapat(surat)
    if not mulai:
        return False, "Jadwal rapat tidak valid"
    sekarang = _sekarang_wib()
    if sekarang < mulai:
        return False, f"Absensi dibuka {tanggal_indo(surat.tanggal_tugas_mulai, True)} pukul {surat.jam_mulai} WIB"
    if sekarang > selesai:
        return False, "Waktu rapat sudah berakhir, absensi ditutup"
    return True, "Absensi dibuka"


def info_rapat_tugas(p, db: Session) -> dict:
    """Status rapat untuk sebuah penugasan (kosong jika bukan undangan rapat)."""
    surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
    if not surat or surat.jenis_tugas != "rapat":
        return {}
    laporan = _laporan_rapat_surat(surat.id, db)
    baris = _baris_peserta_rapat(laporan.id, p.id, db) if laporan else None

    if baris:
        status  = baris.status_hadir or "belum"
        notulen = bool(baris.is_notulen)
    else:
        status  = "belum"
        notulen = db.query(models.PenerimaSuratTugas).filter(
            models.PenerimaSuratTugas.surat_id == surat.id,
            models.PenerimaSuratTugas.nip_pegawai == p.nip_penerima,
            models.PenerimaSuratTugas.is_notulen == True
        ).first() is not None

    label = LABEL_HADIR.get(status, status)
    if baris and status == "hadir" and baris.nip_mewakili:
        label += f" (mewakili {_nama_pegawai(baris.nip_mewakili, db)})"
    if baris and status == "diwakilkan":
        wakil = db.query(models.LaporanRapatPeserta).filter(
            models.LaporanRapatPeserta.laporan_id == laporan.id,
            models.LaporanRapatPeserta.nip_mewakili == p.nip_penerima
        ).first()
        if wakil:
            label += f" ke {_nama_pegawai(wakil.nip_pegawai, db)}"

    buka, ket = jendela_absen_rapat(surat, laporan)
    ket_waktu = (f"📅 {tanggal_indo(surat.tanggal_tugas_mulai, True)}, {surat.jam_mulai or '-'} s.d. "
                 f"{surat.jam_selesai or 'selesai'} WIB • {surat.tempat_tugas or '-'}")
    return {
        "rapat_notulen"     : notulen,
        "rapat_laporan_status": laporan.status if laporan else None,
        "rapat_status_saya" : status,
        "rapat_label_status": label,
        "rapat_absen_buka"  : bool(buka and status == "belum"),
        "rapat_ket_absen"   : ket,
        "rapat_ket_waktu"   : ket_waktu,
        "rapat_catatan_jadwal": surat.catatan_jadwal,
    }


@app.post("/api/rapat/absen")
def absen_rapat(
    penugasan_id: int = Form(...),
    nip         : str = Form(...),
    pin         : str = Form(...),
    db          : Session = Depends(get_db)
):
    p = _penugasan_milik(penugasan_id, nip, db)
    surat = _surat_rapat(p, db)
    if p.status != "proses":
        raise HTTPException(400, "Penugasan ini sudah tidak aktif")

    laporan = pastikan_laporan_rapat(surat, db)
    baris = _baris_peserta_rapat(laporan.id, p.id, db)
    if not baris:
        raise HTTPException(400, "Data peserta rapat tidak ditemukan")
    if baris.status_hadir != "belum":
        raise HTTPException(400, f"Anda sudah tercatat: {LABEL_HADIR.get(baris.status_hadir, baris.status_hadir)}")

    buka, ket = jendela_absen_rapat(surat, laporan)
    if not buka:
        raise HTTPException(400, ket)

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai or not pegawai.tte_aktif or not pegawai.pin_tte:
        raise HTTPException(400, "TTE belum diregistrasi. Daftar di halaman Profil dulu.")
    if not verify_pin(pin, pegawai.pin_tte):
        raise HTTPException(401, "PIN salah!")

    baris.status_hadir  = "hadir"
    baris.tanggal_hadir = datetime.utcnow()
    if not baris.is_notulen:
        p.status = "selesai"      # notulen baru selesai setelah notulensi di-TTE pimpinan
    db.commit()
    return {"message": "Kehadiran Anda tercatat.", "notulen": bool(baris.is_notulen)}


@app.get("/api/rapat/kandidat-perwakilan")
def kandidat_perwakilan_rapat(penugasan_id: int, nip: str, db: Session = Depends(get_db)):
    """Pegawai di unit yang sama atau bawahan, yang belum menjadi peserta rapat ini."""
    p = _penugasan_milik(penugasan_id, nip, db)
    surat = _surat_rapat(p, db)
    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai or not pegawai.kode_unit:
        return []

    kode_boleh = list(kode_unit_turunan(pegawai.kode_unit, db))
    sudah_terlibat = {t.nip_penerima for t in db.query(models.Penugasan).filter(
        models.Penugasan.surat_id == surat.id).all()}
    unit_map = {u.kode_unit: u.nama_unit for u in db.query(models.UnitOrganisasi).all()}

    return [
        {"nip": x.nip, "nama": x.nama, "jabatan": x.jabatan,
         "nama_unit": unit_map.get(x.kode_unit, "—")}
        for x in db.query(models.Pegawai).filter(
            models.Pegawai.kode_unit.in_(kode_boleh)
        ).order_by(models.Pegawai.nama).all()
        if x.nip not in sudah_terlibat and x.nip != nip
    ]


class PerwakilanRapatRequest(BaseModel):
    penugasan_id: int
    nip_pengirim: str
    nip_perwakilan: str
    alasan: str


@app.post("/api/rapat/perwakilan")
def kirim_perwakilan_rapat(req: PerwakilanRapatRequest, db: Session = Depends(get_db)):
    alasan = (req.alasan or "").strip()
    if not alasan:
        raise HTTPException(400, "Alasan berhalangan hadir wajib diisi")

    p = _penugasan_milik(req.penugasan_id, req.nip_pengirim, db)
    surat = _surat_rapat(p, db)
    if p.status != "proses":
        raise HTTPException(400, "Penugasan ini sudah tidak aktif")

    laporan = pastikan_laporan_rapat(surat, db)
    if laporan.status != "draft":
        raise HTTPException(400, "Notulensi sudah dikirim, tidak bisa mengirim perwakilan lagi")
    baris = _baris_peserta_rapat(laporan.id, p.id, db)
    if not baris:
        raise HTTPException(400, "Data peserta rapat tidak ditemukan")
    if baris.status_hadir != "belum":
        raise HTTPException(400, "Anda sudah tercatat hadir/diwakilkan, tidak bisa mengirim perwakilan")

    pengirim = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_pengirim).first()
    wakil    = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_perwakilan).first()
    if not wakil:
        raise HTTPException(404, "Pegawai perwakilan tidak ditemukan")
    if wakil.nip == pengirim.nip:
        raise HTTPException(400, "Perwakilan tidak boleh diri sendiri")
    if wakil.kode_unit not in kode_unit_turunan(pengirim.kode_unit, db):
        raise HTTPException(400, "Perwakilan harus dari unit yang sama atau bawahan Anda")
    if db.query(models.Penugasan).filter(
        models.Penugasan.surat_id == surat.id,
        models.Penugasan.nip_penerima == wakil.nip
    ).first():
        raise HTTPException(400, f"{wakil.nama} sudah terlibat dalam rapat ini")

    peran_notulen = bool(baris.is_notulen)
    baris.status_hadir = "diwakilkan"
    baris.alasan       = alasan
    baris.is_notulen   = False        # peran notulen (jika ada) pindah ke perwakilan
    p.status           = "selesai"

    agenda = surat.agenda_rapat or surat.perihal
    deskripsi = (f"Menghadiri rapat mewakili {pengirim.nama}: {agenda}. "
                 f"Alasan {pengirim.nama} berhalangan hadir: {alasan}. "
                 + ("Anda juga bertugas menyusun notulensi. " if peran_notulen else "")
                 + "Konfirmasi kehadiran saat rapat berlangsung.")
    pen_baru = models.Penugasan(
        surat_id        = surat.id,
        disposisi_id    = None,
        nip_pemberi     = pengirim.nip,
        nip_penerima    = wakil.nip,
        deskripsi_tugas = deskripsi,
        deadline        = None,
        status          = "proses",
    )
    db.add(pen_baru)
    db.flush()
    db.add(models.LaporanRapatPeserta(
        laporan_id=laporan.id, nip_pegawai=wakil.nip, penugasan_id=pen_baru.id,
        nip_mewakili=pengirim.nip, is_notulen=peran_notulen
    ))
    db.commit()
    return {"message": f"Perwakilan dikirim ke {wakil.nama}. Beliau akan menerima tugas menghadiri rapat."}



# ==========================================================================
# 21. RAPAT: NOTULENSI, DAFTAR HADIR & PDF
# ==========================================================================
def _jam_rapat_teks(surat) -> str:
    sel = surat.jam_selesai.replace(":", ".") if surat.jam_selesai else "selesai"
    return f"{(surat.jam_mulai or '-').replace(':', '.')} s.d. {sel} WIB"


def _info_rapat(surat) -> dict:
    return {
        "agenda"      : surat.agenda_rapat or surat.perihal,
        "hari_tanggal": tanggal_indo(surat.tanggal_tugas_mulai, True),
        "waktu"       : _jam_rapat_teks(surat),
        "tempat"      : surat.tempat_tugas or "-",
    }


def _pimpinan_rapat(surat, db: Session) -> dict:
    pg = db.query(models.Pegawai).filter(models.Pegawai.nip == surat.nip_pimpinan_rapat).first()
    if not pg:
        return {"nama": "-", "nip": "-", "jabatan": "-", "kode_kop": "D001"}
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pg.kode_unit
    ).first()
    jabatan = unit.jabatan_surat if unit and unit.jabatan_surat else (pg.jabatan or "-")
    kode_kop = pg.kode_unit if unit and unit.punya_kop_sendiri else "D001"
    return {"nama": pg.nama, "nip": pg.nip, "jabatan": jabatan, "kode_kop": kode_kop}


def _daftar_peserta_rapat(laporan, db: Session) -> list:
    hasil = []
    for r in db.query(models.LaporanRapatPeserta).filter(
        models.LaporanRapatPeserta.laporan_id == laporan.id
    ).order_by(models.LaporanRapatPeserta.id).all():
        pg = db.query(models.Pegawai).filter(models.Pegawai.nip == r.nip_pegawai).first()
        hasil.append({
            "nip"       : r.nip_pegawai,
            "nama"      : pg.nama if pg else r.nip_pegawai,
            "jabatan"   : pg.jabatan if pg and pg.jabatan else "-",
            "status"    : r.status_hadir or "belum",
            "alasan"    : r.alasan,
            "tanggal"   : r.tanggal_hadir,
            "is_notulen": bool(r.is_notulen),
            "mewakili"  : _nama_pegawai(r.nip_mewakili, db) if r.nip_mewakili else None,
        })
    return hasil


def _daftar_tamu_rapat(laporan, db: Session) -> list:
    return db.query(models.LaporanRapatTamu).filter(
        models.LaporanRapatTamu.laporan_id == laporan.id
    ).order_by(models.LaporanRapatTamu.id).all()


def _qr_ke_arsip(arsip, data: str, nama: str, folder_sementara: list = None):
    """Daftarkan QR (bytes) langsung ke arsip Story, sama seperti laporan perjalanan dinas."""
    arsip.add(buat_qr_png(data), nama)


def _render_story_pdf(html: str, css: str, arsip, path_fisik: str):
    story  = pymupdf.Story(html=html, user_css=css, archive=arsip)
    writer = pymupdf.DocumentWriter(path_fisik)
    media  = pymupdf.paper_rect("a4")
    area   = media + (56, 18, -56, -50)
    lagi = 1
    while lagi:
        dev = writer.begin_page(media)
        lagi, _ = story.place(area)
        story.draw(dev)
        writer.end_page()
    writer.close()


_CSS_RAPAT = """
    body { font-family: serif; font-size: 11pt; }
    .tengah { text-align: center; }
    .kecil { font-size: 8pt; }
    .info td { vertical-align: top; padding: 2px; }
    .kop td { font-size: 9pt; }
    .besar { font-size: 14pt; }
    .isi { text-align: justify; line-height: 1.4; }
    .tbl { border: 1px solid black; border-collapse: collapse; width: 100%; }
    .tbl th, .tbl td { border: 1px solid black; padding: 6px; vertical-align: middle; }
    .c { text-align: center; }
"""

def _siapkan_kop(arsip, kode_kop: str = "D001") -> str:
    """Kop unit (gambar) bila ada; jika tidak, kop Direktur; jika tidak ada juga, kop teks."""
    path_kop = None
    for kode in (kode_kop, "D001"):
        nama_kop = MAPPING_KOP.get(kode)
        if nama_kop and os.path.exists(f"uploads/assets/{nama_kop}"):
            path_kop = f"uploads/assets/{nama_kop}"
            break
    if path_kop:
        with open(path_kop, "rb") as f:
            arsip.add(f.read(), "kop.png")
        return '<img src="kop.png" width="483"><hr>'

    logo = ""
    if os.path.exists(PATH_LOGO):
        with open(PATH_LOGO, "rb") as f:
            arsip.add(f.read(), "logo.png")
        logo = '<img src="logo.png" width="60">'
    return f"""
    <table class="kop"><tr>
      <td width="70">{logo}</td>
      <td class="tengah">KEMENTERIAN PENDIDIKAN TINGGI, SAINS, DAN TEKNOLOGI<br>
      <b class="besar">POLITEKNIK PERTANIAN NEGERI PAYAKUMBUH</b><br>
      Jalan Raya Negara KM.7 Tanjung Pati 26271 Kecamatan Harau<br>
      Kabupaten Limapuluh Kota - Sumatera Barat<br>
      Telepon. (0752) 77 54192 - Faksimili (0752) 7750220<br>
      Surel : sekretariat@politanipyk.ac.id Laman : http://politanipyk.ac.id</td>
    </tr></table><hr>"""


def _blok_pimpinan_html(pimpinan: dict, e, tte=None) -> str:
    if tte:
        ttd = (f'<img src="qr_pimpinan.png" width="60"><br>'
               f'<span class="kecil">SAH - TTE: {e(waktu_ttd_str(tte["tanggal"]))}</span><br>')
    else:
        ttd = "<br><br><br><br>"
    return (f"<table width='100%'><tr><td width='55%'>&nbsp;</td><td>"
            f"Pimpinan Rapat<br>{e(pimpinan['jabatan'])},<br>{ttd}"
            f"<b>{e(pimpinan['nama'])}</b><br>NIP. {e(pimpinan['nip'])}"
            f"</td></tr></table>")

def buat_pdf_notulen(info, notulen_nama, pimpinan, peserta_teks, hasil_rapat, path_fisik, tte=None):
    e = lambda t: _esc(str(t or "")).replace("\n", "<br>")
    arsip = pymupdf.Archive()
    folder_tmp = []
    try:
        kop = _siapkan_kop(arsip, pimpinan.get("kode_kop", "D001"))
        if tte:
            _qr_ke_arsip(arsip, tte["url"], "qr_pimpinan.png", folder_tmp)
        html = f"""
        {kop}
        <h3 class="tengah">NOTULEN RAPAT</h3>
        <table class="info" width="100%">
          <tr><td width="105">Agenda Rapat</td><td width="10">:</td><td>{e(info['agenda'])}</td></tr>
          <tr><td>Hari/Tanggal</td><td>:</td><td>{e(info['hari_tanggal'])}</td></tr>
          <tr><td>Waktu</td><td>:</td><td>{e(info['waktu'])}</td></tr>
          <tr><td>Tempat</td><td>:</td><td>{e(info['tempat'])}</td></tr>
          <tr><td>Pimpinan Rapat</td><td>:</td><td>{e(pimpinan['nama'])}</td></tr>
          <tr><td>Notulen</td><td>:</td><td>{e(notulen_nama)}</td></tr>
          <tr><td>Peserta Rapat</td><td>:</td><td>{peserta_teks}</td></tr>
        </table>
        <p><b>Hasil Rapat :</b></p>
        <p class="isi">{e(hasil_rapat)}</p>
        <br>
        {_blok_pimpinan_html(pimpinan, e, tte)}
        """
        _render_story_pdf(html, _CSS_RAPAT, arsip, path_fisik)
    finally:
        for f in folder_tmp:
            shutil.rmtree(f, ignore_errors=True)

def buat_pdf_daftar_hadir(info, rows, pimpinan, path_fisik, url_verifikasi, tte=None):
    e = lambda t: _esc(str(t or "")).replace("\n", "<br>")
    arsip = pymupdf.Archive()
    folder_tmp = []
    try:
        kop = _siapkan_kop(arsip, pimpinan.get("kode_kop", "D001"))
        if tte:
            _qr_ke_arsip(arsip, tte["url"], "qr_pimpinan.png", folder_tmp)
        if any(r["status"] == "hadir" and r.get("tanggal") for r in rows):
            _qr_ke_arsip(arsip, url_verifikasi, "qr.png", folder_tmp)

        baris = ""
        for i, r in enumerate(rows, 1):
            st = r["status"]
            alasan = f'<br><span class="kecil">{e(r["alasan"])}</span>' if r.get("alasan") else ""
            if st == "hadir" and r.get("tanggal"):
                sel = (f'<img src="qr.png" width="42"><br>'
                       f'<span class="kecil">SAH - TTE: {e(waktu_ttd_str(r["tanggal"]))}</span>')
            elif st == "hadir":
                sel = "Hadir"
            elif st == "izin":
                sel = "Izin" + alasan
            elif st == "belum":
                sel = '<span class="kecil">Belum absen</span>'
            else:
                sel = "Tidak hadir" + alasan
            baris += (f"<tr><td class='c'>{i}</td><td>{e(r['nama'])}</td><td>{e(r['nip'])}</td>"
                      f"<td>{e(r['jabatan'])}</td><td class='c'>{sel}</td></tr>")

        html = f"""
        {kop}
        <h3 class="tengah">DAFTAR HADIR RAPAT</h3>
        <table class="info" width="100%">
          <tr><td width="90">Hari/Tanggal</td><td width="10">:</td><td>{e(info['hari_tanggal'])}</td></tr>
          <tr><td>Jam</td><td>:</td><td>{e(info['waktu'])}</td></tr>
          <tr><td>Tempat</td><td>:</td><td>{e(info['tempat'])}</td></tr>
          <tr><td>Agenda</td><td>:</td><td>{e(info['agenda'])}</td></tr>
        </table>
        <br>
        <table class="tbl">
          <tr><th width="28">No</th><th>Nama</th><th width="95">NIP</th><th width="120">Jabatan</th><th width="100">Tandatangan</th></tr>
          {baris}
        </table>
        <br>
        {_blok_pimpinan_html(pimpinan, e, tte)}
        """
        _render_story_pdf(html, _CSS_RAPAT, arsip, path_fisik)
    finally:
        for f in folder_tmp:
            shutil.rmtree(f, ignore_errors=True)
    
def _tulis_pdf_rapat(laporan, surat, db: Session):
    """Susun ulang dua PDF (notulen & daftar hadir) dari data terbaru."""
    info     = _info_rapat(surat)
    pimpinan = _pimpinan_rapat(surat, db)
    peserta  = _daftar_peserta_rapat(laporan, db)
    tamu     = _daftar_tamu_rapat(laporan, db)

    # --- Daftar hadir: perwakilan tampil, yang diwakilkan tidak dicetak ---
    rows = []
    for p in peserta:
        if p["status"] == "diwakilkan":
            continue
        jab = p["jabatan"] + (f" (mewakili {p['mewakili']})" if p["mewakili"] else "")
        rows.append({"nama": p["nama"], "nip": p["nip"], "jabatan": jab, "status": p["status"],
                     "alasan": p["alasan"], "tanggal": p["tanggal"]})
    for t in tamu:
        rows.append({"nama": t.nama, "nip": "-", "jabatan": t.instansi or "-",
                     "status": "hadir" if t.hadir else "tidak_hadir",
                     "alasan": None, "tanggal": None})

    # --- Notulen: hanya yang hadir ---
    e = lambda x: _esc(str(x or ""))
    hadir = [p["nama"] + (f" (mewakili {p['mewakili']})" if p["mewakili"] else "")
             for p in peserta if p["status"] == "hadir"]
    hadir += [t.nama + (f" ({t.instansi})" if t.instansi else "") for t in tamu if t.hadir]
    peserta_teks = "<br>".join(f"{i}. {e(n)}" for i, n in enumerate(hadir, 1)) or "-"
    notulen_nama = next((p["nama"] for p in peserta if p["is_notulen"]), "-")

    kode = f"LRP-{laporan.id}"
    url  = f"{BASE_URL}/verifikasi-laporan?kode={kode}&sig={buat_signature(kode)}"
    sekarang = datetime.utcnow()
    folder = f"bukti_tugas/rapat/{sekarang.strftime('%Y')}/{sekarang.strftime('%m')}"
    os.makedirs(f"uploads/{folder}", exist_ok=True)       # daftar hadir (publik)
    os.makedirs(f"{DIR_PRIVATE}/{folder}", exist_ok=True) # notulen (privat)
    stamp = int(time.time())
    rel_n = f"{folder}/notulen_{laporan.id}_{stamp}.pdf"
    rel_h = f"{folder}/daftar_hadir_{laporan.id}_{stamp}.pdf"

    tte = ({"tanggal": laporan.tanggal_final, "url": url}
           if laporan.status == "final" and laporan.tanggal_final else None)
    buat_pdf_notulen(info, notulen_nama, pimpinan, peserta_teks,
                     laporan.hasil_rapat or "", f"{DIR_PRIVATE}/{rel_n}", tte=tte)
    buat_pdf_daftar_hadir(info, rows, pimpinan, f"uploads/{rel_h}", url, tte=tte)

    hapus_file_notulen(laporan.file_notulen)
    hapus_file_upload(laporan.file_daftar_hadir)
    laporan.file_notulen      = rel_n
    laporan.file_daftar_hadir = rel_h


def _penugasan_notulen(penugasan_id: int, nip: str, db: Session):
    p = _penugasan_milik(penugasan_id, nip, db)
    surat = _surat_rapat(p, db)
    laporan = pastikan_laporan_rapat(surat, db)
    baris = _baris_peserta_rapat(laporan.id, p.id, db)
    if not baris or not baris.is_notulen:
        raise HTTPException(403, "Hanya notulen yang boleh menyusun notulensi")
    return p, surat, laporan, baris


class TamuRapatItem(BaseModel):
    nama: str
    instansi: Optional[str] = None
    hadir: bool = True


class StatusPesertaItem(BaseModel):
    nip: str
    status_hadir: str            # belum | izin | tidak_hadir
    alasan: Optional[str] = None


class NotulensiRapatRequest(BaseModel):
    penugasan_id: int
    nip: str
    hasil_rapat: Optional[str] = ""
    tamu: List[TamuRapatItem] = []
    status_peserta: List[StatusPesertaItem] = []


def _terapkan_notulensi(req: NotulensiRapatRequest, db: Session):
    """Simpan isian notulen ke database (tanpa membuat PDF & tanpa commit)."""
    p, surat, laporan, baris = _penugasan_notulen(req.penugasan_id, req.nip, db)
    if laporan.status not in ("draft", "revisi"):
        raise HTTPException(400, "Notulensi sudah dikirim dan menunggu TTE pimpinan, tidak bisa diubah")

    laporan.hasil_rapat = (req.hasil_rapat or "").strip()

    for it in req.status_peserta:
        if it.status_hadir not in ("belum", "izin", "tidak_hadir"):
            raise HTTPException(400, "Status kehadiran tidak valid")
        r = db.query(models.LaporanRapatPeserta).filter(
            models.LaporanRapatPeserta.laporan_id == laporan.id,
            models.LaporanRapatPeserta.nip_pegawai == it.nip
        ).first()
        if not r or r.is_notulen:
            continue
        if r.status_hadir not in ("belum", "izin", "tidak_hadir"):
            continue                       # hadir / diwakilkan tidak boleh diubah notulen
        r.status_hadir = it.status_hadir
        r.alasan = (it.alasan or "").strip() or None

    db.query(models.LaporanRapatTamu).filter(
        models.LaporanRapatTamu.laporan_id == laporan.id
    ).delete()
    for t in req.tamu[:100]:
        nama = (t.nama or "").strip()
        if not nama:
            continue
        db.add(models.LaporanRapatTamu(
            laporan_id=laporan.id, nama=nama[:255],
            instansi=((t.instansi or "").strip()[:255] or None), hadir=bool(t.hadir)))
    db.flush()
    return p, surat, laporan, baris

def _url_notulen_untuk_notulen(laporan, nip: str) -> Optional[str]:
    """Notulen penyusun hanya dapat pratinjau saat draft/revisi (aturan A)."""
    if laporan.file_notulen and laporan.status in ("draft", "revisi"):
        return f"/api/rapat/notulen-file/{laporan.id}?nip={nip}"
    return None


@app.get("/api/rapat/notulen-file/{laporan_id}")
def unduh_notulen_rapat(laporan_id: int, nip: str, db: Session = Depends(get_db)):
    """PDF notulen: pimpinan rapat & Direktur kapan saja; notulen penyusun hanya saat draft/revisi."""
    laporan = db.query(models.LaporanRapat).filter(models.LaporanRapat.id == laporan_id).first()
    if not laporan or not laporan.file_notulen:
        raise HTTPException(404, "Notulen belum tersedia")
    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()

    boleh = False
    if surat and surat.nip_pimpinan_rapat == nip:
        boleh = True
    elif _is_direktur(nip, db):
        boleh = True
    elif laporan.status in ("draft", "revisi"):
        boleh = db.query(models.LaporanRapatPeserta).filter(
            models.LaporanRapatPeserta.laporan_id == laporan.id,
            models.LaporanRapatPeserta.nip_pegawai == nip,
            models.LaporanRapatPeserta.is_notulen == True
        ).first() is not None
    if not boleh:
        raise HTTPException(403, "Anda tidak berhak melihat notulen ini")

    path = os.path.join(DIR_PRIVATE, laporan.file_notulen)
    if not os.path.exists(path):
        path_lama = os.path.join("uploads", laporan.file_notulen)   # file sebelum dipindah ke folder privat
        if os.path.exists(path_lama):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(path_lama, path)                            # pindahkan otomatis saat pertama dibuka
        else:
            raise HTTPException(404, "File notulen tidak ditemukan di server")
    return FileResponse(path, media_type="application/pdf")

@app.get("/api/rapat/notulensi")
def get_notulensi_rapat(penugasan_id: int, nip: str, db: Session = Depends(get_db)):
    p, surat, laporan, baris = _penugasan_notulen(penugasan_id, nip, db)
    db.commit()    # laporan mungkin baru dibuat
    peserta = [{
        "nip": x["nip"], "nama": x["nama"], "jabatan": x["jabatan"],
        "status": x["status"], "label": LABEL_HADIR.get(x["status"], x["status"]),
        "alasan": x["alasan"], "mewakili": x["mewakili"], "is_notulen": x["is_notulen"],
        "editable": (not x["is_notulen"]) and x["status"] in ("belum", "izin", "tidak_hadir"),
    } for x in _daftar_peserta_rapat(laporan, db)]
    tamu = [{"nama": t.nama, "instansi": t.instansi or "", "hadir": bool(t.hadir)}
            for t in _daftar_tamu_rapat(laporan, db)]
    info = _info_rapat(surat)
    info["pimpinan"] = _pimpinan_rapat(surat, db)["nama"]
    return {
        "info": info, "peserta": peserta, "tamu": tamu,
        "hasil_rapat": laporan.hasil_rapat or "",
        "status": laporan.status, "catatan_revisi": laporan.catatan_revisi,
        "url_notulen": _url_notulen_untuk_notulen(laporan, nip),
        "file_daftar_hadir": laporan.file_daftar_hadir,
        "boleh_edit": laporan.status in ("draft", "revisi"),
    }


@app.post("/api/rapat/notulensi")
def simpan_notulensi_rapat(req: NotulensiRapatRequest, db: Session = Depends(get_db)):
    p, surat, laporan, baris = _terapkan_notulensi(req, db)
    _tulis_pdf_rapat(laporan, surat, db)
    db.commit()
    return {"message": "Draft notulensi tersimpan. PDF pratinjau sudah diperbarui.",
            "url_notulen": _url_notulen_untuk_notulen(laporan, req.nip),
            "file_daftar_hadir": laporan.file_daftar_hadir}


@app.post("/api/rapat/notulensi/kirim")
def kirim_notulensi_rapat(req: NotulensiRapatRequest, db: Session = Depends(get_db)):
    p, surat, laporan, baris = _terapkan_notulensi(req, db)

    if not (laporan.hasil_rapat or "").strip():
        raise HTTPException(400, "Hasil rapat wajib diisi sebelum dikirim")
    if baris.status_hadir != "hadir":
        raise HTTPException(400, "Anda sebagai notulen harus absen (hadir) terlebih dahulu")
    if not ABSEN_RAPAT_BEBAS_WAKTU:
        mulai, _ = _waktu_rapat(surat)
        if mulai and _sekarang_wib() < mulai:
            raise HTTPException(400, "Rapat belum dimulai, notulensi belum bisa dikirim")

    for r in db.query(models.LaporanRapatPeserta).filter(
        models.LaporanRapatPeserta.laporan_id == laporan.id
    ).all():
        if r.is_notulen:
            continue
        if r.status_hadir == "belum":
            r.status_hadir = "tidak_hadir"
            if not r.alasan:
                r.alasan = "Tidak melakukan absensi"
        if r.penugasan_id:
            pen = db.query(models.Penugasan).filter(models.Penugasan.id == r.penugasan_id).first()
            if pen and pen.status == "proses":
                pen.status = "selesai"

    laporan.status         = "menunggu_ttd"
    laporan.tanggal_kirim  = datetime.utcnow()
    laporan.catatan_revisi = None
    db.flush()
    _tulis_pdf_rapat(laporan, surat, db)
    db.commit()
    return {"message": "Notulensi dikirim ke pimpinan rapat untuk ditandatangani."}

@app.post("/api/rapat/notulensi/{laporan_id}/ttd")
def ttd_notulensi_rapat(laporan_id: int, nip: str = Form(...), pin: str = Form(...),
                        db: Session = Depends(get_db)):
    laporan = db.query(models.LaporanRapat).filter(models.LaporanRapat.id == laporan_id).first()
    if not laporan:
        raise HTTPException(404, "Notulensi tidak ditemukan")
    if laporan.status != "menunggu_ttd":
        raise HTTPException(400, "Notulensi ini tidak sedang menunggu TTE")
    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    if not surat or surat.nip_pimpinan_rapat != nip:
        raise HTTPException(403, "Hanya pimpinan rapat yang boleh menandatangani notulensi ini")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    if not pegawai or not pegawai.tte_aktif or not pegawai.pin_tte:
        raise HTTPException(400, "TTE belum diregistrasi. Daftar di halaman Profil dulu.")
    if not verify_pin(pin, pegawai.pin_tte):
        raise HTTPException(401, "PIN salah!")

    laporan.status = "final"
    laporan.tanggal_final = datetime.utcnow()
    laporan.catatan_revisi = None

    # tugas notulen baru selesai setelah notulensi di-TTE pimpinan
    for r in db.query(models.LaporanRapatPeserta).filter(
        models.LaporanRapatPeserta.laporan_id == laporan.id,
        models.LaporanRapatPeserta.is_notulen == True
    ).all():
        if r.penugasan_id:
            pen = db.query(models.Penugasan).filter(models.Penugasan.id == r.penugasan_id).first()
            if pen and pen.status == "proses":
                pen.status = "selesai"

    db.flush()
    _tulis_pdf_rapat(laporan, surat, db)
    db.commit()
    return {"message": "Notulensi berhasil ditandatangani dan final."}


class RevisiNotulensiRequest(BaseModel):
    nip: str
    catatan: str


@app.post("/api/rapat/notulensi/{laporan_id}/revisi")
def revisi_notulensi_rapat(laporan_id: int, req: RevisiNotulensiRequest,
                           db: Session = Depends(get_db)):
    laporan = db.query(models.LaporanRapat).filter(models.LaporanRapat.id == laporan_id).first()
    if not laporan:
        raise HTTPException(404, "Notulensi tidak ditemukan")
    if laporan.status != "menunggu_ttd":
        raise HTTPException(400, "Notulensi ini tidak sedang menunggu TTE")
    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    if not surat or surat.nip_pimpinan_rapat != req.nip:
        raise HTTPException(403, "Hanya pimpinan rapat yang boleh meminta revisi")
    if not (req.catatan or "").strip():
        raise HTTPException(400, "Catatan revisi wajib diisi")

    laporan.status = "revisi"
    laporan.catatan_revisi = req.catatan.strip()
    db.commit()
    return {"message": "Notulensi dikembalikan ke notulen untuk direvisi."}

@app.get("/api/laporan-pd/{laporan_id}/pdf")
def unduh_laporan_pd(laporan_id: int, db: Session = Depends(get_db)):
    laporan = db.query(models.LaporanPD).filter(models.LaporanPD.id == laporan_id).first()
    if not laporan or not laporan.file_pdf:
        raise HTTPException(404, "Laporan belum memiliki PDF")
    path = f"uploads/{laporan.file_pdf}"
    if not os.path.exists(path):
        raise HTTPException(404, "File PDF tidak ditemukan di server")
    return FileResponse(path, media_type="application/pdf")

@app.post("/api/rapat/regenerasi-pdf/{laporan_id}")
def regenerasi_pdf_rapat(laporan_id: int, db: Session = Depends(get_db)):
    """SEMENTARA: buat ulang PDF notulen & daftar hadir. Hapus setelah dipakai."""
    laporan = db.query(models.LaporanRapat).filter(models.LaporanRapat.id == laporan_id).first()
    if not laporan:
        raise HTTPException(404, "Laporan tidak ditemukan")
    surat = db.query(models.Surat).filter(models.Surat.id == laporan.surat_id).first()
    _tulis_pdf_rapat(laporan, surat, db)
    db.commit()

class KembalikanSuratRequest(BaseModel):
    nip: str
    catatan: str


@app.post("/api/surat/{surat_id}/kembalikan")
def kembalikan_surat(surat_id: int, req: KembalikanSuratRequest, db: Session = Depends(get_db)):
    """Direktur mengembalikan surat eksternal ke pembuat (menjadi draft) sebelum didisposisikan."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip).first()
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first() if pegawai else None
    if not unit or unit.level != 1:
        raise HTTPException(403, "Hanya Direktur yang boleh mengembalikan surat ini")

    if surat.jenis_surat != "eksternal" or surat.status != "masuk":
        raise HTTPException(400, "Surat ini tidak bisa dikembalikan")
    if db.query(models.Disposisi).filter(models.Disposisi.surat_id == surat.id).first():
        raise HTTPException(400, "Surat sudah didisposisikan, tidak bisa dikembalikan")

    catatan = (req.catatan or "").strip()
    if not catatan:
        raise HTTPException(400, "Alasan pengembalian wajib diisi")

    surat.status = "draft"
    surat.catatan_kembali = catatan
    db.commit()
    return {"message": "Surat dikembalikan ke pembuat untuk diperbaiki."}

class UbahJadwalRapatRequest(BaseModel):
    penugasan_id: int
    nip: str
    tanggal: str                       # YYYY-MM-DD
    jam_mulai: str                     # HH:MM
    jam_selesai: Optional[str] = None  # kosong = s.d. selesai
    alasan: str


@app.post("/api/rapat/ubah-jadwal")
def ubah_jadwal_rapat(req: UbahJadwalRapatRequest, db: Session = Depends(get_db)):
    """Notulen memajukan/memundurkan jadwal rapat, selama notulensi belum dikirim."""
    p, surat, laporan, baris = _penugasan_notulen(req.penugasan_id, req.nip, db)
    if p.status != "proses":
        raise HTTPException(400, "Penugasan ini sudah tidak aktif")
    if laporan.status != "draft":
        raise HTTPException(400, "Notulensi sudah dikirim, jadwal tidak bisa diubah lagi")

    alasan = (req.alasan or "").strip()
    if not alasan:
        raise HTTPException(400, "Alasan perubahan jadwal wajib diisi")
    try:
        datetime.strptime(req.tanggal, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    pola_jam = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    jam_mulai   = (req.jam_mulai or "").strip()
    jam_selesai = (req.jam_selesai or "").strip()
    if not pola_jam.match(jam_mulai):
        raise HTTPException(400, "Jam mulai wajib diisi (format HH:MM)")
    if jam_selesai:
        if not pola_jam.match(jam_selesai):
            raise HTTPException(400, "Format jam selesai harus HH:MM")
        if jam_selesai <= jam_mulai:
            raise HTTPException(400, "Jam selesai harus setelah jam mulai")

    lama = f"{tanggal_indo(surat.tanggal_tugas_mulai, True)}, {_jam_rapat_teks(surat)}"

    surat.tanggal_tugas_mulai   = req.tanggal
    surat.tanggal_tugas_selesai = req.tanggal      # rapat satu hari
    surat.jam_mulai             = jam_mulai
    surat.jam_selesai           = jam_selesai or None

    baru = f"{tanggal_indo(req.tanggal, True)}, {_jam_rapat_teks(surat)}"
    waktu = _sekarang_wib().strftime("%d-%m-%Y %H:%M")
    catatan = (f"Jadwal diubah dari {lama} menjadi {baru}. Alasan: {alasan} "
               f"(oleh {_nama_pegawai(req.nip, db)}, {waktu} WIB)")
    surat.catatan_jadwal = ((surat.catatan_jadwal + "\n") if surat.catatan_jadwal else "") + catatan

    db.commit()
    return {"message": "Jadwal rapat diperbarui. Tombol absensi peserta mengikuti jadwal baru."}

# ==========================================================================
# 22. KASUS SURAT: AKAR KASUS & RIWAYAT BERKELOMPOK
# ==========================================================================
def cari_akar_kasus(surat_id: int, db: Session) -> Optional[models.Surat]:
    """
    Telusuri ke belakang sampai surat paling awal sebuah kasus, lewat 3 jenis tautan:
      - surat_asal_id      : surat lanjutan / balasan
      - disposisi_asal_id  : surat tugas yang lahir dari disposisi
      - penugasan_asal_id  : surat tugas yang lahir dari penugasan
    """
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    dikunjungi = set()
    while surat and surat.id not in dikunjungi:
        dikunjungi.add(surat.id)
        induk_id = None
        if surat.surat_asal_id:
            induk_id = surat.surat_asal_id
        elif surat.disposisi_asal_id:
            d = db.query(models.Disposisi).filter(models.Disposisi.id == surat.disposisi_asal_id).first()
            induk_id = d.surat_id if d else None
        elif surat.penugasan_asal_id:
            p = db.query(models.Penugasan).filter(models.Penugasan.id == surat.penugasan_asal_id).first()
            induk_id = p.surat_id if p else None
        if not induk_id:
            break
        induk = db.query(models.Surat).filter(models.Surat.id == induk_id).first()
        if not induk:
            break
        surat = induk
    return surat


def ambil_semua_id_kasus(root_id: int, db: Session) -> list:
    """Telusuri maju dari akar: kumpulkan id semua surat dalam satu kasus."""
    hasil, antrian, ada = [root_id], [root_id], {root_id}
    while antrian:
        cur = antrian.pop(0)
        id_disp = [d.id for d in db.query(models.Disposisi).filter(models.Disposisi.surat_id == cur).all()]
        id_pen  = [p.id for p in db.query(models.Penugasan).filter(models.Penugasan.surat_id == cur).all()]
        kondisi = [models.Surat.surat_asal_id == cur]
        if id_disp:
            kondisi.append(models.Surat.disposisi_asal_id.in_(id_disp))
        if id_pen:
            kondisi.append(models.Surat.penugasan_asal_id.in_(id_pen))
        for s in db.query(models.Surat).filter(or_(*kondisi)).all():
            if s.id not in ada:
                ada.add(s.id)
                hasil.append(s.id)
                antrian.append(s.id)
    return hasil


def _pecah_label(label: str):
    """'📅 Rapat — ✅ Hadir (mewakili X)' -> ('📅 Rapat', '✅ Hadir (mewakili X)')."""
    bagian = re.split(r"\s+—\s+|\s+\(", label or "", maxsplit=1)
    judul = bagian[0].strip()
    detail = (label or "")[len(bagian[0]):].lstrip(" —").strip()
    return judul, detail


def _tahap_kasus(root, ids: list, db: Session) -> list:
    """Stepper ringkas: hanya tahap yang memang ada di kasus ini. Status: selesai | berjalan | belum."""
    semua_surat = db.query(models.Surat).filter(models.Surat.id.in_(ids)).all()
    rapat_surat = [s for s in semua_surat if s.jenis_tugas == "rapat"]
    rapat_ids   = {s.id for s in rapat_surat}
    pd_surat    = [s for s in semua_surat if s.jenis_tugas == "perjalanan_dinas"]
    tugas_surat = [s for s in semua_surat if s.jenis_surat == "permohonan" and s.jenis_tugas != "rapat"]

    tahap = [{"kode": "surat", "label": "Surat",
              "status": "selesai" if root.status in ("masuk", "diproses", "selesai") else "berjalan"}]

    disp = db.query(models.Disposisi).filter(models.Disposisi.surat_id.in_(ids)).all()
    if disp:
        punya_anak = {d.disposisi_asal_id for d in disp if d.disposisi_asal_id}
        aktif = any(d.status == "diteruskan" and d.id not in punya_anak for d in disp)
        tahap.append({"kode": "disposisi", "label": "Disposisi", "status": "berjalan" if aktif else "selesai"})

    pen = db.query(models.Penugasan).filter(models.Penugasan.surat_id.in_(ids)).all()
    pen_biasa = [p for p in pen if p.surat_id not in rapat_ids]
    if pen_biasa or tugas_surat:
        aktif = any(p.status == "proses" for p in pen_biasa) or any(s.status != "selesai" for s in tugas_surat)
        tahap.append({"kode": "tugas", "label": "Penugasan", "status": "berjalan" if aktif else "selesai"})

    if pd_surat:
        lap_pd = db.query(models.LaporanPD).filter(
            models.LaporanPD.surat_id.in_([s.id for s in pd_surat])).all()
        selesai = bool(lap_pd) and all(l.status == "final" for l in lap_pd)
        tahap.append({"kode": "laporan_pd", "label": "Laporan Dinas",
                      "status": "selesai" if selesai else "berjalan"})

    if rapat_surat:
        lap = db.query(models.LaporanRapat).filter(models.LaporanRapat.surat_id.in_(list(rapat_ids))).all()
        undangan_terbit = all(s.status == "selesai" for s in rapat_surat)
        rapat_jalan = undangan_terbit and any(l.status != "draft" for l in lap)
        tahap.append({"kode": "rapat", "label": "Rapat", "status": "selesai" if rapat_jalan else "berjalan"})
        if lap and all(l.status == "final" for l in lap):
            st = "selesai"
        elif undangan_terbit:
            st = "berjalan"
        else:
            st = "belum"
        tahap.append({"kode": "notulensi", "label": "Notulensi", "status": st})

    return tahap

def _hari_sejak(dt) -> Optional[int]:
    try:
        return max(0, (datetime.utcnow() - dt).days) if dt else None
    except Exception:
        return None


def _hambatan_kasus(ids: list, db: Session) -> list:
    """Daftar 'apa yang sedang ditunggu' pada sebuah kasus: siapa, menunggu apa, sejak kapan."""
    out = []

    def tambah(tahap, nip, apa, sejak, surat_id):
        pg = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first() if nip else None
        out.append({"tahap": tahap, "nip": nip, "nama": pg.nama if pg else "—",
                    "jabatan": (pg.jabatan if pg else "") or "", "apa": apa,
                    "hari": _hari_sejak(sejak), "surat_id": surat_id})

    surat_list = db.query(models.Surat).filter(models.Surat.id.in_(ids)).all()
    rapat_ids = {s.id for s in surat_list if s.jenis_tugas == "rapat"}
    pd_ids    = {s.id for s in surat_list if s.jenis_tugas == "perjalanan_dinas"}
    direktur  = db.query(models.UnitOrganisasi).filter(models.UnitOrganisasi.level == 1).first()

    # 1. Surat menunggu paraf / TTE, atau surat eksternal menunggu disposisi Direktur
    for s in surat_list:
        if s.status == "menunggu":
            antre = db.query(models.PersetujuanSurat).filter(
                models.PersetujuanSurat.surat_id == s.id,
                models.PersetujuanSurat.status_persetujuan == "menunggu"
            ).order_by(models.PersetujuanSurat.urutan).all()
            if not antre:
                continue
            giliran = antre if antre[0].mode_paralel else antre[:1]
            sebelumnya = db.query(models.PersetujuanSurat).filter(
                models.PersetujuanSurat.surat_id == s.id,
                models.PersetujuanSurat.status_persetujuan == "disetujui"
            ).order_by(models.PersetujuanSurat.tanggal_proses.desc()).first()
            sejak = sebelumnya.tanggal_proses if sebelumnya else s.tanggal_dibuat
            tahap = "rapat" if s.id in rapat_ids else ("laporan_pd" if s.id in pd_ids else "tugas")
            if s.jenis_surat == "internal":
                tahap = "surat"
            for p in giliran:
                tambah(tahap, p.nip_penyetuju,
                       f"{p.jenis_ttd} pada surat \"{s.perihal}\"", sejak, s.id)
        elif s.status == "masuk" and s.jenis_surat == "eksternal":
            if not db.query(models.Disposisi).filter(models.Disposisi.surat_id == s.id).first() and direktur:
                tambah("surat", direktur.nip_pimpinan, "Disposisi surat masuk", s.tanggal_dibuat, s.id)

    # 2. Disposisi yang belum diproses penerimanya
    disp = db.query(models.Disposisi).filter(models.Disposisi.surat_id.in_(ids)).all()
    punya_anak = {d.disposisi_asal_id for d in disp if d.disposisi_asal_id}
    for d in disp:
        if d.status == "diteruskan" and d.id not in punya_anak:
            tambah("disposisi", d.nip_ke, "Meneruskan / menugaskan / mengarsipkan disposisi",
                   d.tanggal, d.surat_id)

    # 3. Penugasan yang masih berjalan
    for p in db.query(models.Penugasan).filter(
        models.Penugasan.surat_id.in_(ids), models.Penugasan.status == "proses"
    ).all():
        if p.surat_id in rapat_ids:
            laporan = _laporan_rapat_surat(p.surat_id, db)
            baris = _baris_peserta_rapat(laporan.id, p.id, db) if laporan else None
            notulen = bool(baris.is_notulen) if baris else bool(db.query(models.PenerimaSuratTugas).filter(
                models.PenerimaSuratTugas.surat_id == p.surat_id,
                models.PenerimaSuratTugas.nip_pegawai == p.nip_penerima,
                models.PenerimaSuratTugas.is_notulen == True).first())
            hadir = bool(baris and baris.status_hadir == "hadir")
            if not notulen:
                tambah("rapat", p.nip_penerima, "Absensi rapat", p.tanggal_ditugaskan, p.surat_id)
            elif not hadir:
                tambah("rapat", p.nip_penerima, "Absensi rapat (juga notulen)", p.tanggal_ditugaskan, p.surat_id)
            elif not laporan or laporan.status == "draft":
                tambah("notulensi", p.nip_penerima, "Menyusun & mengirim notulensi", p.tanggal_ditugaskan, p.surat_id)
            elif laporan.status == "revisi":
                tambah("notulensi", p.nip_penerima,
                       f"Merevisi notulensi: {laporan.catatan_revisi or '-'}", p.tanggal_ditugaskan, p.surat_id)
            elif laporan.status == "menunggu_ttd":
                s_r = next((x for x in surat_list if x.id == p.surat_id), None)
                tambah("notulensi", s_r.nip_pimpinan_rapat if s_r else None,
                       "TTE notulensi (pimpinan rapat)", laporan.tanggal_kirim, p.surat_id)
            continue

        sudah_lapor = db.query(models.LaporanTugas).filter(
            models.LaporanTugas.penugasan_id == p.id).first()
        if sudah_lapor:
            tambah("tugas", p.nip_pemberi, "Menilai laporan tugas", sudah_lapor.tanggal_lapor, p.surat_id)
        elif p.surat_id in pd_ids:
            lap = _laporan_pd_surat(p.surat_id, db)
            if not lap:
                tambah("laporan_pd", p.nip_penerima, "Menyusun laporan perjalanan dinas",
                       p.tanggal_ditugaskan, p.surat_id)
            else:
                r = db.query(models.LaporanPDPeserta).filter(
                    models.LaporanPDPeserta.laporan_id == lap.id,
                    models.LaporanPDPeserta.nip_pegawai == p.nip_penerima).first()
                if r and not r.sudah_ttd:
                    tambah("laporan_pd", p.nip_penerima, "TTE laporan perjalanan dinas",
                           lap.tanggal_dibuat, p.surat_id)
        else:
            st = db.query(models.Surat).filter(
                models.Surat.penugasan_asal_id == p.id).order_by(models.Surat.id.desc()).first()
            if st and st.status == "selesai":
                apa = "Mengirim laporan tugas (surat tugas sudah terbit & final, tinggal kirim laporan)"
            elif st:
                apa = f"Mengirim laporan tugas (surat tugas masih berstatus '{st.status}')"
            else:
                apa = "Mengirim laporan tugas"
            tambah("tugas", p.nip_penerima, apa, p.tanggal_ditugaskan, p.surat_id)

    out.sort(key=lambda h: -(h["hari"] or 0))
    return out

@app.get("/api/riwayat-kasus")
def get_riwayat_kasus(nip: str, db: Session = Depends(get_db)):
    """Riwayat Saya dikelompokkan per kasus (akar surat yang sama menjadi satu grup)."""
    langkah = get_riwayat_saya_lengkap(nip=nip, db=db)

    cache_akar = {}
    kelompok = {}
    for item in langkah:
        sid = item.get("surat_id") or item.get("surat_ref_id")
        kunci = None
        if sid:
            if sid not in cache_akar:
                r = cari_akar_kasus(sid, db)
                cache_akar[sid] = r.id if r else None
            kunci = cache_akar[sid]
        item["judul_ringkas"], item["detail_label"] = _pecah_label(item.get("label_aksi"))
        kelompok.setdefault(kunci, []).append(item)

    hasil = []
    for kunci, daftar in kelompok.items():
        daftar.sort(key=lambda x: x.get("tanggal") or "")      # kronologis: lama -> baru
        terakhir = daftar[-1].get("tanggal")
        root = db.query(models.Surat).filter(models.Surat.id == kunci).first() if kunci else None

        if not root:
            hasil.append({"kasus_id": None, "judul": "Aktivitas lain", "jenis_surat": None,
                          "nomor_surat": None, "status_kasus": "lain", "tahap": [],
                          "jumlah_langkah": len(daftar), "tanggal_terakhir": terakhir,
                          "langkah": daftar})
            continue

        ids_kasus = ambil_semua_id_kasus(root.id, db)
        tahap = _tahap_kasus(root, ids_kasus, db)
        selesai = all(t["status"] == "selesai" for t in tahap)
        menunggu = [] if selesai else _hambatan_kasus(ids_kasus, db)
        hasil.append({
            "kasus_id": root.id, "judul": root.perihal, "jenis_surat": root.jenis_surat,
            "nomor_surat": root.nomor_surat,
            "status_kasus": "selesai" if selesai else "berjalan",
            "tahap": tahap, "menunggu": menunggu, "jumlah_langkah": len(daftar),
            "tanggal_terakhir": terakhir, "langkah": daftar,
        })

    hasil.sort(key=lambda g: (g["kasus_id"] is not None, g["tanggal_terakhir"] or ""), reverse=True)
    return hasil

# ==========================================================================
# 23. POHON TRACKING KASUS & DAFTAR SEMUA KASUS (DIREKTUR)
# ==========================================================================
def _fmt_waktu(dt) -> Optional[str]:
    return str(dt)[:16] if dt else None


def bangun_pohon_kasus(root, nip_pelihat: str, db: Session):
    """Return (pohon, set_nip_terlibat). File dibatasi menurut hak akses pelihat."""
    terlibat, dikunjungi = set(), set()
    pelihat_direktur = _is_direktur(nip_pelihat, db)

    def nodus(tipe, judul, sub="", status="info", tanggal=None, surat_id=None, files=None, nips=()):
        for n in nips:
            if n:
                terlibat.add(n)
        return {"tipe": tipe, "judul": judul, "sub": sub, "status": status,
                "tanggal": _fmt_waktu(tanggal), "surat_id": surat_id,
                "files": files or [], "anak": []}

    def nama(n):
        return _nama_pegawai(n, db) if n else "—"

    # ---------- Penugasan ----------
    def nodus_penugasan(p):
        n = nodus("tugas", f"Tugas: {nama(p.nip_pemberi)} → {nama(p.nip_penerima)}",
                  (p.deskripsi_tugas or "")[:140],
                  "selesai" if p.status == "selesai" else "berjalan",
                  p.tanggal_ditugaskan, nips=(p.nip_pemberi, p.nip_penerima))
        for st in db.query(models.Surat).filter(models.Surat.penugasan_asal_id == p.id).order_by(models.Surat.id).all():
            if st.id not in dikunjungi:
                n["anak"].append(nodus_surat(st))
        lap = db.query(models.LaporanTugas).filter(
            models.LaporanTugas.penugasan_id == p.id).order_by(models.LaporanTugas.id.desc()).first()
        if lap:
            files = [{"label": "Bukti laporan", "url": f"/uploads/{lap.file_bukti}"}] if lap.file_bukti else []
            n["anak"].append(nodus("laporan", "Laporan tugas dikirim", (lap.isi_laporan or "")[:140],
                                   "selesai", lap.tanggal_lapor, files=files))
        nilai = db.query(models.Penilaian).filter(models.Penilaian.penugasan_id == p.id).first()
        if nilai:
            n["anak"].append(nodus("nilai", f"Dinilai: {(nilai.skala or '').replace('_', ' ')}",
                                   (nilai.catatan or "")[:140], "selesai", None, nips=(nilai.nip_penilai,)))
        return n

    # ---------- Disposisi ----------
    def nodus_disposisi(d):
        anak_d = db.query(models.Disposisi).filter(models.Disposisi.disposisi_asal_id == d.id).order_by(models.Disposisi.id).all()
        if d.status == "diteruskan":
            status = "selesai" if anak_d else "berjalan"
            ket = "Sudah diteruskan" if anak_d else "Menunggu diproses penerima"
        elif d.status == "diarsipkan":
            status, ket = "selesai", "Diarsipkan"
        else:
            status, ket = "selesai", "Dijadikan tugas"
        n = nodus("disposisi", f"Disposisi: {nama(d.nip_dari)} → {nama(d.nip_ke)}",
                  f"{ket}. Catatan: {(d.catatan or '')[:120]}", status, d.tanggal,
                  nips=(d.nip_dari, d.nip_ke))
        for ad in anak_d:
            n["anak"].append(nodus_disposisi(ad))
        for p in db.query(models.Penugasan).filter(models.Penugasan.disposisi_id == d.id).order_by(models.Penugasan.id).all():
            n["anak"].append(nodus_penugasan(p))
        for st in db.query(models.Surat).filter(models.Surat.disposisi_asal_id == d.id).order_by(models.Surat.id).all():
            if st.id not in dikunjungi:
                n["anak"].append(nodus_surat(st))
        return n

    # ---------- Rapat ----------
    def nodus_rapat(s):
        laporan = _laporan_rapat_surat(s.id, db)
        info = _info_rapat(s)
        pimpinan = s.nip_pimpinan_rapat
        n = nodus("rapat", "Pelaksanaan Rapat",
                  f"{info['agenda']} • {info['hari_tanggal']}, {info['waktu']} • {info['tempat']} • Pimpinan: {nama(pimpinan)}",
                  "selesai" if (laporan and laporan.status != "draft") else ("berjalan" if s.status == "selesai" else "belum"),
                  None, nips=(pimpinan,))
        baris_map = {}
        if laporan:
            for r in db.query(models.LaporanRapatPeserta).filter(models.LaporanRapatPeserta.laporan_id == laporan.id).all():
                baris_map[r.penugasan_id] = r
        for p in db.query(models.Penugasan).filter(models.Penugasan.surat_id == s.id).order_by(models.Penugasan.id).all():
            r = baris_map.get(p.id)
            st_hadir = (r.status_hadir if r else "belum") or "belum"
            ket = LABEL_HADIR.get(st_hadir, st_hadir)
            if r and r.nip_mewakili:
                ket += f" • mewakili {nama(r.nip_mewakili)}"
            if r and r.alasan:
                ket += f" • {r.alasan[:80]}"
            judul = nama(p.nip_penerima) + (" (notulen)" if (r and r.is_notulen) else "")
            n["anak"].append(nodus("peserta", judul, ket,
                                   "berjalan" if st_hadir == "belum" else "selesai",
                                   r.tanggal_hadir if r else None, nips=(p.nip_penerima,)))
        if laporan:
            sub = {"draft": "Notulen sedang menyusun",
                   "revisi": f"Revisi diminta: {(laporan.catatan_revisi or '-')[:100]}",
                   "menunggu_ttd": "Menunggu TTE pimpinan rapat",
                   "final": "Final dan terkunci"}.get(laporan.status, laporan.status)
            files = []
            if laporan.file_notulen and (pelihat_direktur or nip_pelihat == pimpinan):
                files.append({"label": "Lihat Notulen", "url": f"/api/rapat/notulen-file/{laporan.id}?nip={nip_pelihat}"})
            if laporan.file_daftar_hadir and laporan.status == "final":
                files.append({"label": "Lihat Daftar Hadir", "url": f"/uploads/{laporan.file_daftar_hadir}"})
            n["anak"].append(nodus("notulensi", "Notulensi & Daftar Hadir", sub,
                                   "selesai" if laporan.status == "final" else "berjalan",
                                   laporan.tanggal_final or laporan.tanggal_kirim, files=files))
        return n

    # ---------- Surat ----------
    def nodus_surat(s):
        dikunjungi.add(s.id)
        if s.jenis_surat == "permohonan":
            jenis = {"rapat": "Undangan Rapat", "perjalanan_dinas": "Surat Tugas Perjalanan Dinas"}.get(s.jenis_tugas, "Surat Tugas")
        else:
            jenis = {"eksternal": "Surat Masuk (Eksternal)", "internal": "Surat Internal"}.get(s.jenis_surat, "Surat")
        status = {"selesai": "selesai", "draft": "belum"}.get(s.status, "berjalan")
        sub = f"{s.perihal}" + (f" • No. {s.nomor_surat}" if s.nomor_surat else "") + \
              (f" • Asal: {s.asal_surat}" if s.jenis_surat == "eksternal" and s.asal_surat else "") + \
              f" • Status: {s.status}"
        n = nodus("surat", jenis, sub, status, s.tanggal_dibuat, surat_id=s.id,
                  nips=(s.nip_pembuat, s.nip_tujuan, s.nip_penandatangan, s.nip_pimpinan_rapat))
        n["sub"] = f"Dibuat oleh {nama(s.nip_pembuat)} • " + n["sub"]

        for pr in db.query(models.PersetujuanSurat).filter(
                models.PersetujuanSurat.surat_id == s.id).order_by(models.PersetujuanSurat.urutan).all():
            st_p = {"disetujui": "selesai", "ditolak": "batal"}.get(pr.status_persetujuan, "berjalan")
            n["anak"].append(nodus("ttd", f"{pr.jenis_ttd}: {nama(pr.nip_penyetuju)}",
                                   (pr.catatan or "")[:100], st_p, pr.tanggal_proses, nips=(pr.nip_penyetuju,)))
        for pn in db.query(models.PenerimaSuratTugas).filter(models.PenerimaSuratTugas.surat_id == s.id).all():
            terlibat.add(pn.nip_pegawai)

        ada_laporan_rapat = _laporan_rapat_surat(s.id, db) is not None
        if s.jenis_tugas == "rapat" or ada_laporan_rapat:
            n["anak"].append(nodus_rapat(s))
        elif s.jenis_tugas == "perjalanan_dinas":
            lap = _laporan_pd_surat(s.id, db)
            if lap:
                ps = db.query(models.LaporanPDPeserta).filter(models.LaporanPDPeserta.laporan_id == lap.id).all()
                n["anak"].append(nodus("laporan_pd", "Laporan Perjalanan Dinas",
                                       f"{sum(1 for x in ps if x.sudah_ttd)}/{len(ps)} peserta sudah TTE",
                                       "selesai" if lap.status == "final" else "berjalan",
                                       lap.tanggal_final or lap.tanggal_dibuat,
                                       files=[{"label": "Laporan (PDF)", "url": f"/api/laporan-pd/{lap.id}/pdf"}] if lap.file_pdf else [],
                                       nips=tuple(x.nip_pegawai for x in ps)))

        for d in db.query(models.Disposisi).filter(
                models.Disposisi.surat_id == s.id, models.Disposisi.disposisi_asal_id.is_(None)
        ).order_by(models.Disposisi.id).all():
            n["anak"].append(nodus_disposisi(d))

        if s.jenis_tugas != "rapat":
            for p in db.query(models.Penugasan).filter(
                    models.Penugasan.surat_id == s.id, models.Penugasan.disposisi_id.is_(None)
            ).order_by(models.Penugasan.id).all():
                n["anak"].append(nodus_penugasan(p))

        for lanjut in db.query(models.Surat).filter(models.Surat.surat_asal_id == s.id).order_by(models.Surat.id).all():
            if lanjut.id not in dikunjungi:
                n["anak"].append(nodus_surat(lanjut))
        return n

    return nodus_surat(root), terlibat


@app.get("/api/pohon-kasus/{surat_id}")
def get_pohon_kasus(surat_id: int, nip: str, db: Session = Depends(get_db)):
    """Pohon alur satu kasus. Boleh dilihat: Direktur + semua pegawai yang terlibat."""
    root = cari_akar_kasus(surat_id, db)
    if not root:
        raise HTTPException(404, "Surat tidak ditemukan")
    pohon, terlibat = bangun_pohon_kasus(root, nip, db)
    if not (_is_direktur(nip, db) or nip in terlibat):
        raise HTTPException(403, "Anda tidak terlibat dalam kasus ini")

    ids = ambil_semua_id_kasus(root.id, db)
    tahap = _tahap_kasus(root, ids, db)
    selesai = all(t["status"] == "selesai" for t in tahap)
    return {
        "kasus_id": root.id, "judul": root.perihal, "jenis_surat": root.jenis_surat,
        "nomor_surat": root.nomor_surat,
        "status_kasus": "selesai" if selesai else "berjalan",
        "tahap": tahap,
        "menunggu": [] if selesai else _hambatan_kasus(ids, db),
        "pohon": pohon,
    }


@app.get("/api/semua-kasus")
def get_semua_kasus(nip: str, db: Session = Depends(get_db)):
    """Khusus Direktur: seluruh kasus (surat akar) walau Direktur tidak terlibat."""
    if not _is_direktur(nip, db):
        raise HTTPException(403, "Hanya Direktur yang boleh melihat seluruh kasus")
    hasil = []
    for s in db.query(models.Surat).order_by(models.Surat.id.desc()).limit(300).all():
        if s.status == "draft" or s.surat_asal_id or s.disposisi_asal_id or s.penugasan_asal_id:
            continue
        ids = ambil_semua_id_kasus(s.id, db)
        tahap = _tahap_kasus(s, ids, db)
        selesai = all(t["status"] == "selesai" for t in tahap)
        menunggu = [] if selesai else _hambatan_kasus(ids, db)
        hasil.append({
            "kasus_id": s.id, "judul": s.perihal, "jenis_surat": s.jenis_surat,
            "nomor_surat": s.nomor_surat, "pembuat": _nama_pegawai(s.nip_pembuat, db),
            "tanggal": _fmt_waktu(s.tanggal_dibuat),
            "status_kasus": "selesai" if selesai else "berjalan",
            "tahap": tahap, "jumlah_menunggu": len(menunggu),
            "menunggu_utama": (f"{menunggu[0]['nama']}: {menunggu[0]['apa']}" if menunggu else None),
        })
    return hasil

# ==========================================================================
# 24. KOREKSI PENUGASAN (edit instruksi / ganti penerima oleh pemberi tugas)
# ==========================================================================
def penugasan_bisa_dikoreksi(p, surat, db: Session) -> bool:
    """Penugasan manual (surat eksternal/internal), masih 'proses', penerima belum bertindak."""
    if p.status != "proses":
        return False
    if not surat or surat.jenis_surat not in ("eksternal", "internal"):
        return False
    if db.query(models.LaporanTugas).filter(models.LaporanTugas.penugasan_id == p.id).first():
        return False
    if db.query(models.Surat).filter(models.Surat.penugasan_asal_id == p.id).first():
        return False
    return True


def _penugasan_koreksi(penugasan_id: int, nip_pemberi: str, db: Session):
    p = db.query(models.Penugasan).filter(models.Penugasan.id == penugasan_id).first()
    if not p:
        raise HTTPException(404, "Penugasan tidak ditemukan")
    if p.nip_pemberi != nip_pemberi:
        raise HTTPException(403, "Hanya pemberi tugas yang boleh mengoreksi penugasan ini")
    surat = db.query(models.Surat).filter(models.Surat.id == p.surat_id).first()
    if not penugasan_bisa_dikoreksi(p, surat, db):
        raise HTTPException(400, "Penugasan tidak bisa dikoreksi lagi (penerima sudah bertindak atau penugasan otomatis)")
    return p


class KoreksiTugasRequest(BaseModel):
    nip_pemberi: str
    deskripsi_tugas: Optional[str] = None
    deadline: Optional[str] = None          # YYYY-MM-DD, kosong = tanpa deadline


class GantiPenerimaRequest(BaseModel):
    nip_pemberi: str
    nip_penerima_baru: str


@app.put("/api/penugasan/{penugasan_id}")
def edit_penugasan(penugasan_id: int, req: KoreksiTugasRequest, db: Session = Depends(get_db)):
    p = _penugasan_koreksi(penugasan_id, req.nip_pemberi, db)

    deadline = None
    if req.deadline and req.deadline.strip():
        try:
            deadline = datetime.strptime(req.deadline.strip(), "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format deadline harus YYYY-MM-DD")

    p.deskripsi_tugas = (req.deskripsi_tugas or "").strip() or None
    p.deadline = deadline
    db.commit()
    return {"message": "Penugasan diperbarui."}


@app.post("/api/penugasan/{penugasan_id}/ganti-penerima")
def ganti_penerima_penugasan(penugasan_id: int, req: GantiPenerimaRequest, db: Session = Depends(get_db)):
    p = _penugasan_koreksi(penugasan_id, req.nip_pemberi, db)

    if req.nip_penerima_baru == p.nip_penerima:
        raise HTTPException(400, "Penerima baru sama dengan penerima saat ini")

    pemberi = db.query(models.Pegawai).filter(models.Pegawai.nip == p.nip_pemberi).first()
    baru = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip_penerima_baru).first()
    if not pemberi or not baru:
        raise HTTPException(404, "Pegawai tidak ditemukan")

    unit_pemberi = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pemberi.kode_unit).first()
    unit_baru = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == baru.kode_unit).first()
    boleh = ((unit_baru and unit_pemberi and unit_baru.level > unit_pemberi.level)
             or baru.kode_unit == pemberi.kode_unit)
    if not boleh:
        raise HTTPException(400, f"{baru.nama} tidak bisa ditugaskan (bukan bawahan / bukan di unit yang sama)")

    ganda = db.query(models.Penugasan).filter(
        models.Penugasan.surat_id == p.surat_id,
        models.Penugasan.disposisi_id == p.disposisi_id,
        models.Penugasan.nip_penerima == baru.nip,
        models.Penugasan.status == "proses",
        models.Penugasan.id != p.id
    ).first()
    if ganda:
        raise HTTPException(400, f"{baru.nama} sudah menerima penugasan yang sama")

    p.nip_penerima = baru.nip
    db.commit()
    return {"message": f"Penugasan dialihkan ke {baru.nama}."}

class HapusPenugasanRequest(BaseModel):
    nip_pemberi: str


@app.post("/api/penugasan/{penugasan_id}/hapus")
def hapus_penugasan(penugasan_id: int, req: HapusPenugasanRequest, db: Session = Depends(get_db)):
    p = _penugasan_koreksi(penugasan_id, req.nip_pemberi, db)
    surat_id, disposisi_id = p.surat_id, p.disposisi_id

    db.delete(p)
    db.flush()

    if disposisi_id:
        masih_ada = db.query(models.Penugasan).filter(
            models.Penugasan.disposisi_id == disposisi_id).first()
        d = db.query(models.Disposisi).filter(models.Disposisi.id == disposisi_id).first()
        if d and not masih_ada and d.status == "diproses_jadi_tugas":
            d.status = "diteruskan"      # kembali ke inbox penerima disposisi
        hitung_ulang_status_surat(surat_id, db)
    else:
        masih_ada = db.query(models.Penugasan).filter(
            models.Penugasan.surat_id == surat_id).first()
        surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
        if surat and not masih_ada:
            surat.status = "diproses"
        else:
            hitung_ulang_status_surat(surat_id, db)

    db.commit()
    return {"message": "Penugasan dihapus."}

# ==========================================================================
# 25. PORTAL PELACAKAN PUBLIK (tanpa login)
# ==========================================================================
POLA_KODE      = re.compile(r"^PPNP-[A-Z0-9]{6}$")
BATAS_LACAK    = 10     # maksimal percobaan ...
JENDELA_LACAK  = 60     # ... per 60 detik per IP
TRUST_PROXY    = os.getenv("TRUST_PROXY", "0") == "1"   # set "1" HANYA jika di belakang nginx/proxy
_RIWAYAT_LACAK = defaultdict(deque)
TEKS_TIDAK_ADA = "Kode pelacakan tidak ditemukan. Periksa kembali kode pada tanda terima Anda."


def _ip_klien(request: Request) -> str:
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def cek_rate_limit_lacak(ip: str):
    sekarang = time.time()
    antrian = _RIWAYAT_LACAK[ip]
    while antrian and sekarang - antrian[0] > JENDELA_LACAK:
        antrian.popleft()
    if len(antrian) >= BATAS_LACAK:
        raise HTTPException(429, "Terlalu banyak percobaan. Silakan coba lagi dalam 1 menit.")
    antrian.append(sekarang)

    # bersihkan IP lama agar memori tidak membengkak
    if len(_RIWAYAT_LACAK) > 5000:
        for k in [k for k, v in _RIWAYAT_LACAK.items() if not v or sekarang - v[-1] > JENDELA_LACAK]:
            del _RIWAYAT_LACAK[k]


def _fmt_wib(dt) -> Optional[str]:
    return (dt + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M") if dt else None


@app.get("/lacak", response_class=HTMLResponse)
def halaman_lacak(request: Request):
    return templates.TemplateResponse(request=request, name="publik/lacak.html")

def hasil_akhir_publik(ids: list, db: Session) -> dict:
    """
    Jenis hasil untuk pengirim, dihitung dari data kasus.
    Tidak membocorkan nama pegawai, catatan disposisi, maupun nilai.
    """
    disposisi = db.query(models.Disposisi).filter(models.Disposisi.surat_id.in_(ids)).all()
    penugasan = db.query(models.Penugasan).filter(models.Penugasan.surat_id.in_(ids)).all()
    surat_tugas = db.query(models.Surat).filter(
        models.Surat.id.in_(ids), models.Surat.jenis_surat == "permohonan"
    ).all()

    ada_rapat = any(s.jenis_tugas == "rapat" for s in surat_tugas)
    ada_dinas = any(s.jenis_tugas == "perjalanan_dinas" for s in surat_tugas)
    # penugasan dari surat tugas rapat/dinas tidak dihitung ganda sebagai "penugasan biasa"
    id_surat_tugas = {s.id for s in surat_tugas}
    penugasan_biasa = [p for p in penugasan if p.surat_id not in id_surat_tugas]
    ada_tindak_lanjut = bool(penugasan_biasa) or bool(surat_tugas) or any(
        d.status == "diproses_jadi_tugas" for d in disposisi
    )

    if ada_tindak_lanjut:
        if ada_rapat:
            ket = "PPNP menindaklanjuti surat Anda dengan menugaskan perwakilan untuk menghadiri kegiatan/rapat yang dimaksud."
        elif ada_dinas:
            ket = "PPNP menindaklanjuti surat Anda dengan menugaskan pegawai untuk melaksanakan kegiatan yang dimaksud."
        else:
            ket = "Surat Anda telah ditindaklanjuti oleh unit terkait di PPNP."
        return {"kode": "ditindaklanjuti", "judul": "Ditindaklanjuti", "ikon": "✅", "keterangan": ket}

    if disposisi and all(d.status == "diarsipkan" for d in disposisi):
        return {"kode": "diarsipkan", "judul": "Diterima dan Diarsipkan", "ikon": "🗄️",
                "keterangan": "Surat Anda bersifat informasi sehingga dicatat dan diarsipkan. "
                              "Tidak ada tindak lanjut maupun surat balasan atas surat ini."}

    return {"kode": "lainnya", "judul": "Selesai Diproses", "ikon": "✔️",
            "keterangan": "Proses atas surat Anda telah selesai."}

@app.get("/api/publik/lacak")
def lacak_publik(request: Request, kode: str, db: Session = Depends(get_db)):
    """Status surat eksternal untuk pengirim. TIDAK membocorkan nama pegawai, catatan, atau nilai."""
    cek_rate_limit_lacak(_ip_klien(request))

    kode = (kode or "").strip().upper()
    if kode and not kode.startswith("PPNP-"):
        kode = "PPNP-" + kode
    if not POLA_KODE.match(kode):
        raise HTTPException(404, TEKS_TIDAK_ADA)

    surat = db.query(models.Surat).filter(
        models.Surat.kode_pelacakan == kode,
        models.Surat.jenis_surat == "eksternal"
    ).first()
    if not surat:
        raise HTTPException(404, TEKS_TIDAK_ADA)

    # --- Tentukan posisi di 3 tahap ---
    if surat.status == "draft" and surat.catatan_kembali:
        kode_status = "dikembalikan"
        judul = "Berkas Perlu Diperbaiki"
        ket = "Surat dikembalikan karena berkas perlu dilengkapi atau diperbaiki. Silakan hubungi petugas persuratan PPNP."
        saat_ini = 0
    elif surat.status == "draft":
        kode_status = "tercatat"
        judul = "Surat Tercatat"
        ket = "Surat Anda telah tercatat dan sedang disiapkan oleh petugas untuk diteruskan ke Pimpinan."
        saat_ini = 0
    elif surat.status == "masuk":
        kode_status = "diterima"
        judul = "Surat Diterima"
        ket = "Surat Anda telah diterima dan menunggu ditinjau oleh Pimpinan."
        saat_ini = 0
    elif surat.status == "selesai":
        kode_status = "selesai"
        judul = "Selesai Diproses"
        ket = "Proses tindak lanjut atas surat Anda telah selesai."
        saat_ini = 3
    else:   # diproses, sedang_dikerjakan, dll.
        kode_status = "diproses"
        judul = "Sedang Diproses"
        ket = "Surat Anda sedang ditindaklanjuti oleh unit terkait di PPNP."
        saat_ini = 1

    nama_tahap = ["Diterima", "Diproses", "Selesai"]
    tahap = []
    for i, nama in enumerate(nama_tahap):
        if i < saat_ini:
            st = "selesai"
        elif i == saat_ini:
            st = "terhenti" if kode_status == "dikembalikan" else "berjalan"
        else:
            st = "belum"
        tahap.append({"label": nama, "status": st})

    # --- Update terakhir & lama proses (dari seluruh kasus) ---
    ids = ambil_semua_id_kasus(surat.id, db)
    tanggal = [surat.tanggal_dibuat]
    tanggal += [d.tanggal for d in db.query(models.Disposisi).filter(
        models.Disposisi.surat_id.in_(ids)).all()]
    penugasan = db.query(models.Penugasan).filter(models.Penugasan.surat_id.in_(ids)).all()
    tanggal += [p.tanggal_ditugaskan for p in penugasan]
    if penugasan:
        tanggal += [n.tanggal_nilai for n in db.query(models.Penilaian).filter(
            models.Penilaian.penugasan_id.in_([p.id for p in penugasan])).all()]
    tanggal = [t for t in tanggal if t]
    terakhir = max(tanggal) if tanggal else surat.tanggal_dibuat

    akhir = terakhir if kode_status == "selesai" else datetime.utcnow()
    lama_hari = max(0, (akhir - surat.tanggal_dibuat).days) if surat.tanggal_dibuat else None

    hasil = hasil_akhir_publik(ids, db) if kode_status == "selesai" else None

    return {
        "kode"            : surat.kode_pelacakan,
        "perihal"         : surat.perihal,
        "tanggal_surat"   : surat.tanggal_surat,
        "tanggal_diterima": surat.tanggal_diterima,
        "status_kode"     : kode_status,
        "judul_status"    : judul,
        "keterangan"      : ket,
        "tahap"           : tahap,
        "update_terakhir" : _fmt_wib(terakhir),
        "lama_hari"       : lama_hari,
        "hasil_akhir"     : hasil,
    }

# ==========================================================================
# 26. TANDA TERIMA SURAT EKSTERNAL (PDF)
# ==========================================================================
@app.get("/api/surat/{surat_id}/tanda-terima")
def unduh_tanda_terima(surat_id: int, nip: str, db: Session = Depends(get_db)):
    """PDF tanda terima untuk pengirim surat eksternal. Hanya pembuat surat yang boleh mencetak."""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or surat.jenis_surat != "eksternal":
        raise HTTPException(404, "Surat eksternal tidak ditemukan")
    if surat.nip_pembuat != nip:
        raise HTTPException(403, "Hanya petugas pencatat surat yang boleh mencetak tanda terima")

    petugas = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    e = lambda t: _esc(str(t or "-")).replace("\n", "<br>")

    url_lacak = f"{BASE_URL}/lacak?kode={surat.kode_pelacakan}"
    arsip = pymupdf.Archive()
    arsip.add(buat_qr_png(url_lacak), "qr.png")
    kop = _siapkan_kop(arsip, "D001")

    dicetak = _sekarang_wib().strftime("%d-%m-%Y %H:%M")
    html = f"""
    {kop}
    <h3 class="tengah">TANDA TERIMA SURAT</h3>
    <table class="info" width="100%">
      <tr><td width="110">Nomor Surat</td><td width="10">:</td><td>{e(surat.nomor_surat)}</td></tr>
      <tr><td>Asal Surat</td><td>:</td><td>{e(surat.asal_surat)}</td></tr>
      <tr><td>Perihal</td><td>:</td><td>{e(surat.perihal)}</td></tr>
      <tr><td>Tanggal Surat</td><td>:</td><td>{e(tanggal_indo(surat.tanggal_surat))}</td></tr>
      <tr><td>Tanggal Diterima</td><td>:</td><td>{e(tanggal_indo(surat.tanggal_diterima))}</td></tr>
    </table>
    <br>
    <table class="tbl">
      <tr>
        <td class="c" width="150"><img src="qr.png" width="110"><br>
            <span class="kecil">Scan untuk melacak</span></td>
        <td class="c">
          <span class="kecil">KODE PELACAKAN</span><br>
          <b class="besar">{e(surat.kode_pelacakan)}</b><br><br>
          <span class="kecil">Lacak status surat Anda kapan saja di:<br>
          <b>{e(BASE_URL)}/lacak</b></span>
        </td>
      </tr>
    </table>
    <br>
    <table width="100%"><tr><td width="55%">&nbsp;</td><td class="c">
      Petugas Penerima,<br><br><br><br>
      <b>{e(petugas.nama if petugas else '-')}</b><br>
      <span class="kecil">Dicetak: {dicetak} WIB</span>
    </td></tr></table>
    <p class="kecil">Simpan tanda terima ini. Kode pelacakan diperlukan untuk memantau proses surat Anda.</p>
    """
    sekarang = datetime.utcnow()
    folder = f"{DIR_PRIVATE}/tanda_terima"
    os.makedirs(folder, exist_ok=True)
    path = f"{folder}/TT_{surat.id}_{int(time.time())}.pdf"
    _render_story_pdf(html, _CSS_RAPAT, arsip, path)
    return FileResponse(path, media_type="application/pdf",
                        filename=f"TandaTerima_{surat.kode_pelacakan}.pdf")

# ==========================================================================
# 27. ARSIP SURAT (disposisi berstatus 'diarsipkan')
# ==========================================================================
def _rantai_disposisi(d, db: Session) -> list:
    """Jalur dari disposisi akar sampai node d (urut: akar -> d)."""
    rantai, dilihat = [d], {d.id}
    while rantai[0].disposisi_asal_id and rantai[0].disposisi_asal_id not in dilihat:
        induk = db.query(models.Disposisi).filter(
            models.Disposisi.id == rantai[0].disposisi_asal_id).first()
        if not induk:
            break
        dilihat.add(induk.id)
        rantai.insert(0, induk)
    return rantai


@app.get("/arsip", response_class=HTMLResponse)
def halaman_arsip(request: Request):
    return templates.TemplateResponse(request=request, name="admin/arsip.html")


@app.get("/api/arsip")
def get_arsip(nip: str, q: str = "", tahun: Optional[int] = None,
              halaman: int = 1, per_halaman: int = 10, db: Session = Depends(get_db)):
    """Arsip yang boleh dilihat pegawai ini: semua pejabat di jalur disposisi sampai node pengarsip."""
    cache_nama = {}
    def nama(n):
        if n not in cache_nama:
            cache_nama[n] = _nama_pegawai(n, db)
        return cache_nama[n]

    kata = (q or "").lower().split()
    daftar = []
    for d in db.query(models.Disposisi).filter(
        models.Disposisi.status == "diarsipkan"
    ).order_by(models.Disposisi.id.desc()).all():
        rantai = _rantai_disposisi(d, db)
        terlibat = {x.nip_dari for x in rantai} | {x.nip_ke for x in rantai}
        if nip not in terlibat:
            continue

        surat = db.query(models.Surat).filter(models.Surat.id == d.surat_id).first()
        if not surat:
            continue

        instruksi, _, alasan = (d.catatan or "").partition("[Diarsipkan]:")
        tgl = d.tanggal_arsip or d.tanggal
        if tahun and (not tgl or (tgl + timedelta(hours=7)).year != tahun):
            continue

        jalur = [nama(rantai[0].nip_dari)] + [nama(x.nip_ke) for x in rantai]
        berkas = None
        if surat.file_final:
            berkas = f"/uploads/{surat.file_final}"
        elif surat.file_surat:
            berkas = f"/uploads/{surat.file_surat}"

        item = {
            "disposisi_id"   : d.id,
            "surat_id"       : surat.id,
            "perihal"        : surat.perihal,
            "asal_surat"     : surat.asal_surat,
            "nomor_surat"    : surat.nomor_surat,
            "jenis_surat"    : surat.jenis_surat,
            "tanggal_diterima": surat.tanggal_diterima or surat.tanggal_surat,
            "pengarsip"      : nama(d.nip_ke),
            "tanggal_arsip"  : _fmt_wib(tgl),
            "alasan"         : alasan.strip(),
            "instruksi"      : instruksi.strip(),
            "jalur"          : jalur,
            "url_berkas"     : berkas,
        }
        if kata:
            gabung = " ".join(str(item[k] or "") for k in
                              ("perihal", "asal_surat", "nomor_surat", "alasan", "pengarsip")).lower()
            if not all(k in gabung for k in kata):
                continue
        daftar.append(item)

    daftar.sort(key=lambda x: x["tanggal_arsip"] or "", reverse=True)
    per_halaman = max(1, min(per_halaman, 50))
    halaman = max(1, halaman)
    mulai = (halaman - 1) * per_halaman
    return {
        "total"      : len(daftar),
        "halaman"    : halaman,
        "per_halaman": per_halaman,
        "item"       : daftar[mulai:mulai + per_halaman],
    }