import pandas as pd
from database import engine, SessionLocal, Base
from models import UnitOrganisasi

# Ini akan otomatis membuat tabel 'unit_organisasi' di pgAdmin jika belum ada
Base.metadata.create_all(bind=engine)

def jalankan_import():
    db = SessionLocal()
    
    # Daftar unit yang memotong kompas (bypass) langsung ke Direktur
    unit_kop_sendiri = [
        "Jurusan Bisnis Pertanian",
        "Jurusan Budidaya Tanaman",
        "Jurusan Peternakan dan Kesehatan Hewan",
        "Jurusan Rekayasa Pertanian dan Komputer",
        "Jurusan Teknologi Hasil Pertanian",
        "Pusat Penelitian dan Pengabdian Kepada Masyarakat",
        "Unit Pengembangan Pembelajaran dan Penjaminan Mutu",
        "UPT Perpustakaan",
        "UPT Komputer",
        "UPT Perbaikan dan Pemeliharaan"
    ]

    file_path = 'Struktur_Organisasi_Database (1).xlsx'
    print(f"Membaca file {file_path}...")
    
    # Membaca file Excel sheet 'Struktur Organisasi'
    df = pd.read_excel(file_path, sheet_name='Struktur Organisasi')
    
    # --- BARIS TAMBAHAN: Hapus baris ganda berdasarkan Kode Unit ---
    df = df.drop_duplicates(subset=['Kode Unit'], keep='first')
    
    # Opsional: Hapus data lama agar tidak duplikat jika script dijalankan berulang
    db.query(UnitOrganisasi).delete()
    
    jumlah_data = 0
    for index, row in df.iterrows():
        # Membersihkan data: Mengubah nilai strip (-) atau NaN dari Excel menjadi NULL/None di database
        kode_induk = row['Kode Induk'] if pd.notna(row['Kode Induk']) and str(row['Kode Induk']).strip() != '-' else None
        nama_induk = row['Nama Induk'] if pd.notna(row['Nama Induk']) and str(row['Nama Induk']).strip() != '-' else None
        nip = row['NIP'] if pd.notna(row['NIP']) and str(row['NIP']).strip() != '-' else None
        pimpinan = row['Nama Pimpinan / Koordinator'] if pd.notna(row['Nama Pimpinan / Koordinator']) and str(row['Nama Pimpinan / Koordinator']).strip() != '-' else None
        status = row['Status Posisi'] if pd.notna(row['Status Posisi']) else None
        
        # Cek logika True/False untuk by-pass surat
        punya_kop = str(row['Nama Unit']).strip() in unit_kop_sendiri
        
        # Mapping ke format database
        unit = UnitOrganisasi(
            kode_unit=str(row['Kode Unit']).strip(),
            nama_unit=str(row['Nama Unit']).strip(),
            level=int(row['Level']),
            kode_induk=kode_induk,
            nama_induk=nama_induk,
            nip_pimpinan=nip,
            nama_pimpinan=pimpinan,
            status_posisi=status,
            punya_kop_sendiri=punya_kop
        )
        db.add(unit)
        jumlah_data += 1
        
    db.commit()
    db.close()
    print(f"SUKSES: {jumlah_data} data unit berhasil dimasukkan ke PostgreSQL!")

if __name__ == "__main__":
    jalankan_import()