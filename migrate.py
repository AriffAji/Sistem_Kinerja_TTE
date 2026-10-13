from database import engine
from sqlalchemy import text

def jalankan_migrasi():
    with engine.connect() as conn:

        perintah_sql = [
            "ALTER TABLE surat ADD COLUMN tipe_input VARCHAR(20) DEFAULT 'editor'",
            "ALTER TABLE surat ADD COLUMN file_upload VARCHAR(255)",
            "ALTER TABLE surat ADD COLUMN file_final VARCHAR(255)",
            "ALTER TABLE persetujuan_surat ADD COLUMN mode_paralel BOOLEAN DEFAULT FALSE",
            "ALTER TABLE persetujuan_surat ADD COLUMN posisi_hal INTEGER",
            "ALTER TABLE persetujuan_surat ADD COLUMN posisi_x FLOAT",
            "ALTER TABLE persetujuan_surat ADD COLUMN posisi_y FLOAT",
            "ALTER TABLE persetujuan_surat ADD COLUMN label_ttd VARCHAR(100)",
            "ALTER TABLE unit_organisasi ADD COLUMN jabatan_surat VARCHAR(255)",
            "ALTER TABLE disposisi ADD COLUMN sudah_diketahui BOOLEAN DEFAULT FALSE",
            "ALTER TABLE disposisi ALTER COLUMN catatan SET NOT NULL",
            "ALTER TABLE disposisi ADD COLUMN disposisi_asal_id INTEGER REFERENCES disposisi(id)",
            "ALTER TABLE surat ADD COLUMN surat_asal_id INTEGER REFERENCES surat(id)",
            "ALTER TABLE surat ADD COLUMN disposisi_asal_id INTEGER REFERENCES disposisi(id)",
            "ALTER TABLE surat ADD COLUMN penugasan_asal_id INTEGER REFERENCES penugasan(id)",
            "ALTER TABLE surat ADD COLUMN tanggal_tugas_mulai VARCHAR(50)",
            "ALTER TABLE surat ADD COLUMN tanggal_tugas_selesai VARCHAR(50)",
            "ALTER TABLE surat ADD COLUMN jenis_tugas VARCHAR(50)",
            "ALTER TABLE surat ADD COLUMN tempat_tugas VARCHAR(255)",
        ]

        for sql in perintah_sql:
            deskripsi = sql if "ADD COLUMN" not in sql else sql.split("ADD COLUMN")[1].strip().split(" ")[0]
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Berhasil : {deskripsi}")
            except Exception as e:
                conn.rollback()
                pesan = str(e).lower()
                if "duplicate column" in pesan or "already exists" in pesan:
                    print(f"⚠️  Sudah ada (skip) : {deskripsi}")
                else:
                    print(f"❌ Error             : {e}")

def buat_tabel_posisi_nomor_surat():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS posisi_nomor_surat (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER REFERENCES surat(id),
                posisi_hal INTEGER NOT NULL,
                posisi_x FLOAT NOT NULL,
                posisi_y FLOAT NOT NULL,
                prefix_teks VARCHAR(50)
            )
        """))
        conn.commit()
        print("✅ Tabel 'posisi_nomor_surat' berhasil dibuat (atau sudah ada sebelumnya)")


def buat_tabel_kinerja():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS disposisi (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER REFERENCES surat(id),
                nip_dari VARCHAR(50) REFERENCES pegawai(nip),
                nip_ke VARCHAR(50) REFERENCES pegawai(nip),
                level_dari INTEGER,
                level_ke INTEGER,
                catatan TEXT,
                status VARCHAR(30) DEFAULT 'diteruskan',
                tanggal TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()
        print("✅ Tabel 'disposisi' siap")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS penugasan (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER REFERENCES surat(id),
                disposisi_id INTEGER REFERENCES disposisi(id),
                nip_pemberi VARCHAR(50) REFERENCES pegawai(nip),
                nip_penerima VARCHAR(50) REFERENCES pegawai(nip),
                deskripsi_tugas TEXT,
                deadline TIMESTAMP,
                status VARCHAR(30) DEFAULT 'proses',
                tanggal_ditugaskan TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()
        print("✅ Tabel 'penugasan' siap")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_tugas (
                id SERIAL PRIMARY KEY,
                penugasan_id INTEGER REFERENCES penugasan(id),
                isi_laporan TEXT,
                file_bukti VARCHAR(255),
                tanggal_lapor TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()
        print("✅ Tabel 'laporan_tugas' siap")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS penilaian (
                id SERIAL PRIMARY KEY,
                penugasan_id INTEGER REFERENCES penugasan(id),
                skala VARCHAR(20) NOT NULL,
                catatan TEXT,
                nip_penilai VARCHAR(50) REFERENCES pegawai(nip),
                tanggal_nilai TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()
        print("✅ Tabel 'penilaian' siap")

def buat_tabel_unit_induk_tambahan():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS unit_induk_tambahan (
                id SERIAL PRIMARY KEY,
                kode_unit_anak VARCHAR(50) NOT NULL,
                kode_induk_tambahan VARCHAR(50) NOT NULL
            )
        """))
        conn.commit()
        print("✅ Tabel 'unit_induk_tambahan' siap")

