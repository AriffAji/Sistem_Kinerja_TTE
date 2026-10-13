from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text, Float
from sqlalchemy.orm import relationship, backref
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

    kode_pelacakan = Column(String(50), unique=True, index=True, nullable=True)

    # Identitas Pembuat
    nip_pembuat = Column(String(50), ForeignKey("pegawai.nip"))

    # Tujuan (Jika Internal)
    nip_tujuan = Column(String(50), ForeignKey("pegawai.nip"), nullable=True)
    tujuan_tambahan = Column(String(255), nullable=True)

    # Tujuan (Jika Permohonan, Butuh TTE Siapa?)
    nip_penandatangan = Column(String(50), ForeignKey("pegawai.nip"), nullable=True)

    status = Column(String(50), default="draft")
    tanggal_dibuat = Column(DateTime, default=datetime.utcnow)
    tanggal_surat = Column(String(50), nullable=True)
    tanggal_diterima = Column(String(50), nullable=True)

    surat_asal_id     = Column(Integer, ForeignKey("surat.id"), nullable=True)
    disposisi_asal_id = Column(Integer, ForeignKey("disposisi.id"), nullable=True)
    penugasan_asal_id = Column(Integer, nullable=True)
    tanggal_tugas_mulai   = Column(String(50), nullable=True)
    tanggal_tugas_selesai = Column(String(50), nullable=True)
    jenis_tugas           = Column(String(50), nullable=True)
    tempat_tugas          = Column(String(255), nullable=True)

    # khusus surat tugas jenis rapat
    jam_mulai          = Column(String(10), nullable=True)
    jam_selesai        = Column(String(10), nullable=True)
    agenda_rapat       = Column(String(500), nullable=True)
    nip_pimpinan_rapat = Column(String(50), ForeignKey("pegawai.nip"), nullable=True)
    catatan_jadwal     = Column(Text, nullable=True)

    # kolom baru
    catatan_kembali = Column(Text, nullable=True)
    nama_pengirim   = Column(String(255), nullable=True)
    no_wa_pengirim  = Column(String(20), nullable=True)

    pembuat = relationship("Pegawai", foreign_keys=[nip_pembuat])
    riwayat = relationship("PersetujuanSurat", back_populates="surat", cascade="all, delete-orphan")

class PenilaianEksternal(Base):
    __tablename__ = "penilaian_eksternal"
    id            = Column(Integer, primary_key=True, index=True)
    surat_id      = Column(Integer, ForeignKey("surat.id", ondelete="CASCADE"), unique=True, nullable=False)
    bintang       = Column(Integer, nullable=False)
    catatan       = Column(Text, nullable=True)
    tanggal_nilai = Column(DateTime, default=datetime.utcnow)

class NotifikasiWA(Base):
    __tablename__ = "notifikasi_wa"
    id             = Column(Integer, primary_key=True, index=True)
    surat_id       = Column(Integer, ForeignKey("surat.id", ondelete="CASCADE"))
    jenis          = Column(String(30), nullable=False)    # surat_masuk | hasil_surat
    tujuan         = Column(String(20), nullable=False)
    pesan          = Column(Text, nullable=True)
    status         = Column(String(20), default="menunggu")  # menunggu | terkirim | gagal
    percobaan      = Column(Integer, default=0)
    galat          = Column(Text, nullable=True)
    tanggal_dibuat = Column(DateTime, default=datetime.utcnow)
    tanggal_kirim  = Column(DateTime, nullable=True)

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

class PosisiNomorSurat(Base):
    __tablename__ = "posisi_nomor_surat"
    id = Column(Integer, primary_key=True, index=True)
    surat_id    = Column(Integer, ForeignKey("surat.id"))
    posisi_hal  = Column(Integer, nullable=False)
    posisi_x    = Column(Float, nullable=False)
    posisi_y    = Column(Float, nullable=False)
    prefix_teks = Column(String(50), nullable=True)   # contoh: "Nomor :", atau kosong

    surat = relationship("Surat")
    
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

