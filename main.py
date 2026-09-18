from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models
import os
import shutil
import string
import random
from typing import Optional, List
from fastapi.responses import FileResponse
import pymupdf
import qrcode
import io
import re
import json
import time
import bcrypt
from datetime import datetime
from PIL import Image
import hmac
import hashlib
import re as re_module

# ⚠️ GANTI dengan string acak panjang milikmu sendiri, JANGAN dibagikan ke siapapun.
# Ini kunci rahasia yang membuat signature tidak bisa dipalsukan orang lain.
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
SECRET_KEY_VERIFIKASI = "8f3e1a9c7d2b4f6e8a1c3d5f7b9e2a4c6d8f0b2e4a6c8d0f2b4e6a8c0d2f4b6e"

def buat_signature(kode_pelacakan: str) -> str:
    """Buat signature HMAC untuk kode_pelacakan, dipakai di URL verifikasi QR."""
    return hmac.new(
        SECRET_KEY_VERIFIKASI.encode("utf-8"),
        kode_pelacakan.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()[:16]   # ambil 16 karakter awal saja, cukup aman & tidak terlalu panjang di URL

def cek_signature(kode_pelacakan: str, sig: str) -> bool:
    """Cek apakah signature yang diberikan cocok dengan kode_pelacakan."""
    sig_asli = buat_signature(kode_pelacakan)
    return hmac.compare_digest(sig_asli, sig)   # compare_digest supaya aman dari timing attack

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode('utf-8'), pin_hash.encode('utf-8'))

if not os.path.exists("uploads/ttd"):
    os.makedirs("uploads/ttd")

if not os.path.exists("uploads/dokumen"):
    os.makedirs("uploads/dokumen")

if not os.path.exists("uploads/final"):
    os.makedirs("uploads/final")

# Inisialisasi Aplikasi dan Template
app = FastAPI(title="Sistem Kinerja TTE")
templates = Jinja2Templates(directory="templates")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Fungsi Koneksi Database
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# SCHEMA PYDANTIC (Validasi JSON)
# ==========================================
class LoginRequest(BaseModel):
    nip: str
    password: str
class PosisiTTDItem(BaseModel):
    nip_penyetuju : str
    label_ttd     : str
    posisi_hal    : int
    posisi_x      : float
    posisi_y      : float

class SetPosisiTTDRequest(BaseModel):
    posisi_list  : List[PosisiTTDItem]
    mode_paralel : bool = False

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
    tipe_input  : str           = 'editor'
    file_upload : Optional[str] = None