def buat_tabel_penilaian_layanan():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS penilaian_layanan (
                id SERIAL PRIMARY KEY,
                surat_root_id INTEGER UNIQUE REFERENCES surat(id),
                nip_penilai VARCHAR(50) REFERENCES pegawai(nip),
                bintang INTEGER NOT NULL,
                catatan TEXT,
                tanggal_nilai TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()
        print("✅ Tabel 'penilaian_layanan' siap")


def buat_tabel_penerima_surat_tugas():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS penerima_surat_tugas (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER NOT NULL REFERENCES surat(id),
                nip_pegawai VARCHAR(50) NOT NULL REFERENCES pegawai(nip)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_penerima_surat_tugas_surat_id ON penerima_surat_tugas(surat_id)"
        ))
        conn.commit()
        print("✅ Tabel 'penerima_surat_tugas' siap")


def buat_tabel_laporan_pd():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_pd (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER NOT NULL UNIQUE REFERENCES surat(id),
                nip_penyusun VARCHAR(50) NOT NULL REFERENCES pegawai(nip),
                tempat VARCHAR(255) NOT NULL,
                hasil_kegiatan TEXT NOT NULL,
                file_pdf VARCHAR(255),
                status VARCHAR(20) DEFAULT 'draft',
                tanggal_dibuat TIMESTAMP DEFAULT NOW(),
                tanggal_final TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_pd_peserta (
                id SERIAL PRIMARY KEY,
                laporan_id INTEGER NOT NULL REFERENCES laporan_pd(id),
                nip_pegawai VARCHAR(50) NOT NULL REFERENCES pegawai(nip),
                penugasan_id INTEGER,
                sudah_ttd BOOLEAN DEFAULT FALSE,
                tanggal_ttd TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_laporan_pd_peserta_laporan_id ON laporan_pd_peserta(laporan_id)"
        ))
        conn.commit()
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_pd_lampiran (
                id SERIAL PRIMARY KEY,
                laporan_id INTEGER NOT NULL REFERENCES laporan_pd(id),
                file_path VARCHAR(255) NOT NULL,
                nama_asli VARCHAR(255) NOT NULL,
                tanggal TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_laporan_pd_lampiran_laporan_id ON laporan_pd_lampiran(laporan_id)"
        ))
        conn.commit()
        print("✅ Tabel 'laporan_pd', 'laporan_pd_peserta', dan 'laporan_pd_lampiran' siap")


def buat_tabel_delegasi_surat():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS delegasi_surat (
                id SERIAL PRIMARY KEY,
                nip_pemberi VARCHAR(50) NOT NULL REFERENCES pegawai(nip),
                nip_staf VARCHAR(50) NOT NULL REFERENCES pegawai(nip),
                aktif BOOLEAN DEFAULT TRUE,
                tanggal_dibuat TIMESTAMP DEFAULT NOW(),
                tanggal_dicabut TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_delegasi_surat_nip_staf ON delegasi_surat(nip_staf)"
        ))
        conn.commit()
        print("✅ Tabel 'delegasi_surat' siap")