class Disposisi(Base):
    __tablename__ = "disposisi"
    id = Column(Integer, primary_key=True, index=True)
    surat_id   = Column(Integer, ForeignKey("surat.id"))
    nip_dari   = Column(String(50), ForeignKey("pegawai.nip"))
    nip_ke     = Column(String(50), ForeignKey("pegawai.nip"))
    level_dari = Column(Integer, nullable=True)
    level_ke   = Column(Integer, nullable=True)
    catatan    = Column(Text, nullable=False)  # sekarang wajib
    status     = Column(String(30), default="diteruskan")  # diteruskan | diproses_jadi_tugas | diarsipkan
    sudah_diketahui = Column(Boolean, default=False)
    tanggal    = Column(DateTime, default=datetime.utcnow)
    tanggal_arsip = Column(DateTime, nullable=True)
    # ✅ BARU — bentuk pohon disposisi. NULL = disposisi akar (langsung dari surat, hanya boleh Direktur).
    disposisi_asal_id = Column(Integer, ForeignKey("disposisi.id"), nullable=True)
    

    surat = relationship("Surat", foreign_keys=[surat_id])

    # ✅ BARU — akses anak-anak disposisi ini (hasil "diteruskan lagi")
    anak_disposisi = relationship(
        "Disposisi",
        backref=backref("induk_disposisi", remote_side=[id])
    )


class Penugasan(Base):
    __tablename__ = "penugasan"
    id             = Column(Integer, primary_key=True, index=True)
    surat_id       = Column(Integer, ForeignKey("surat.id"))
    disposisi_id   = Column(Integer, ForeignKey("disposisi.id"), nullable=True)
    nip_pemberi    = Column(String(50), ForeignKey("pegawai.nip"))
    nip_penerima   = Column(String(50), ForeignKey("pegawai.nip"))
    deskripsi_tugas = Column(Text, nullable=True)
    deadline       = Column(DateTime, nullable=True)
    status         = Column(String(30), default="proses")  # proses | selesai | ditolak
    tanggal_ditugaskan = Column(DateTime, default=datetime.utcnow)

    surat     = relationship("Surat")
    disposisi = relationship("Disposisi")


class LaporanTugas(Base):
    __tablename__ = "laporan_tugas"
    id            = Column(Integer, primary_key=True, index=True)
    penugasan_id  = Column(Integer, ForeignKey("penugasan.id"))
    isi_laporan   = Column(Text, nullable=True)
    file_bukti    = Column(String(255), nullable=True)
    tanggal_lapor = Column(DateTime, default=datetime.utcnow)

    penugasan = relationship("Penugasan")


class Penilaian(Base):
    __tablename__ = "penilaian"
    id           = Column(Integer, primary_key=True, index=True)
    penugasan_id = Column(Integer, ForeignKey("penugasan.id"))
    skala        = Column(String(20), nullable=False)  # sangat_baik | baik | cukup | kurang
    catatan      = Column(Text, nullable=True)
    nip_penilai  = Column(String(50), ForeignKey("pegawai.nip"))
    tanggal_nilai = Column(DateTime, default=datetime.utcnow)

    penugasan = relationship("Penugasan")

class UnitIndukTambahan(Base):
    __tablename__ = "unit_induk_tambahan"
    id = Column(Integer, primary_key=True, index=True)
    kode_unit_anak       = Column(String(50), nullable=False)  # anak yang punya jalur tambahan
    kode_induk_tambahan  = Column(String(50), nullable=False)  # induk tambahan di luar kode_induk normal

class AfiliasiTambahan(Base):
    __tablename__ = "afiliasi_tambahan"
    id = Column(Integer, primary_key=True, index=True)
    nip                 = Column(String(50), ForeignKey("pegawai.nip"))
    kode_unit_tambahan  = Column(String(50), nullable=False)
    jabatan_tambahan    = Column(String(255), nullable=True)

class PenilaianLayanan(Base):
    __tablename__ = "penilaian_layanan"
    id = Column(Integer, primary_key=True, index=True)
    surat_root_id = Column(Integer, ForeignKey("surat.id"), unique=True)  # ✅ 1 penilaian per utas
    nip_penilai   = Column(String(50), ForeignKey("pegawai.nip"))
    bintang       = Column(Integer, nullable=False)  # 1–5
    catatan       = Column(Text, nullable=True)
    tanggal_nilai = Column(DateTime, default=datetime.utcnow)

    surat_root = relationship("Surat")

class DelegasiSurat(Base):
    __tablename__ = "delegasi_surat"
    id              = Column(Integer, primary_key=True, index=True)
    nip_pemberi     = Column(String(50), ForeignKey("pegawai.nip"), nullable=False)
    nip_staf        = Column(String(50), ForeignKey("pegawai.nip"), nullable=False, index=True)
    aktif           = Column(Boolean, default=True)
    tanggal_dibuat  = Column(DateTime, default=datetime.utcnow)
    tanggal_dicabut = Column(DateTime, nullable=True)