# ==========================================
# FUNGSI INJECT TTD KE PDF
# ==========================================
def inject_ttd_ke_pdf(surat_id: int, db: Session) -> str | None:
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or not surat.file_upload:
        return None

    pdf_path = f"uploads/{surat.file_upload}"
    if not os.path.exists(pdf_path):
        return None

    doc = pymupdf.open(pdf_path)

    persetujuan_list = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id           == surat_id,
        models.PersetujuanSurat.status_persetujuan == "disetujui",
        models.PersetujuanSurat.posisi_x.isnot(None)
    ).order_by(models.PersetujuanSurat.urutan).all()

     # ✅ Untuk surat permohonan, hanya TTE Utama (Direktur) yang dicetak.
    # Paraf level di bawahnya hanya proses internal, tidak dicetak sebagai stempel QR.
    if surat.jenis_surat == "permohonan":
        persetujuan_list = [p for p in persetujuan_list if p.jenis_ttd == "TTE Utama"]

    for p in persetujuan_list:
        pegawai = db.query(models.Pegawai).filter(
            models.Pegawai.nip == p.nip_penyetuju
        ).first()
        unit = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == pegawai.kode_unit
        ).first() if pegawai else None

                # ── QR Code berisi URL verifikasi publik ──
        sig = buat_signature(surat.kode_pelacakan or "")
        qr_data = f"{BASE_URL}/verifikasi?kode={surat.kode_pelacakan}&sig={sig}"

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4, border=1
        )
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")

        # ── Tempel logo PPNP di tengah QR ──
        logo_path = "uploads/assets/logo.png"
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")

            ukuran_logo = int(qr_pil.size[0] * 0.22)
            logo = logo.resize((ukuran_logo, ukuran_logo))

            # Lingkaran putih di belakang logo (biar QR tetap terbaca jelas)
            from PIL import ImageDraw
            bg_size = ukuran_logo + 12
            bg = Image.new("RGBA", (bg_size, bg_size), (255, 255, 255, 0))
            draw = ImageDraw.Draw(bg)
            draw.ellipse((0, 0, bg_size, bg_size), fill=(255, 255, 255, 255))

            pos_bg   = ((qr_pil.size[0] - bg_size) // 2, (qr_pil.size[1] - bg_size) // 2)
            pos_logo = ((qr_pil.size[0] - ukuran_logo) // 2, (qr_pil.size[1] - ukuran_logo) // 2)

            qr_pil = qr_pil.convert("RGBA")
            qr_pil.paste(bg, pos_bg, bg)
            qr_pil.paste(logo, pos_logo, logo)
            qr_pil = qr_pil.convert("RGB")

        qr_bytes = io.BytesIO()
        qr_pil.save(qr_bytes, format="PNG")
        qr_bytes.seek(0)

        # ── Halaman & koordinat ──
        hal_index = max(0, min((p.posisi_hal or 1) - 1, len(doc) - 1))
        page      = doc[hal_index]
        pw, ph    = page.rect.width, page.rect.height

        x0 = (p.posisi_x / 100.0) * pw
        y0 = (p.posisi_y / 100.0) * ph

        BLOK_W  = 155
        QR_SIZE = 70

        # Label (contoh: "Mengetahui,")
        if p.label_ttd:
            page.insert_text(
                (x0, y0 + 10), p.label_ttd,
                fontsize=8, fontname="helv", color=(0, 0, 0)
            )

        # QR Code
        page.insert_image(
            pymupdf.Rect(x0, y0 + 14, x0 + QR_SIZE, y0 + 14 + QR_SIZE),
            stream=qr_bytes.read()
        )

        # Garis bawah
        y_garis = y0 + 14 + QR_SIZE + 6
        page.draw_line(
            pymupdf.Point(x0, y_garis),
            pymupdf.Point(x0 + BLOK_W, y_garis),
            color=(0, 0, 0), width=0.5
        )

        # Nama pejabat (bold)
        nama = (pegawai.nama if pegawai else "—")[:40]
        page.insert_text(
            (x0, y_garis + 11), nama,
            fontsize=8, fontname="hebo", color=(0, 0, 0)
        )

        # NIP
        page.insert_text(
            (x0, y_garis + 22), f"NIP. {p.nip_penyetuju}",
            fontsize=7.5, fontname="helv", color=(0, 0, 0)
        )

        # Tanggal & status SAH
        if p.tanggal_proses:
            bln = ["Jan","Feb","Mar","Apr","Mei","Jun",
                   "Jul","Agu","Sep","Okt","Nov","Des"]
            tgl = p.tanggal_proses
            tgl_str = f"{tgl.day} {bln[tgl.month-1]} {tgl.year} {tgl.hour:02d}:{tgl.minute:02d}"
            page.insert_text(
                (x0, y_garis + 33), f"SAH — TTE: {tgl_str}",
                fontsize=6.5, fontname="helv", color=(0.1, 0.5, 0.1)
            )

    # Overwrite file di lokasi yang sama (file sementara dulu, baru ditimpa)
    path_sementara = pdf_path + ".tmp"
    doc.save(path_sementara, garbage=4, deflate=True)
    doc.close()
    os.replace(path_sementara, pdf_path)

    return surat.file_upload

# ==========================================
# ROUTE HALAMAN HTML (FRONTEND)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def halaman_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/surat", response_class=HTMLResponse)
def halaman_manajemen_surat(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ==========================================
# ROUTE API (BACKEND)
# ==========================================
@app.post("/api/login")
def proses_login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.Pegawai).filter(models.Pegawai.nip == req.nip).first()
    if not user or user.password != req.password:
        raise HTTPException(status_code=401, detail="NIP atau Password salah!")
    return {
        "nip": user.nip,
        "nama": user.nama,
        "jabatan": user.jabatan,
        "role_ppnp": user.role_ppnp,
        "kode_unit": user.kode_unit
    }

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

    result = []
    for p in pejabat:
        result.append({
            "nip":       p.nip,
            "nama":      p.nama,
            "jabatan":   p.jabatan,
            "nama_unit": unit_info.nama_unit if unit_info else "-"
        })

    return result

# Pastikan folder upload tersedia
if not os.path.exists("uploads/surat"):
    os.makedirs("uploads/surat")

def buat_path_surat(jenis_surat: str, perihal: str, ekstensi: str) -> tuple[str, str]:
    now = datetime.utcnow()
    tahun  = now.strftime("%Y")
    bulan  = now.strftime("%m")
    tanggal_str = now.strftime("%Y%m%d")

    perihal_bersih = re.sub(r'[^a-zA-Z0-9]+', '_', (perihal or "surat").strip())[:50].strip('_')
    if not perihal_bersih:
        perihal_bersih = "surat"

    kode_acak = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))

    nama_file = f"{tanggal_str}_{perihal_bersih}_{kode_acak}.{ekstensi}"

    folder_relatif = f"surat/{jenis_surat}/{tahun}/{bulan}"
    folder_absolut = f"uploads/{folder_relatif}"   # ✅ tambahkan prefix "uploads/" di sini

    if not os.path.exists(folder_absolut):
        os.makedirs(folder_absolut, exist_ok=True)

    return folder_relatif, nama_file

def generate_kode():
    karakter = string.ascii_uppercase + string.digits
    acak = ''.join(random.choice(karakter) for _ in range(6))
    return f"PPNP-{acak}"

@app.post("/api/upload/surat")
def upload_file(
    file: UploadFile = File(...),
    nomor_surat: str = Form(default=""),
    asal_surat: str = Form(default=""),
    perihal: str = Form(default="")
):
    ekstensi = file.filename.split('.')[-1]
    folder_relatif, nama_file = buat_path_surat("eksternal", perihal, ekstensi)
    path_relatif_db = f"{folder_relatif}/{nama_file}"
    path_fisik       = f"uploads/{path_relatif_db}"

    with open(path_fisik, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)

    return {"nama_file": path_relatif_db}