def buat_tabel_afiliasi_tambahan():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS afiliasi_tambahan (
                id SERIAL PRIMARY KEY,
                nip VARCHAR(50) REFERENCES pegawai(nip),
                kode_unit_tambahan VARCHAR(50) NOT NULL,
                jabatan_tambahan VARCHAR(255)
            )
        """))
        conn.commit()
        print("✅ Tabel 'afiliasi_tambahan' siap")

def tambah_kolom_rapat():
    """Kolom untuk fitur Surat Tugas jenis Rapat."""
    perintah_sql = [
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS jam_mulai VARCHAR(10)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS jam_selesai VARCHAR(10)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS agenda_rapat VARCHAR(500)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS nip_pimpinan_rapat VARCHAR(50) REFERENCES pegawai(nip)",
        "ALTER TABLE penerima_surat_tugas ADD COLUMN IF NOT EXISTS is_notulen BOOLEAN DEFAULT FALSE",
    ]
    with engine.connect() as conn:
        for sql in perintah_sql:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Berhasil : {sql}")
            except Exception as e:
                conn.rollback()
                print(f"❌ Error    : {e}")

def buat_tabel_laporan_rapat():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_rapat (
                id SERIAL PRIMARY KEY,
                surat_id INTEGER NOT NULL UNIQUE REFERENCES surat(id),
                hasil_rapat TEXT,
                status VARCHAR(20) DEFAULT 'draft',
                catatan_revisi TEXT,
                file_notulen VARCHAR(255),
                file_daftar_hadir VARCHAR(255),
                tanggal_dibuat TIMESTAMP DEFAULT NOW(),
                tanggal_kirim TIMESTAMP,
                tanggal_final TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_rapat_peserta (
                id SERIAL PRIMARY KEY,
                laporan_id INTEGER NOT NULL REFERENCES laporan_rapat(id),
                nip_pegawai VARCHAR(50) NOT NULL REFERENCES pegawai(nip),
                penugasan_id INTEGER,
                status_hadir VARCHAR(20) DEFAULT 'belum',
                alasan TEXT,
                tanggal_hadir TIMESTAMP,
                nip_mewakili VARCHAR(50),
                is_notulen BOOLEAN DEFAULT FALSE
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_laporan_rapat_peserta_laporan_id ON laporan_rapat_peserta(laporan_id)"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS laporan_rapat_tamu (
                id SERIAL PRIMARY KEY,
                laporan_id INTEGER NOT NULL REFERENCES laporan_rapat(id),
                nama VARCHAR(255) NOT NULL,
                instansi VARCHAR(255),
                hadir BOOLEAN DEFAULT TRUE
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_laporan_rapat_tamu_laporan_id ON laporan_rapat_tamu(laporan_id)"
        ))
        conn.commit()
        print("✅ Tabel 'laporan_rapat', 'laporan_rapat_peserta', dan 'laporan_rapat_tamu' siap")

def tambah_kolom_catatan_kembali():
    """Kolom alasan surat dikembalikan pejabat ke pembuat."""
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE surat ADD COLUMN IF NOT EXISTS catatan_kembali TEXT"))
            conn.execute(text("ALTER TABLE surat ADD COLUMN IF NOT EXISTS catatan_jadwal TEXT"))
            conn.commit()
            print("✅ Kolom 'catatan_kembali' pada tabel surat siap")
        except Exception as e:
            conn.rollback()
            print(f"❌ Error    : {e}")

def sinkron_model_dan_indeks():
    """Kolom yang ada di models.py tapi belum tercatat di migrasi, plus indeks & cascade."""
    perintah = [
        # --- kolom surat ---
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS kode_pelacakan VARCHAR(50)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS nip_tujuan VARCHAR(50) REFERENCES pegawai(nip)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS tujuan_tambahan VARCHAR(255)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS nip_penandatangan VARCHAR(50) REFERENCES pegawai(nip)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS tanggal_surat VARCHAR(50)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS tanggal_diterima VARCHAR(50)",
        # --- kolom pegawai & unit ---
        "ALTER TABLE pegawai ADD COLUMN IF NOT EXISTS pin_tte VARCHAR(255)",
        "ALTER TABLE pegawai ADD COLUMN IF NOT EXISTS file_ttd VARCHAR(255)",
        "ALTER TABLE pegawai ADD COLUMN IF NOT EXISTS tte_aktif BOOLEAN DEFAULT FALSE",
        "ALTER TABLE unit_organisasi ADD COLUMN IF NOT EXISTS punya_kop_sendiri BOOLEAN DEFAULT FALSE",
        "ALTER TABLE unit_organisasi ADD COLUMN IF NOT EXISTS nip_pimpinan VARCHAR(50)",
        "ALTER TABLE unit_organisasi ADD COLUMN IF NOT EXISTS nama_pimpinan VARCHAR(255)",
        "ALTER TABLE unit_organisasi ADD COLUMN IF NOT EXISTS status_posisi VARCHAR(255)",
        # --- samakan panjang kolom ---
        "ALTER TABLE persetujuan_surat ALTER COLUMN label_ttd TYPE VARCHAR(255)",
        # --- cascade: hapus draft tidak lagi terblokir pin nomor surat ---
        "ALTER TABLE posisi_nomor_surat DROP CONSTRAINT IF EXISTS posisi_nomor_surat_surat_id_fkey",
        "ALTER TABLE posisi_nomor_surat ADD CONSTRAINT posisi_nomor_surat_surat_id_fkey "
        "FOREIGN KEY (surat_id) REFERENCES surat(id) ON DELETE CASCADE",
        # --- indeks kinerja ---
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_surat_kode_pelacakan ON surat(kode_pelacakan)",
        "CREATE INDEX IF NOT EXISTS ix_disposisi_surat_id ON disposisi(surat_id)",
        "CREATE INDEX IF NOT EXISTS ix_disposisi_nip_ke ON disposisi(nip_ke)",
        "CREATE INDEX IF NOT EXISTS ix_disposisi_asal ON disposisi(disposisi_asal_id)",
        "CREATE INDEX IF NOT EXISTS ix_penugasan_surat_id ON penugasan(surat_id)",
        "CREATE INDEX IF NOT EXISTS ix_penugasan_nip_penerima ON penugasan(nip_penerima)",
        "CREATE INDEX IF NOT EXISTS ix_penugasan_nip_pemberi ON penugasan(nip_pemberi)",
        "CREATE INDEX IF NOT EXISTS ix_persetujuan_surat_id ON persetujuan_surat(surat_id)",
        "CREATE INDEX IF NOT EXISTS ix_persetujuan_nip ON persetujuan_surat(nip_penyetuju)",
        "CREATE INDEX IF NOT EXISTS ix_surat_asal_id ON surat(surat_asal_id)",
        "CREATE INDEX IF NOT EXISTS ix_surat_nip_pembuat ON surat(nip_pembuat)",
    ]
    with engine.connect() as conn:
        for sql in perintah:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ {sql[:90]}")
            except Exception as e:
                conn.rollback()
                print(f"❌ {sql[:60]}... -> {e}")


def tambah_fitur_portal_publik():
    """Tahap 2-5: kontak pengirim, penilaian eksternal, log WhatsApp."""
    perintah = [
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS nama_pengirim VARCHAR(255)",
        "ALTER TABLE surat ADD COLUMN IF NOT EXISTS no_wa_pengirim VARCHAR(20)",
        """CREATE TABLE IF NOT EXISTS penilaian_eksternal (
               id SERIAL PRIMARY KEY,
               surat_id INTEGER NOT NULL UNIQUE REFERENCES surat(id) ON DELETE CASCADE,
               bintang INTEGER NOT NULL,
               catatan TEXT,
               tanggal_nilai TIMESTAMP DEFAULT NOW()
           )""",
        """CREATE TABLE IF NOT EXISTS notifikasi_wa (
               id SERIAL PRIMARY KEY,
               surat_id INTEGER REFERENCES surat(id) ON DELETE CASCADE,
               jenis VARCHAR(30) NOT NULL,
               tujuan VARCHAR(20) NOT NULL,
               pesan TEXT,
               status VARCHAR(20) DEFAULT 'menunggu',
               percobaan INTEGER DEFAULT 0,
               galat TEXT,
               tanggal_dibuat TIMESTAMP DEFAULT NOW(),
               tanggal_kirim TIMESTAMP
           )""",
        "CREATE INDEX IF NOT EXISTS ix_notifikasi_wa_surat ON notifikasi_wa(surat_id)",
        "CREATE INDEX IF NOT EXISTS ix_notifikasi_wa_status ON notifikasi_wa(status)",
    ]
    with engine.connect() as conn:
        for sql in perintah:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ {sql.strip().splitlines()[0][:90]}")
            except Exception as e:
                conn.rollback()
                print(f"❌ {e}")

def tambah_kolom_tanggal_arsip():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE disposisi ADD COLUMN IF NOT EXISTS tanggal_arsip TIMESTAMP"))
            conn.commit()
            print("✅ Kolom 'tanggal_arsip' siap")
        except Exception as e:
            conn.rollback()
            print(f"❌ Error : {e}")

if __name__ == "__main__":
    buat_tabel_kinerja()              # penugasan harus ada dulu sebelum ALTER surat ke penugasan(id)
    jalankan_migrasi()
    buat_tabel_posisi_nomor_surat()
    buat_tabel_unit_induk_tambahan()
    buat_tabel_afiliasi_tambahan()
    buat_tabel_penilaian_layanan()
    buat_tabel_delegasi_surat()
    buat_tabel_penerima_surat_tugas()
    buat_tabel_laporan_pd()
    tambah_kolom_rapat()
    buat_tabel_laporan_rapat()
    tambah_kolom_catatan_kembali()    
    sinkron_model_dan_indeks()
    tambah_fitur_portal_publik()
    tambah_kolom_tanggal_arsip()