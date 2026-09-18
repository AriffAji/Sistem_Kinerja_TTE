from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text, Float
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class UnitOrganisasi(Base):
    __tablename__ = "unit_organisasi"
    id = Column(Integer, primary_key=True, index=True)
    kode_unit = Column(String(50), unique=True, index=True, nullable=False)
    nama_unit = Column(String(255), nullable=False)
    level = Column(Integer, nullable=False)
    kode_induk = Column(String(50), nullable=True) 
    nama_induk = Column(String(255), nullable=True)
    nip_pimpinan = Column(String(50), nullable=True)
    nama_pimpinan = Column(String(255), nullable=True)
    status_posisi = Column(String(255), nullable=True)
    punya_kop_sendiri = Column(Boolean, default=False)
    jabatan_surat = Column(String(255), nullable=True)


# --- TABEL SURAT MASTER ---
class Surat(Base):
    __tablename__ = "surat"
    id = Column(Integer, primary_key=True, index=True)
    jenis_surat = Column(String(50), nullable=False) # 'internal', 'eksternal', 'permohonan'
    nomor_surat = Column(String(100), nullable=True) 
    perihal = Column(String(255), nullable=False)
    asal_surat = Column(String(255), nullable=True) 
    isi_surat = Column(Text, nullable=True) 
    file_surat = Column(String(255), nullable=True) 
    tipe_input  = Column(String, default="editor")
    file_upload = Column(String, nullable=True)
    file_final  = Column(String, nullable=True)

    # --- KOLOM BARU ---
    kode_pelacakan = Column(String(50), unique=True, index=True, nullable=True)
    
    # Identitas Pembuat
    nip_pembuat = Column(String(50), ForeignKey("pegawai.nip"))
    
    # Tujuan (Jika Internal)
    nip_tujuan = Column(String(50), ForeignKey("pegawai.nip"), nullable=True)
    tujuan_tambahan = Column(String(255), nullable=True)
    
    # Tujuan (Jika Permohonan, Butuh TTE Siapa?)
    nip_penandatangan = Column(String(50), ForeignKey("pegawai.nip"), nullable=True)
    
    status = Column(String(50), default="draft") # draft, proses, selesai, ditolak
    tanggal_dibuat = Column(DateTime, default=datetime.utcnow)
    tanggal_surat = Column(String(50), nullable=True)
    tanggal_diterima = Column(String(50), nullable=True)

    pembuat = relationship("Pegawai", foreign_keys=[nip_pembuat])
    riwayat = relationship("PersetujuanSurat", back_populates="surat", cascade="all, delete-orphan")

# --- TABEL CASCADING (Khusus Permohonan & Approval) ---
class PersetujuanSurat(Base):
    __tablename__ = "persetujuan_surat"
    id = Column(Integer, primary_key=True, index=True)
    surat_id = Column(Integer, ForeignKey("surat.id"))
    nip_penyetuju = Column(String(50), ForeignKey("pegawai.nip"))
    jenis_ttd = Column(String(50), nullable=True) # Paraf atau TTE Utama
    urutan = Column(Integer, default=1) 
    status_persetujuan = Column(String(50), default="menunggu") # menunggu, disetujui, ditolak
    catatan = Column(Text, nullable=True)
    tanggal_proses = Column(DateTime, nullable=True)
    # --- kolom baru (fitur posisi TTD di PDF) ---
    mode_paralel = Column(Boolean, default=False)
    posisi_hal   = Column(Integer, nullable=True)
    posisi_x     = Column(Float, nullable=True)
    posisi_y     = Column(Float, nullable=True)
    label_ttd    = Column(String(255), nullable=True)

    surat = relationship("Surat", back_populates="riwayat")

class Pegawai(Base):
    __tablename__ = "pegawai"
    id        = Column(Integer, primary_key=True, index=True)
    nip       = Column(String(50), unique=True, index=True, nullable=False)
    password  = Column(String(255), nullable=False)
    nama      = Column(String(255), nullable=False)
    jabatan   = Column(String(255), nullable=True)
    role_ppnp = Column(String(50), default="staf")
    kode_unit = Column(String(50), ForeignKey("unit_organisasi.kode_unit"), nullable=True)

    # ✅ Kolom TTE (sudah ditambah via ALTER TABLE)
    pin_tte   = Column(String(255), nullable=True)
    file_ttd  = Column(String(255), nullable=True)
    tte_aktif = Column(Boolean, default=False)

    unit = relationship("UnitOrganisasi")