@app.post("/api/surat")
def simpan_surat(req: SuratCreate, db: Session = Depends(get_db)):
    # ✅ Cegah nomor surat ganda
    if req.nomor_surat and req.nomor_surat.strip():
        sudah_ada = db.query(models.Surat).filter(
            models.Surat.nomor_surat == req.nomor_surat.strip()
        ).first()
        if sudah_ada:
            raise HTTPException(400, f"Nomor surat '{req.nomor_surat}' sudah digunakan pada surat lain!")

    kode_resi = generate_kode()
    nip_ttd   = req.nip_penandatangan if req.nip_penandatangan and req.nip_penandatangan.strip() != "" else None
    nip_tujuan = req.nip_tujuan if req.nip_tujuan and req.nip_tujuan.strip() != "" else None

    surat_baru = models.Surat(
        jenis_surat      = req.jenis_surat,
        nomor_surat      = req.nomor_surat,
        perihal          = req.perihal,
        asal_surat       = req.asal_surat,
        isi_surat        = req.isi_surat,
        file_surat       = req.file_surat,
        kode_pelacakan   = kode_resi,
        nip_pembuat      = req.nip_pembuat,
        nip_penandatangan = nip_ttd,
        nip_tujuan       = nip_tujuan,
        tujuan_tambahan  = req.tujuan_tambahan,
        tanggal_surat    = req.tanggal_surat,
        tanggal_diterima = req.tanggal_diterima,
        status           = "draft",
        # ✅ BARU
        tipe_input       = req.tipe_input  or "editor",
        file_upload      = req.file_upload,
    )
    db.add(surat_baru)
    db.commit()
    return {"message": "Surat berhasil disimpan", "kode_pelacakan": kode_resi}

# --- API UNTUK MENGAMBIL DAFTAR SURAT ---
@app.get("/api/surat")
def get_daftar_surat(
    nip   : str,
    status: str = "draft",
    db: Session = Depends(get_db)
):
    if status == "terkirim":
        daftar_surat = db.query(models.Surat).filter(
            models.Surat.nip_pembuat == nip,
            models.Surat.status.notin_(["draft"])
        ).order_by(models.Surat.id.desc()).all()
    else:
        daftar_surat = db.query(models.Surat).filter(
            models.Surat.nip_pembuat == nip,
            models.Surat.status      == status
        ).order_by(models.Surat.id.desc()).all()

    return daftar_surat

def format_tanggal_indonesia(dt) -> str:
    """Format datetime jadi teks Indonesia rapi, misal '16 September 2026, 12:29 WIB'."""
    if not dt:
        return "—"
    bulan = ["Januari","Februari","Maret","April","Mei","Juni",
             "Juli","Agustus","September","Oktober","November","Desember"]
    return f"{dt.day} {bulan[dt.month-1]} {dt.year}, {dt.hour:02d}:{dt.minute:02d} WIB"

@app.get("/verifikasi", response_class=HTMLResponse)
def halaman_verifikasi(request: Request, kode: str = None, sig: str = None, db: Session = Depends(get_db)):
    """Halaman publik untuk verifikasi keabsahan surat via QR Code."""

    konteks = {"request": request, "valid": False, "pesan": ""}

    if not kode or not sig:
        konteks["pesan"] = "Parameter tidak lengkap."
        return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)

    if not cek_signature(kode, sig):
        konteks["pesan"] = "Kode verifikasi tidak valid atau telah dimanipulasi."
        return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)

    surat = db.query(models.Surat).filter(
        models.Surat.kode_pelacakan == kode
    ).first()

    if not surat:
        konteks["pesan"] = "Surat dengan kode ini tidak ditemukan dalam sistem."
        return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)

    if surat.status != "selesai":
        konteks["pesan"] = "Surat ini belum selesai proses tanda tangan, sehingga belum sah."
        return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)

    persetujuan_final = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id           == surat.id,
        models.PersetujuanSurat.status_persetujuan == "disetujui",
        models.PersetujuanSurat.jenis_ttd           == "TTE Utama"
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
        "valid"           : True,
        "kode"            : surat.kode_pelacakan,
        "perihal"         : surat.perihal,
        "jenis_surat"     : surat.jenis_surat.upper(),
        "nomor_surat"     : surat.nomor_surat or "Belum ada nomor",
        "nama_pejabat"    : pejabat.nama if pejabat else "—",
        "jabatan"         : jabatan_final or "—",
        "unit_pejabat"    : unit_pejabat.nama_unit if unit_pejabat else "—",
        "tanggal_tte"     : format_tanggal_indonesia(persetujuan_final.tanggal_proses) if persetujuan_final else "—",
        "waktu_akses"     : format_tanggal_indonesia(datetime.utcnow()),
    })

    return templates.TemplateResponse(request=request, name="verifikasi.html", context=konteks)

@app.get("/api/kop")
def cek_kop(kode_unit: str):
    import re
    if not re.match(r'^[A-Za-z0-9\.]+$', kode_unit):
        raise HTTPException(status_code=400, detail="Kode unit tidak valid")

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

    nama_file = MAPPING_KOP.get(kode_unit)

    if not nama_file:
        return {"ada": False, "url": None}

    file_path = f"uploads/assets/{nama_file}"
    if os.path.exists(file_path):
        from urllib.parse import quote
        url_aman = f"/uploads/assets/{quote(nama_file)}"
        return {"ada": True, "url": url_aman}

    return {"ada": False, "url": None}

