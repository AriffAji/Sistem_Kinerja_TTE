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
        ]

        for sql in perintah_sql:
            nama_kolom = sql.split("ADD COLUMN")[1].strip().split(" ")[0]
            try:
                conn.execute(text(sql))
                conn.commit()          # commit per-statement, langsung selesai transaksinya
                print(f"✅ Kolom ditambahkan : {nama_kolom}")
            except Exception as e:
                conn.rollback()         # ✅ KUNCI: reset transaksi yang gagal
                pesan = str(e).lower()
                if "duplicate column" in pesan or "already exists" in pesan:
                    print(f"⚠️  Sudah ada (skip) : {nama_kolom}")
                else:
                    print(f"❌ Error             : {e}")

        print("\n🎉 Migrasi database selesai!")

if __name__ == "__main__":
    jalankan_migrasi()