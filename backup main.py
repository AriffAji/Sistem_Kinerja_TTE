from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles  # ✅ TAMBAHKAN INI
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models
import os
import shutil
import string   # ✅ TAMBAHKAN INI
import random   # ✅ TAMBAHKAN INI
from typing import Optional
import bcrypt
from datetime import datetime

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode('utf-8'), pin_hash.encode('utf-8'))

if not os.path.exists("uploads/ttd"):
    os.makedirs("uploads/ttd")

# Inisialisasi Aplikasi dan Template
app = FastAPI(title="Sistem Kinerja TTE")
templates = Jinja2Templates(directory="templates")

# ✅ TAMBAHKAN INI — agar file di folder uploads/ bisa diakses via URL
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
    nip_tujuan: Optional[str] = None        # ✅ TAMBAH
    tujuan_tambahan: Optional[str] = None   # ✅ TAMBAH


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

    # ✅ Cukup ambil pejabat di unit yang SAMA dengan user
    pejabat = db.query(models.Pegawai).filter(
        models.Pegawai.kode_unit == kode_unit,
        models.Pegawai.role_ppnp != "staf"
    ).all()

    # Ambil juga info unit untuk nama unit
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
    import re
    import shutil
    
    def bersihkan_nama(teks):
        return re.sub(r'[^a-zA-Z0-9_\-]', '_', teks.strip())
    
    ekstensi = file.filename.split('.')[-1]
    
    # Menyusun nama file baru dari form yang terisi
    komponen = []
    if nomor_surat: komponen.append(bersihkan_nama(nomor_surat))
    if asal_surat: komponen.append(bersihkan_nama(asal_surat))
    if perihal: komponen.append(bersihkan_nama(perihal))
    
    nama_baru = "_".join(komponen) + f".{ekstensi}" if komponen else file.filename
    
    file_location = f"uploads/surat/{nama_baru}"
    with open(file_location, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)
        
    return {"nama_file": nama_baru}

@app.post("/api/surat")
def simpan_surat(req: SuratCreate, db: Session = Depends(get_db)):
    kode_resi = generate_kode()
    nip_ttd   = req.nip_penandatangan if req.nip_penandatangan and req.nip_penandatangan.strip() != "" else None
    nip_tujuan = req.nip_tujuan if req.nip_tujuan and req.nip_tujuan.strip() != "" else None  # ✅

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
        nip_tujuan       = nip_tujuan,           # ✅
        tujuan_tambahan  = req.tujuan_tambahan,  # ✅
        tanggal_surat    = req.tanggal_surat,
        tanggal_diterima = req.tanggal_diterima,
        status           = "draft"
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
    # ✅ Jika status = "terkirim" → ambil semua status kecuali draft
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

@app.get("/api/kop")
def cek_kop(kode_unit: str):
    import re
    if not re.match(r'^[A-Za-z0-9\.]+$', kode_unit):
        raise HTTPException(status_code=400, detail="Kode unit tidak valid")

    # ✅ Mapping kode unit → nama file kop surat yang sebenarnya
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
        # Encode nama file agar URL aman (handle spasi)
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
            # ✅ BUKAN ke pimpinan unit sendiri — tapi ke DIREKTUR (level 1)
            direktur_unit = db.query(models.UnitOrganisasi).filter(
                models.UnitOrganisasi.level == 1
            ).first()

            if not direktur_unit or not direktur_unit.nip_pimpinan:
                raise HTTPException(400, "Data Direktur tidak ditemukan di database")

            chain.append((direktur_unit.nip_pimpinan, "TTE Utama", 1))

        else:
            # Unit non-kop → traverse hierarki ke atas sampai Direktur
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

@app.get("/api/unit-tujuan")
def get_unit_tujuan(kode_unit: str, db: Session = Depends(get_db)):
    # Cek apakah unit pengirim punya kop sendiri
    unit_pengirim = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.kode_unit == kode_unit
    ).first()
    punya_kop = unit_pengirim.punya_kop_sendiri if unit_pengirim else False

    # Ambil semua unit yang punya pimpinan
    semua_unit = db.query(models.UnitOrganisasi).filter(
        models.UnitOrganisasi.nip_pimpinan.isnot(None),
        models.UnitOrganisasi.kode_unit != kode_unit  # Kecualikan unit sendiri
    ).order_by(models.UnitOrganisasi.level).all()

    result = []
    for u in semua_unit:
        # Unit TIDAK punya kop → tidak boleh kirim ke level 1 (Direktur) & level 2 (Wadir)
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
    nip:  str        = Form(...)
):
    import re
    # Validasi tipe file
    ext = file.filename.split('.')[-1].lower()
    if ext not in ['png', 'jpg', 'jpeg']:
        raise HTTPException(
            status_code=400,
            detail="Format file harus PNG atau JPG"
        )

    # Nama file pakai NIP agar unik per ASN
    nip_bersih  = re.sub(r'[^a-zA-Z0-9]', '', nip)
    nama_file   = f"ttd_{nip_bersih}.{ext}"
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
    # Validasi PIN 6 digit angka
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

    # Hash PIN dengan bcrypt
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

    # ✅ Verifikasi PIN lama
    if not verify_pin(pin_lama, pegawai.pin_tte):
        raise HTTPException(status_code=401, detail="PIN lama tidak sesuai")

    # Validasi PIN baru
    if not pin_baru.isdigit() or len(pin_baru) != 6:
        raise HTTPException(status_code=400, detail="PIN baru harus 6 digit angka")

    # ✅ Hash PIN baru
    pegawai.pin_tte = hash_pin(pin_baru)
    db.commit()
    return {"message": "PIN berhasil diubah"}