@app.post("/api/surat/{surat_id}/kirim")
def kirim_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa dikirim")

    pembuat = db.query(models.Pegawai).filter(
        models.Pegawai.nip == surat.nip_pembuat
    ).first()
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pembuat.kode_unit
    ).first() if pembuat else None

    punya_kop = unit.punya_kop_sendiri if unit else False
    chain     = []


        # ─────────────────────────────────────────
    # UPLOAD PDF → chain sudah diset via posisi-ttd
    # ─────────────────────────────────────────
    if surat.tipe_input == "upload":
        existing_chain = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id == surat.id
        ).all()
        if not existing_chain:
            raise HTTPException(
                400,
                "Belum ada penandatangan. Klik tombol 'Atur Posisi TTD' terlebih dahulu."
            )
        surat.status = "menunggu"
        db.commit()
        return {
            "message"       : "Surat berhasil dikirim untuk ditandatangani",
            "chain_approval": [
                {"urutan": p.urutan, "nip": p.nip_penyetuju, "jenis": p.jenis_ttd}
                for p in existing_chain
            ]
        }

    # ─────────────────────────────────────────
    # EKSTERNAL → inbox Direktur, tanpa TTE
    # ─────────────────────────────────────────
    if surat.jenis_surat == "eksternal":
        surat.status = "masuk"
        db.commit()
        return {"message": "Surat masuk berhasil diteruskan ke Direktur"}

    # ─────────────────────────────────────────
    # INTERNAL → pimpinan unit TTE, langsung
    # ─────────────────────────────────────────
    elif surat.jenis_surat == "internal":
        if not surat.nip_penandatangan:
            raise HTTPException(400, "Penandatangan surat internal tidak ditemukan")
        chain.append((surat.nip_penandatangan, "TTE Utama", 1))

    # ─────────────────────────────────────────
    # PERMOHONAN → tergantung kop surat
    # ─────────────────────────────────────────
    elif surat.jenis_surat == "permohonan":
        if punya_kop:
            direktur_unit = db.query(models.UnitOrganisasi).filter(
                models.UnitOrganisasi.level == 1
            ).first()

            if not direktur_unit or not direktur_unit.nip_pimpinan:
                raise HTTPException(400, "Data Direktur tidak ditemukan di database")

            chain.append((direktur_unit.nip_pimpinan, "TTE Utama", 1))

        else:
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
                    jenis = "TTE Utama" if unit_skrg.level == 1 else "Paraf"
                    chain.append((pejabat.nip, jenis, urutan))
                    urutan += 1

                kode_skrg = unit_skrg.kode_induk

            if not chain:
                raise HTTPException(400, "Rantai persetujuan tidak bisa ditentukan")

            chain = [(n, j, i+1) for i, (n, j, _) in enumerate(chain)]

    # Buat record persetujuan
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

@app.delete("/api/surat/{surat_id}")
def hapus_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa dihapus")

    # ✅ Hapus file fisik terkait (scan eksternal, PDF upload, PDF final), kalau ada
    for nama_file in [surat.file_surat, surat.file_upload, surat.file_final]:
        if nama_file:
            path_fisik = f"uploads/{nama_file}"
            if os.path.exists(path_fisik):
                try:
                    os.remove(path_fisik)
                except Exception as e:
                    print(f"⚠️ Gagal hapus file {path_fisik}: {e}")

    db.delete(surat)
    db.commit()
    return {"message": "Draft berhasil dihapus"}

@app.put("/api/surat/{surat_id}")
def update_surat(surat_id: int, req: SuratCreate, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa diedit")

    # ✅ Cegah nomor surat ganda (kecuali dengan dirinya sendiri saat edit)
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
    if req.file_surat:
        surat.file_surat = req.file_surat
    # ✅ BARU
    if req.file_upload:
        surat.file_upload = req.file_upload
    surat.tipe_input = req.tipe_input or surat.tipe_input

    db.commit()
    return {"message": "Draft berhasil diperbarui"}

@app.get("/api/unit-info")
def get_unit_info(kode_unit: str, db: Session = Depends(get_db)):
    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    if not unit:
        raise HTTPException(404, "Unit tidak ditemukan")

    return {
        "kode_unit"  : unit.kode_unit,
        "nama_unit"  : unit.nama_unit,
        "level"      : unit.level,
        "kode_induk" : unit.kode_induk,
        "nama_induk" : unit.nama_induk,
        "punya_kop_sendiri" : unit.punya_kop_sendiri,
        "jabatan_surat"     : unit.jabatan_surat,
    }

@app.get("/api/rantai-unit")
def get_rantai_unit(kode_unit: str, db: Session = Depends(get_db)):
    """
    Ambil daftar unit mulai dari kode_unit yang diberikan, mendaki ke atas
    lewat kode_induk, sampai level 1 (Direktur). Dipakai untuk menentukan
    rantai paraf/TTE surat permohonan (upload PDF), meniru logika di kirim_surat().
    """
    unit_awal = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    if not unit_awal:
        raise HTTPException(404, "Unit tidak ditemukan")

    hasil = []

    # ── Jika unit punya kop sendiri: langsung ke Direktur saja, tanpa paraf ──
    if unit_awal.punya_kop_sendiri:
        direktur_unit = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.level == 1
        ).first()
        if direktur_unit:
            hasil.append({
                "kode_unit": direktur_unit.kode_unit,
                "nama_unit": direktur_unit.nama_unit,
                "level"    : direktur_unit.level,
            })
        return hasil

    # ── Jika tidak punya kop sendiri: mendaki dari unit sendiri sampai level 1 ──
    kode_skrg = kode_unit
    while kode_skrg:
        unit_skrg = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == kode_skrg
        ).first()
        if not unit_skrg:
            break

        hasil.append({
            "kode_unit": unit_skrg.kode_unit,
            "nama_unit": unit_skrg.nama_unit,
            "level"    : unit_skrg.level,
        })

        kode_skrg = unit_skrg.kode_induk

    return hasil

@app.get("/api/unit-tujuan")
def get_unit_tujuan(kode_unit: str, db: Session = Depends(get_db)):
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

# ─────────────────────────────────────────────
# PROFIL & TTE
# ─────────────────────────────────────────────

@app.get("/profil", response_class=HTMLResponse)
def halaman_profil(request: Request):
    return templates.TemplateResponse(request=request, name="profil.html")


