with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

tambahan = """

class SetPosisiTTDRequest(BaseModel):
    posisi_list : List[PosisiTTDItem]
    mode_paralel: bool = False
"""

cari = "class SetPosisiNomorRequest(BaseModel):\n    posisi_list: List[PosisiNomorItem]"
ganti = cari + tambahan

if cari in content:
    content = content.replace(cari, ganti)
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Berhasil ditambahkan!")
else:
    print("Teks tidak ditemukan, cek manual!") 