@app.get("/inbox", response_class=HTMLResponse)
def halaman_inbox(request: Request):
    return templates.TemplateResponse(request=request, name="inbox.html")

@app.get("/api/inbox")
def get_inbox(nip: str, db: Session = Depends(get_db)):
    """Ambil semua surat yang menunggu tanda tangan pejabat ini"""

    # Cari persetujuan yang menunggu untuk NIP ini
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

        # Cek apakah giliran pejabat ini (urutan terkecil yang masih menunggu)
        urutan_aktif = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id           == surat.id,
            models.PersetujuanSurat.status_persetujuan == "menunggu"
        ).order_by(models.PersetujuanSurat.urutan).first()

        # Hanya tampilkan jika memang giliran pejabat ini
        if not urutan_aktif or urutan_aktif.nip_penyetuju != nip:
            continue

        pembuat = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_pembuat
        ).first()

        result.append({
            "persetujuan_id" : p.id,
            "surat_id"       : surat.id,
            "jenis_surat"    : surat.jenis_surat,
            "nomor_surat"    : surat.nomor_surat,
            "perihal"        : surat.perihal,
            "jenis_ttd"      : p.jenis_ttd,
            "urutan"         : p.urutan,
            "tanggal_dibuat" : str(surat.tanggal_dibuat),
            "nama_pembuat"   : pembuat.nama    if pembuat else "—",
            "jabatan_pembuat": pembuat.jabatan if pembuat else "—",
        })

    return result


@app.post("/api/tanda-tangan/{persetujuan_id}")
def proses_tanda_tangan(
    persetujuan_id: int,
    nip          : str = Form(...),
    pin          : str = Form(...),
    catatan      : str = Form(default=""),
    nomor_surat  : str = Form(default=""),   # ✅ TAMBAH
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

    # ✅ Jika permohonan & ini TTE Utama → simpan nomor surat dari Direktur
    if (surat.jenis_surat == "permohonan"
            and persetujuan.jenis_ttd == "TTE Utama"
            and nomor_surat.strip()):
        surat.nomor_surat = nomor_surat.strip()

    persetujuan_berikut = db.query(models.PersetujuanSurat).filter(
        models.PersetujuanSurat.surat_id           == surat.id,
        models.PersetujuanSurat.status_persetujuan == "menunggu",
        models.PersetujuanSurat.urutan             >  persetujuan.urutan
    ).order_by(models.PersetujuanSurat.urutan).first()

    if persetujuan_berikut:
        db.commit()
        return {
            "message"  : f"{persetujuan.jenis_ttd} berhasil! Diteruskan ke level berikutnya.",
            "status"   : "berlanjut",
            "jenis_ttd": persetujuan.jenis_ttd
        }
    else:
        surat.status = "selesai"
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

    # Ambil penandatangan dari nip_penandatangan
    penandatangan = None
    if surat.nip_penandatangan:
        penandatangan = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_penandatangan
        ).first()

    # ✅ Untuk permohonan: penandatangan diambil dari TTE Utama di PersetujuanSurat
    # karena nip_penandatangan tidak diisi saat buat draft permohonan
    if penandatangan is None and surat.jenis_surat == "permohonan":
        tte_utama = db.query(models.PersetujuanSurat).filter(
            models.PersetujuanSurat.surat_id  == surat.id,
            models.PersetujuanSurat.jenis_ttd == "TTE Utama"
        ).first()
        if tte_utama:
            penandatangan = db.query(models.Pegawai).filter(
                models.Pegawai.nip == tte_utama.nip_penyetuju
            ).first()

    tujuan = None
    if surat.nip_tujuan:
        tujuan = db.query(models.Pegawai).filter(
            models.Pegawai.nip == surat.nip_tujuan
        ).first()

    # ✅ Ambil seluruh chain persetujuan untuk ditampilkan di preview (blok paraf)
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

    # Cek TTE selesai
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

    # Permohonan → selalu kop Direktur (D001), lainnya → kop unit pembuat
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
        "jabatan_penandatangan"  : penandatangan.jabatan if penandatangan else None,
        "nip_penandatangan"      : penandatangan.nip     if penandatangan else None,
        "nama_tujuan"            : tujuan.nama    if tujuan else None,
        "jabatan_tujuan"         : tujuan.jabatan if tujuan else None,
        "tujuan_tambahan"        : surat.tujuan_tambahan,
        "tte_selesai"            : tte_selesai,
        "file_ttd_penandatangan" : file_ttd_penandatangan,
        "tanggal_tte"            : tanggal_tte,
        "chain_persetujuan"      : chain_persetujuan,  # ✅ untuk blok paraf di preview
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

    # Riwayat persetujuan
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

    # Info tujuan
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

    # Urutkan semua berdasarkan tanggal terbaru
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