@app.get("/api/profil")
def get_profil(nip: str, db: Session = Depends(get_db)):
    pegawai = db.query(models.Pegawai).filter(
        models.Pegawai.nip == nip
    ).first()
    if not pegawai:
        raise HTTPException(status_code=404, detail="Pegawai tidak ditemukan")

    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()

    return {
        "nip"       : pegawai.nip,
        "nama"      : pegawai.nama,
        "jabatan"   : pegawai.jabatan,
        "role_ppnp" : pegawai.role_ppnp,
        "kode_unit" : pegawai.kode_unit,
        "nama_unit" : unit.nama_unit if unit else "—",
        "tte_aktif" : pegawai.tte_aktif,
        "file_ttd"  : pegawai.file_ttd,
    }


@app.post("/api/upload/ttd")
def upload_ttd(
    file: UploadFile = File(...),
    nip:  str        = Form(...),
    db: Session       = Depends(get_db)
):
    ext = file.filename.split('.')[-1].lower()
    if ext not in ['png', 'jpg', 'jpeg']:
        raise HTTPException(
            status_code=400,
            detail="Format file harus PNG atau JPG"
        )

    pegawai = db.query(models.Pegawai).filter(models.Pegawai.nip == nip).first()
    nama_bersih = re.sub(r'[^a-zA-Z0-9]+', '_', pegawai.nama.strip()) if pegawai else "unknown"
    nip_bersih  = re.sub(r'[^a-zA-Z0-9]', '', nip)

    nama_file   = f"ttd_{nama_bersih}_{nip_bersih}.{ext}"
    file_path   = f"uploads/ttd/{nama_file}"

    with open(file_path, "wb+") as f:
        shutil.copyfileobj(file.file, f)

    return {"nama_file": nama_file, "url": f"/uploads/ttd/{nama_file}"}


@app.post("/api/registrasi-ttd")
def registrasi_ttd(
    nip      : str = Form(...),
    pin      : str = Form(...),
    file_ttd : str = Form(...),
    db: Session = Depends(get_db)
):
    if not pin.isdigit() or len(pin) != 6:
        raise HTTPException(
            status_code=400,
            detail="PIN harus 6 digit angka"
        )

    pegawai = db.query(models.Pegawai).filter(
        models.Pegawai.nip == nip
    ).first()
    if not pegawai:
        raise HTTPException(status_code=404, detail="Pegawai tidak ditemukan")

    pegawai.pin_tte = hash_pin(pin)
    pegawai.file_ttd  = file_ttd
    pegawai.tte_aktif = True

    db.commit()
    return {"message": "Registrasi TTD berhasil! TTE Anda sudah aktif."}


@app.post("/api/ganti-pin")
def ganti_pin(
    nip      : str = Form(...),
    pin_lama : str = Form(...),
    pin_baru : str = Form(...),
    db: Session   = Depends(get_db)
):
    pegawai = db.query(models.Pegawai).filter(
        models.Pegawai.nip == nip
    ).first()
    if not pegawai or not pegawai.pin_tte:
        raise HTTPException(status_code=404, detail="TTE belum terdaftar")

    if not verify_pin(pin_lama, pegawai.pin_tte):
        raise HTTPException(status_code=401, detail="PIN lama tidak sesuai")

    if not pin_baru.isdigit() or len(pin_baru) != 6:
        raise HTTPException(status_code=400, detail="PIN baru harus 6 digit angka")

    pegawai.pin_tte = hash_pin(pin_baru)
    db.commit()
    return {"message": "PIN berhasil diubah"}

@app.get("/inbox", response_class=HTMLResponse)
def halaman_inbox(request: Request):
    return templates.TemplateResponse(request=request, name="inbox.html")

@app.get("/api/inbox")
def get_inbox(nip: str, db: Session = Depends(get_db)):
    """Ambil semua surat yang menunggu tanda tangan pejabat ini"""

    persetujuan_list = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.nip_penyetuju      == nip,
        models.PersetujuanSurat.status_persetujuan == "menunggu"
    ).order_by(models.PersetujuanSurat.id.desc()).all()

    result = []
    for p in persetujuan_list:
        surat = db.query(models.Surat).filter(
            models.Surat.id == p.surat_id
        ).first()
        if not surat:
            continue

        # ✅ Cegah surat yang masih draft muncul di inbox
        # (khusus tipe upload, PersetujuanSurat dibuat lebih awal saat Simpan Posisi,
        # jadi kita perlu pastikan surat sudah benar-benar dikirim)
        if surat.status == "draft":
            continue

        # Cek apakah giliran pejabat ini (urutan terkecil yang masih menunggu)
                # ✅ Mode paralel: semua penandatangan bisa TTD bersamaan
        if p.mode_paralel:
            pass  # selalu tampilkan, tidak perlu cek urutan
        else:
            # Mode sequential: hanya tampilkan jika giliran pejabat ini
            urutan_aktif = db.query(models.PersetujuanSurat).filter(
                models.PersetujuanSurat.surat_id           == surat.id,
                models.PersetujuanSurat.status_persetujuan == "menunggu"
            ).order_by(models.PersetujuanSurat.urutan).first()

            if not urutan_aktif or urutan_aktif.nip_penyetuju != nip:
                continue

        pembuat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_pembuat
        ).first()

        # ✅ TAMBAH — ambil info tahap paraf/TTE SEBELUMNYA (kalau ada).
        # Ini dipakai untuk ditampilkan sebagai catatan di tabel Inbox
        # ("Menunggu TTE/Paraf"), BUKAN untuk dicetak di badan surat —
        # supaya surat resmi tetap bersih dari jejak proses administratif.
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
            "nomor_surat"     : surat.nomor_surat,
            "perihal"         : surat.perihal,
            "jenis_ttd"       : p.jenis_ttd,
            "urutan"          : p.urutan,
            "tanggal_dibuat"  : str(surat.tanggal_dibuat),
            "nama_pembuat"    : pembuat.nama    if pembuat else "—",
            "jabatan_pembuat" : pembuat.jabatan if pembuat else "—",
            "paraf_sebelumnya": paraf_sebelumnya,   # ✅ info tahap sebelumnya untuk tabel inbox
        })

    return result


