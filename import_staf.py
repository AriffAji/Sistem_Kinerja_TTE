import openpyxl
from database import SessionLocal
import models

def hash_password_simpel(password: str) -> str:
    # Sistem kamu menyimpan password apa adanya (plain text), sesuai proses_login yang ada
    return password

def jalankan_import():
    wb = openpyxl.load_workbook("Anggota_staff.xlsx", data_only=True)
    ws = wb.active

    db = SessionLocal()

    baru_ditambah = 0
    afiliasi_ditambah = 0
    dilewati_unit_tidak_ada = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        nip, password_excel, nama, jabatan, kode_unit, role_ppnp = row

        if not nip or not nama or not kode_unit:
            continue

        nip = str(nip).strip()
        nama = nama.strip()
        jabatan = (jabatan or "").strip()
        kode_unit = kode_unit.strip()
        role_ppnp = (role_ppnp or "staf").strip()

        # Pastikan unit tujuan memang ada di database
        unit_ada = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.kode_unit == kode_unit
        ).first()
        if not unit_ada:
            dilewati_unit_tidak_ada.append((nip, nama, kode_unit))
            continue

        pegawai_ada = db.query(models.Pegawai).filter(
            models.Pegawai.nip == nip
        ).first()

        if not pegawai_ada:
            # NIP belum pernah ada -> insert baru sebagai pegawai
            pegawai_baru = models.Pegawai(
                nip       = nip,
                password  = hash_password_simpel("123"),
                nama      = nama,
                jabatan   = jabatan,
                role_ppnp = role_ppnp,
                kode_unit = kode_unit,
            )
            db.add(pegawai_baru)
            db.flush()  # supaya langsung tersedia untuk pengecekan berikutnya
            baru_ditambah += 1

        else:
            # NIP sudah ada -> cek apakah kode_unit di baris ini SAMA dengan yang sudah tercatat
            if pegawai_ada.kode_unit == kode_unit:
                # Sama persis, tidak perlu apa-apa (data sudah representatif)
                continue
            else:
                # Beda unit -> catat sebagai afiliasi tambahan (kalau belum ada)
                afiliasi_ada = db.query(models.AfiliasiTambahan).filter(
                    models.AfiliasiTambahan.nip == nip,
                    models.AfiliasiTambahan.kode_unit_tambahan == kode_unit
                ).first()
                if not afiliasi_ada:
                    db.add(models.AfiliasiTambahan(
                        nip = nip,
                        kode_unit_tambahan = kode_unit,
                        jabatan_tambahan = jabatan,
                    ))
                    afiliasi_ditambah += 1

    db.commit()
    db.close()

    print(f"✅ Pegawai baru ditambahkan : {baru_ditambah}")
    print(f"✅ Afiliasi tambahan dicatat : {afiliasi_ditambah}")
    if dilewati_unit_tidak_ada:
        print(f"\n⚠️  {len(dilewati_unit_tidak_ada)} baris dilewati (kode_unit tidak ditemukan):")
        for nip, nama, ku in dilewati_unit_tidak_ada:
            print(f"   - {nip} | {nama} | kode_unit: {ku}")

if __name__ == "__main__":
    jalankan_import()