import openpyxl
from database import SessionLocal
import models

def jalankan_import():
    wb = openpyxl.load_workbook("nama_isi_surat.xlsx", data_only=True)
    ws = wb.active

    db = SessionLocal()
    berhasil = 0
    tidak_ketemu = []

    # Lewati baris pertama (header: "Nama Unit", "Teks Disurat")
    for row in ws.iter_rows(min_row=2, values_only=True):
        nama_unit_excel, teks_disurat = row[0], row[1]
        if not nama_unit_excel or not teks_disurat:
            continue

        nama_unit_excel = nama_unit_excel.strip()
        teks_disurat    = teks_disurat.strip()

        unit = db.query(models.UnitOrganisasi).filter(
            models.UnitOrganisasi.nama_unit == nama_unit_excel
        ).first()

        if unit:
            unit.jabatan_surat = teks_disurat
            berhasil += 1
        else:
            tidak_ketemu.append(nama_unit_excel)

    db.commit()
    db.close()

    print(f"✅ Berhasil update {berhasil} unit.")
    if tidak_ketemu:
        print(f"\n⚠️  {len(tidak_ketemu)} nama unit di Excel TIDAK ditemukan di database:")
        for nama in tidak_ketemu:
            print(f"   - {nama}")

if __name__ == "__main__":
    jalankan_import()