@app.post("/api/tanda-tangan/{persetujuan_id}")
def proses_tanda_tangan(
    persetujuan_id: int,
    nip          : str = Form(...),
    pin          : str = Form(...),
    catatan      : str = Form(default=""),
    nomor_surat  : str = Form(default=""),
    db: Session        = Depends(get_db)
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
    if not pegawai.tte_aktif or not pegawai.pin_tte:
        raise HTTPException(400, "TTE belum diregistrasi. Daftar di halaman Profil dulu.")
    if not verify_pin(pin, pegawai.pin_tte):
        raise HTTPException(401, "PIN salah!")

    persetujuan.status_persetujuan = "disetujui"
    persetujuan.catatan            = catatan
    persetujuan.tanggal_proses     = datetime.utcnow()

    surat = db.query(models.Surat).filter(
        models.Surat.id == persetujuan.surat_id
    ).first()

    # Jika permohonan & ini TTE Utama → simpan nomor surat dari Direktur
    # Jika permohonan & ini TTE Utama → simpan nomor surat dari Direktur
    if (surat.jenis_surat == "permohonan"
            and persetujuan.jenis_ttd == "TTE Utama"
            and nomor_surat.strip()):

        # ✅ Cegah nomor surat ganda
        sudah_ada = db.query(models.Surat).filter(
            models.Surat.nomor_surat == nomor_surat.strip(),
            models.Surat.id != surat.id
        ).first()
        if sudah_ada:
            raise HTTPException(400, f"Nomor surat '{nomor_surat.strip()}' sudah digunakan pada surat lain!")

        surat.nomor_surat = nomor_surat.strip()

        # ── Cek apakah masih ada yang menunggu ──
    if persetujuan.mode_paralel:
        # Mode paralel: selesai jika SEMUA sudah disetujui
        masih_menunggu = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "menunggu"
        ).first()
        ada_berikut = masih_menunggu is not None
    else:
        # Mode sequential: cek urutan berikutnya
        persetujuan_berikut = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "menunggu",
            models.PersetujuanSurat.urutan             >  persetujuan.urutan
        ).order_by(models.PersetujuanSurat.urutan).first()
        ada_berikut = persetujuan_berikut is not None

    if ada_berikut:
        db.commit()
        return {
            "message"  : f"{persetujuan.jenis_ttd} berhasil! Menunggu tanda tangan lainnya.",
            "status"   : "berlanjut",
            "jenis_ttd": persetujuan.jenis_ttd
        }
    else:
        surat.status = "selesai"

        # ✅ Inject TTD ke PDF jika tipe upload
        if surat.tipe_input == "upload":
            db.commit()  # commit dulu agar data TTD tersimpan
            nama_final = inject_ttd_ke_pdf(surat.id, db)
            if nama_final:
                surat = db.query(models.Surat).filter(
                    models.Surat.id == surat.id
                ).first()
                surat.file_final = nama_final
                surat.status     = "selesai"

        db.commit()
        return {
            "message"  : "Semua tanda tangan selesai! Surat telah final.",
            "status"   : "selesai",
            "jenis_ttd": persetujuan.jenis_ttd
        }