class PenerimaSuratTugas(Base):
    __tablename__ = "penerima_surat_tugas"
    id          = Column(Integer, primary_key=True, index=True)
    surat_id    = Column(Integer, ForeignKey("surat.id"), nullable=False, index=True)
    nip_pegawai = Column(String(50), ForeignKey("pegawai.nip"), nullable=False)
    is_notulen  = Column(Boolean, default=False)


class LaporanPD(Base):
    __tablename__ = "laporan_pd"
    id             = Column(Integer, primary_key=True, index=True)
    surat_id       = Column(Integer, ForeignKey("surat.id"), nullable=False, unique=True)   # 1 surat tugas = 1 laporan
    nip_penyusun   = Column(String(50), ForeignKey("pegawai.nip"), nullable=False)
    tempat         = Column(String(255), nullable=False)
    hasil_kegiatan = Column(Text, nullable=False)
    file_pdf       = Column(String(255), nullable=True)
    status         = Column(String(20), default="draft")   # draft | final
    tanggal_dibuat = Column(DateTime, default=datetime.utcnow)
    tanggal_final  = Column(DateTime, nullable=True)


class LaporanPDPeserta(Base):
    __tablename__ = "laporan_pd_peserta"
    id           = Column(Integer, primary_key=True, index=True)
    laporan_id   = Column(Integer, ForeignKey("laporan_pd.id"), nullable=False, index=True)
    nip_pegawai  = Column(String(50), ForeignKey("pegawai.nip"), nullable=False)
    penugasan_id = Column(Integer, nullable=True)
    sudah_ttd    = Column(Boolean, default=False)
    tanggal_ttd  = Column(DateTime, nullable=True)


class LaporanPDLampiran(Base):
    __tablename__ = "laporan_pd_lampiran"
    id          = Column(Integer, primary_key=True, index=True)
    laporan_id  = Column(Integer, ForeignKey("laporan_pd.id"), nullable=False, index=True)
    file_path   = Column(String(255), nullable=False)
    nama_asli   = Column(String(255), nullable=False)
    tanggal     = Column(DateTime, default=datetime.utcnow)



class LaporanRapat(Base):
    __tablename__ = "laporan_rapat"
    id                = Column(Integer, primary_key=True, index=True)
    surat_id          = Column(Integer, ForeignKey("surat.id"), nullable=False, unique=True)  # 1 surat rapat = 1 laporan
    hasil_rapat       = Column(Text, nullable=True)
    status            = Column(String(20), default="draft")   # draft | menunggu_ttd | revisi | final
    catatan_revisi    = Column(Text, nullable=True)
    file_notulen      = Column(String(255), nullable=True)
    file_daftar_hadir = Column(String(255), nullable=True)
    tanggal_dibuat    = Column(DateTime, default=datetime.utcnow)
    tanggal_kirim     = Column(DateTime, nullable=True)
    tanggal_final     = Column(DateTime, nullable=True)


class LaporanRapatPeserta(Base):
    __tablename__ = "laporan_rapat_peserta"
    id            = Column(Integer, primary_key=True, index=True)
    laporan_id    = Column(Integer, ForeignKey("laporan_rapat.id"), nullable=False, index=True)
    nip_pegawai   = Column(String(50), ForeignKey("pegawai.nip"), nullable=False)
    penugasan_id  = Column(Integer, nullable=True)
    status_hadir  = Column(String(20), default="belum")   # belum | hadir | izin | tidak_hadir | diwakilkan
    alasan        = Column(Text, nullable=True)
    tanggal_hadir = Column(DateTime, nullable=True)
    nip_mewakili  = Column(String(50), nullable=True)     # diisi jika baris ini adalah perwakilan dari NIP tersebut
    is_notulen    = Column(Boolean, default=False)


class LaporanRapatTamu(Base):
    __tablename__ = "laporan_rapat_tamu"
    id          = Column(Integer, primary_key=True, index=True)
    laporan_id  = Column(Integer, ForeignKey("laporan_rapat.id"), nullable=False, index=True)
    nama        = Column(String(255), nullable=False)
    instansi    = Column(String(255), nullable=True)
    hadir       = Column(Boolean, default=True)