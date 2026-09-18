from database import SessionLocal
from models import UnitOrganisasi, Pegawai

def buat_akun_pegawai():
    db = SessionLocal()
    
    # 1. Bersihkan data pegawai lama jika script dijalankan ulang
    db.query(Pegawai).delete()
    
    # 2. Ambil semua pimpinan dari tabel unit_organisasi
    units = db.query(UnitOrganisasi).filter(UnitOrganisasi.nip_pimpinan.isnot(None)).all()
    
    jumlah_akun = 0
    for u in units:
        # Lewati jika NIP kosong atau strip (-)
        if not u.nip_pimpinan or u.nip_pimpinan.strip() == '-' or u.nip_pimpinan.strip() == '':
            continue
            
        # Menentukan Role sederhana berdasarkan Nama Unit dan Level
        role = "pimpinan"
        nama_unit_lower = u.nama_unit.lower()
        
        if "direktur" in nama_unit_lower and u.level == 1:
            role = "direktur"
        elif "bagian" in nama_unit_lower and u.level == 2:
            role = "kabag"
        elif "jurusan" in nama_unit_lower and u.level == 3:
            role = "kajur"
        
        # Buat entri Pegawai
        pegawai = Pegawai(
            nip=u.nip_pimpinan.strip(),
            password="123", # Password default untuk semua pimpinan
            nama=u.nama_pimpinan.strip(),
            jabatan=f"Pimpinan {u.nama_unit}",
            role_ppnp=role,
            kode_unit=u.kode_unit
        )
        
        # Cek duplikat NIP (karena 1 orang bisa merangkap jabatan)
        existing = db.query(Pegawai).filter_by(nip=pegawai.nip).first()
        if not existing:
            db.add(pegawai)
            jumlah_akun += 1
            
    # 3. Tambahkan 1 Akun Staf Biasa (Level 4) untuk Testing Pembuat Surat
    staf_dummy = Pegawai(
        nip="199001012026011001",
        password="123",
        nama="Staf Tester Pengelola Kepegawaian",
        jabatan="Staf Administrasi Kepegawaian",
        role_ppnp="staf",
        kode_unit="D003.1.2" # D003.1.2 adalah Tim Kerja Kepegawaian (Level 4)
    )
    if not db.query(Pegawai).filter_by(nip=staf_dummy.nip).first():
        db.add(staf_dummy)
        jumlah_akun += 1
        
    db.commit()
    db.close()
    
    print(f"SUKSES: {jumlah_akun} akun Pegawai berhasil dibuat!")
    print("--------------------------------------------------")
    print("Info Akun Testing:")
    print("1. DIREKTUR -> NIP: 196703271994031002 | Pass: 123")
    print("2. STAF     -> NIP: 199001012026011001 | Pass: 123")
    print("*(Semua pimpinan lain passwordnya juga 123)*")

if __name__ == "__main__":
    buat_akun_pegawai()