@app.get("/api/surat/{surat_id}")
def get_detail_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(status_code=404, detail="Surat tidak ditemukan")

    pembuat = db.query(models.Pegawai).filter(
        models.Pegawai.nip == surat.nip_pembuat
    ).first()

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

    # ✅ Ambil jabatan formal untuk surat (dari unit_organisasi.jabatan_surat)
    jabatan_penandatangan_final = None
    if penandatangan:
        unit_penandatangan = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == penandatangan.kode_unit
        ).first()
        jabatan_penandatangan_final = (
            unit_penandatangan.jabatan_surat
            if unit_penandatangan and unit_penandatangan.jabatan_surat
            else penandatangan.jabatan   # fallback ke jabatan lama kalau belum di-mapping
        )

    tujuan = None
    if surat.nip_tujuan:
        tujuan = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_tujuan
        ).first()

    # ✅ Ambil jabatan formal untuk surat (dari unit_organisasi.jabatan_surat)
    jabatan_tujuan_final = None
    if tujuan:
        unit_tujuan = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == tujuan.kode_unit
        ).first()
        jabatan_tujuan_final = (
            unit_tujuan.jabatan_surat
            if unit_tujuan and unit_tujuan.jabatan_surat
            else tujuan.jabatan   # fallback ke jabatan lama kalau belum di-mapping
        )

    # Chain persetujuan tetap dikirim — dipakai untuk fitur Tracking (bukan untuk
    # dicetak di badan surat). Lihat catatan di previewSuratInbox() pada inbox.html.
    chain_persetujuan = []
    persetujuan_list  = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat.id
    ).order_by(models.PersetujuanSurat.urutan).all()

    for p in persetujuan_list:
        pejabat_p = db.query(models.Pegawai).filter(
            models.Pegawai.nip == p.nip_penyetuju
        ).first()
        chain_persetujuan.append({
            "urutan"         : p.urutan,
            "jenis_ttd"      : p.jenis_ttd,
            "status"         : p.status_persetujuan,
            "nama_pejabat"   : pejabat_p.nama     if pejabat_p else "—",
            "jabatan_pejabat": pejabat_p.jabatan  if pejabat_p else "—",
            "tanggal_proses" : str(p.tanggal_proses) if p.tanggal_proses else None,
        })

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
            tanggal_tte = str(persetujuan_final.tanggal_proses) \
                          if persetujuan_final.tanggal_proses else None
            pejabat_ttd = db.query(models.Pegawai).filter(
                models.Pegawai.nip == persetujuan_final.nip_penyetuju
            ).first()
            if pejabat_ttd and pejabat_ttd.file_ttd:
                file_ttd_penandatangan = pejabat_ttd.file_ttd

    kode_unit_kop = "D001" if surat.jenis_surat == "permohonan" \
                    else (pembuat.kode_unit if pembuat else None)

    return {
        "id"                     : surat.id,
        "jenis_surat"            : surat.jenis_surat,
        "nomor_surat"            : surat.nomor_surat,
        "perihal"                : surat.perihal,
        "asal_surat"             : surat.asal_surat,
        "isi_surat"              : surat.isi_surat,
        "file_surat"             : surat.file_surat,
        "kode_pelacakan"         : surat.kode_pelacakan,
        "status"                 : surat.status,
        "tanggal_dibuat"         : str(surat.tanggal_dibuat),
        "tanggal_surat"          : surat.tanggal_surat,
        "tanggal_diterima"       : surat.tanggal_diterima,
        "nama_pembuat"           : pembuat.nama      if pembuat      else "—",
        "jabatan_pembuat"        : pembuat.jabatan   if pembuat      else "—",
        "kode_unit_pembuat"      : pembuat.kode_unit if pembuat      else None,
        "kode_unit_kop"          : kode_unit_kop,
        "nama_penandatangan"     : penandatangan.nama    if penandatangan else None,
        "jabatan_penandatangan"  : jabatan_penandatangan_final if penandatangan else None,
        "nip_penandatangan"      : penandatangan.nip     if penandatangan else None,
        "nama_tujuan"            : tujuan.nama    if tujuan else None,
        "jabatan_tujuan"         : jabatan_tujuan_final if tujuan else None,
        "tujuan_tambahan"        : surat.tujuan_tambahan,
        "tte_selesai"            : tte_selesai,
        "file_ttd_penandatangan" : file_ttd_penandatangan,
        "tanggal_tte"            : tanggal_tte,
        "chain_persetujuan"      : chain_persetujuan,
                # ✅ BARU — tambahkan sebelum baris penutup }
        "tipe_input"  : surat.tipe_input,
        "file_upload" : surat.file_upload,
        "file_final"  : surat.file_final,
                # ✅ BARU — untuk QR Code verifikasi
        "url_verifikasi": (
            f"{surat.kode_pelacakan}|{buat_signature(surat.kode_pelacakan)}"
            if surat.kode_pelacakan else None
        ),
    }

@app.get("/api/surat/{surat_id}/tracking")
def get_tracking_surat(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(
        models.Surat.id == surat_id
    ).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")

    pembuat = db.query(models.Pegawai).filter(
        models.Pegawai.nip == surat.nip_pembuat
    ).first()

    riwayat = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat_id
    ).order_by(models.PersetujuanSurat.urutan).all()

    chain = []
    for r in riwayat:
        pejabat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == r.nip_penyetuju
        ).first()
        chain.append({
            "urutan"          : r.urutan,
            "jenis_ttd"       : r.jenis_ttd,
            "status"          : r.status_persetujuan,
            "nama_pejabat"    : pejabat.nama    if pejabat else "—",
            "jabatan_pejabat" : pejabat.jabatan if pejabat else "—",
            "catatan"         : r.catatan,
            "tanggal_proses"  : str(r.tanggal_proses) if r.tanggal_proses else None,
        })

    tujuan = None
    if surat.nip_tujuan:
        p = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_tujuan
        ).first()
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


@app.get("/api/surat-masuk")
def get_surat_masuk(nip: str, db: Session = Depends(get_db)):
    """
    Ambil surat yang DITERIMA oleh user ini:
    - Internal  : nip_tujuan == nip & status selesai
    - Eksternal : Direktur (level 1) & status masuk
    - Permohonan: nip_pembuat == nip & status selesai
    """
    pegawai = db.query(models.Pegawai).filter(
        models.Pegawai.nip == nip
    ).first()
    if not pegawai:
        return []

    unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == pegawai.kode_unit
    ).first()

    result = []

    # ─────────────────────────────────────────
    # 1. SURAT INTERNAL — pimpinan unit tujuan
    # ─────────────────────────────────────────
    surat_internal = db.query(models.Surat).filter(
        models.Surat.nip_tujuan  == nip,
        models.Surat.jenis_surat == "internal",
        models.Surat.status      == "selesai"
    ).order_by(models.Surat.id.desc()).all()

    for s in surat_internal:
        pembuat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == s.nip_pembuat
        ).first()
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
            "kategori"        : "diterima"
        })

    # ─────────────────────────────────────────
    # 2. SURAT EKSTERNAL — khusus Direktur
    # ─────────────────────────────────────────
    if unit and unit.level == 1:
        surat_eksternal = db.query(models.Surat).filter(
            models.Surat.jenis_surat == "eksternal",
            models.Surat.status      == "masuk"
        ).order_by(models.Surat.id.desc()).all()

        for s in surat_eksternal:
            pembuat = db.query(models.Pegawai).filter(
                models.Pegawai.nip == s.nip_pembuat
            ).first()
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

    # ─────────────────────────────────────────
    # 3. PERMOHONAN SELESAI — kembali ke pembuat
    # ─────────────────────────────────────────
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

    result.sort(key=lambda x: x['tanggal_dibuat'], reverse=True)
    return result

@app.get("/api/riwayat-ttd")
def get_riwayat_ttd(nip: str, db: Session = Depends(get_db)):
    riwayat = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.nip_penyetuju         == nip,
        models.PersetujuanSurat.status_persetujuan.in_(["disetujui", "ditolak"])
    ).order_by(models.PersetujuanSurat.tanggal_proses.desc()).all()

    result = []
    for r in riwayat:
        surat = db.query(models.Surat).filter(
            models.Surat.id == r.surat_id
        ).first()
        if not surat:
            continue
        pembuat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_pembuat
        ).first()
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

# ==========================================
# ENDPOINT DOKUMEN UPLOAD (PDF)
# ==========================================
@app.post("/api/upload/dokumen")
def upload_dokumen(
    file: UploadFile = File(...),
    jenis_surat: str = Form(...),
    perihal: str = Form(default="")
):
    ext = file.filename.split('.')[-1].lower()
    if ext != 'pdf':
        raise HTTPException(400, "Hanya file PDF yang diizinkan")

    folder_relatif, nama_file = buat_path_surat(jenis_surat, perihal, ext)
    path_relatif_db = f"{folder_relatif}/{nama_file}"    # disimpan ke database (tanpa "uploads/")
    path_fisik       = f"uploads/{path_relatif_db}"        # dipakai untuk tulis file fisik

    with open(path_fisik, "wb+") as f:
        shutil.copyfileobj(file.file, f)

    return {"nama_file": path_relatif_db}

@app.get("/api/preview-pdf/{surat_id}")
def preview_pdf_url(surat_id: int, db: Session = Depends(get_db)):
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat or not surat.file_upload:
        raise HTTPException(404, "File PDF tidak ditemukan")
    return {"url": f"/uploads/{surat.file_upload}"}


@app.post("/api/surat/{surat_id}/posisi-ttd")
def set_posisi_ttd(
    surat_id : int,
    req      : SetPosisiTTDRequest,
    db       : Session = Depends(get_db)
):
    """Simpan posisi TTD dan daftar penandatangan untuk surat upload"""
    surat = db.query(models.Surat).filter(models.Surat.id == surat_id).first()
    if not surat:
        raise HTTPException(404, "Surat tidak ditemukan")
    if surat.status != "draft":
        raise HTTPException(400, "Hanya draft yang bisa diatur posisi TTD")
    if surat.tipe_input != "upload":
        raise HTTPException(400, "Endpoint ini hanya untuk surat tipe upload")
    if not req.posisi_list:
        raise HTTPException(400, "Minimal 1 posisi TTD harus diisi")

    # Hapus posisi lama (jika user edit ulang)
    db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id == surat_id
    ).delete()

        # ─────────────────────────────────────────────────────────
    # PERMOHONAN: hanya 1 pinned (Direktur / TTE Utama).
    # Rantai paraf level di bawahnya dibuat otomatis TANPA posisi,
    # meniru logika kirim_surat() pada surat editor.
    # ─────────────────────────────────────────────────────────
    if surat.jenis_surat == "permohonan":
        if len(req.posisi_list) != 1:
            raise HTTPException(
                400,
                "Surat permohonan hanya boleh memiliki 1 posisi TTD (Direktur). "
                "Paraf pejabat di bawahnya diproses otomatis oleh sistem."
            )

        pembuat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_pembuat
        ).first()
        unit_pembuat = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == pembuat.kode_unit
        ).first() if pembuat else None
        punya_kop = unit_pembuat.punya_kop_sendiri if unit_pembuat else False

        urutan = 1

        # Kalau unit TIDAK punya kop sendiri -> perlu paraf berjenjang dulu
        if not punya_kop and pembuat:
            kode_skrg = pembuat.kode_unit
            while kode_skrg:
                unit_skrg = db.query(models.UnitOrganisasi).filter(
                    models.UnitOrganisasi.kode_unit == kode_skrg
                ).first()
                if not unit_skrg:
                    break

                # Level 1 (Direktur) dilewati — itu jatah pinned TTE Utama
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
                            jenis_ttd          = "Paraf",
                            urutan             = urutan,
                            status_persetujuan = "menunggu",
                            mode_paralel       = False,   # hirarki selalu berurutan
                            # posisi sengaja dikosongkan -> tidak dicetak di PDF
                        ))
                        urutan += 1

                kode_skrg = unit_skrg.kode_induk

        # Pinned Direktur sebagai TTE Utama (satu-satunya yang dicetak di PDF)
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
        return {
            "message": f"Posisi TTD Direktur disimpan. {urutan - 1} paraf berjenjang dibuat otomatis."
        }

        # Simpan posisi baru
    total_posisi = len(req.posisi_list)
    for i, pos in enumerate(req.posisi_list):
        is_terakhir = (i == total_posisi - 1)
        db.add(models.PersetujuanSurat(
            surat_id           = surat_id,
            nip_penyetuju      = pos.nip_penyetuju,
            jenis_ttd          = "TTE Utama" if is_terakhir else "Paraf",
            urutan             = i + 1,
            status_persetujuan = "menunggu",
            mode_paralel       = req.mode_paralel,
            posisi_hal         = pos.posisi_hal,
            posisi_x           = pos.posisi_x,
            posisi_y           = pos.posisi_y,
            label_ttd          = pos.label_ttd,
        ))

    db.commit()
    return {"message": f"{len(req.posisi_list)} posisi TTD berhasil disimpan"}

@app.get("/api/download/{surat_id}")
def download_pdf_final(surat_id: int, db: Session = Depends(get_db)):
    """Download PDF final yang sudah diinjeksi TTD"""
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