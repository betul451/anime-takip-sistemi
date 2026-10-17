"""
AniList GraphQL API -> SQL Server (AnimeTakipDB)
Doldurdugu tablolar: Studyo, Tur, Anime, Anime_Tur, Karakter, Anime_Karakter, Etiket, Anime_Etiket

Once veritabani/yama_etiket.sql dosyasini SSMS'te calistirmis olman gerekir.
Tekrar calistirmak guvenli: veritabaninda zaten olan animeleri atlar.
"""
import re
import time
import requests
import pyodbc

# ================== AYARLAR ==================
SERVER = "localhost"          # SSMS'te baglanirken yazdigin sunucu adi
DATABASE = "AnimeTakipDB"
DRIVER = "ODBC Driver 17 for SQL Server"  # pyodbc.drivers() ile gordugun isim

SAYFA_SAYISI = 4       # 1 sayfa = 50 anime. Once 1 ile dene, calisinca 4 yap (200 anime)
KARAKTER_SAYISI = 10    # her anime icin kac karakter
ETIKET_SAYISI = 8       # her anime icin kac etiket
# ============================================================================

URL = "https://graphql.anilist.co"

LISTE_SORGU = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: POPULARITY_DESC) {
      id
      title { romaji english native }
      format
      episodes
      status
      startDate { year month day }
      description(asHtml: false)
      averageScore
      coverImage { large }
      genres
      studios(isMain: true) { nodes { id name } }
      tags { id name category rank isMediaSpoiler }
    }
  }
}
"""

KARAKTER_SORGU = """
query ($id: Int, $perPage: Int) {
  Media(id: $id, type: ANIME) {
    characters(sort: [ROLE, RELEVANCE, ID], perPage: $perPage) {
      edges { role node { id name { full } image { large } } }
    }
  }
}
"""


def baglan():
    cs = (f"DRIVER={{{DRIVER}}};SERVER={SERVER};DATABASE={DATABASE};"
          "Trusted_Connection=yes;TrustServerCertificate=yes;")
    return pyodbc.connect(cs)


def sorgu(query, variables):
    """AniList'e istek atar. Limit asilirsa bekler, hata olursa tekrar dener."""
    for _ in range(6):
        time.sleep(2.5)  # AniList su an dakikada ~30 istege izin veriyor
        try:
            r = requests.post(URL, json={"query": query, "variables": variables},
                              headers={"Content-Type": "application/json",
                                       "Accept": "application/json"}, timeout=30)
        except requests.RequestException as e:
            print("  baglanti hatasi, tekrar deneniyor:", e)
            time.sleep(5)
            continue
        if r.status_code == 200:
            j = r.json()
            if j.get("data"):
                return j["data"]
            print("  sorgu hatasi:", j.get("errors"))
            return None
        if r.status_code == 429:
            bekle = int(r.headers.get("Retry-After", 60))
            print(f"  limit doldu, {bekle} sn bekleniyor")
            time.sleep(bekle + 1)
            continue
        print("  hata kodu", r.status_code, r.text[:150])
        time.sleep(5)
    return None


def temizle(metin):
    """Aciklamadaki <br>, <i> gibi HTML etiketlerini temizler."""
    if not metin:
        return None
    metin = re.sub(r"<br\s*/?>", "\n", metin)
    return re.sub(r"<[^>]+>", "", metin).strip()


def tarih(sd):
    if not sd or not sd.get("year"):
        return None
    return f"{sd['year']:04d}-{(sd['month'] or 1):02d}-{(sd['day'] or 1):02d}"


def turleri_yukle(cur):
    cur.execute("SELECT Ad, TurID FROM Tur")
    return {satir[0]: satir[1] for satir in cur.fetchall()}


def anime_kaydet(cur, m, tur_cache):
    aid = m["id"]
    t = m["title"]
    baslik = t.get("romaji") or t.get("english") or t.get("native")
    puan = m["averageScore"] / 10 if m.get("averageScore") is not None else None

    nodes = (m.get("studios") or {}).get("nodes") or []
    stud = nodes[0] if nodes else None
    if stud:
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM Studyo WHERE StudyoID=?) "
            "INSERT INTO Studyo (StudyoID, Ad) VALUES (?, ?)",
            stud["id"], stud["id"], stud["name"])

    cur.execute(
        "INSERT INTO Anime (AnimeID, Baslik, BaslikJaponca, Format, BolumSayisi, Durum, "
        "YayinBaslangic, Ozet, MalPuani, PosterURL, StudyoID) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        aid, baslik, t.get("native"), m.get("format"), m.get("episodes"), m.get("status"),
        tarih(m.get("startDate")), temizle(m.get("description")), puan,
        (m.get("coverImage") or {}).get("large"), stud["id"] if stud else None)

    # Turler (AniList'te tur id'si yok, kendimiz numara veriyoruz)
    for g in m.get("genres") or []:
        if g not in tur_cache:
            yeni = max(tur_cache.values(), default=0) + 1
            cur.execute("INSERT INTO Tur (TurID, Ad) VALUES (?, ?)", yeni, g)
            tur_cache[g] = yeni
        cur.execute("INSERT INTO Anime_Tur (AnimeID, TurID) VALUES (?, ?)", aid, tur_cache[g])

    # Etiketler (spoiler olmayanlardan en yuksek oylu ilk N tanesi)
    etiketler = [e for e in (m.get("tags") or []) if not e.get("isMediaSpoiler")]
    etiketler.sort(key=lambda e: e.get("rank") or 0, reverse=True)
    for e in etiketler[:ETIKET_SAYISI]:
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM Etiket WHERE EtiketID=?) "
            "INSERT INTO Etiket (EtiketID, Ad, Kategori) VALUES (?, ?, ?)",
            e["id"], e["id"], e["name"], e.get("category"))
        cur.execute("INSERT INTO Anime_Etiket (AnimeID, EtiketID, Oy) VALUES (?, ?, ?)",
                    aid, e["id"], e.get("rank"))


def karakter_kaydet(cur, aid):
    veri = sorgu(KARAKTER_SORGU, {"id": aid, "perPage": KARAKTER_SAYISI})
    if not veri or not veri.get("Media"):
        return 0
    adet = 0
    for edge in veri["Media"]["characters"]["edges"]:
        c = edge["node"]
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM Karakter WHERE KarakterID=?) "
            "INSERT INTO Karakter (KarakterID, Ad, ResimURL) VALUES (?, ?, ?)",
            c["id"], c["id"], c["name"]["full"], (c.get("image") or {}).get("large"))
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM Anime_Karakter WHERE AnimeID=? AND KarakterID=?) "
            "INSERT INTO Anime_Karakter (AnimeID, KarakterID, Rol) VALUES (?, ?, ?)",
            aid, c["id"], aid, c["id"], edge.get("role"))
        adet += 1
    return adet


def main():
    conn = baglan()
    cur = conn.cursor()
    tur_cache = turleri_yukle(cur)
    toplam = 0

    for sayfa in range(1, SAYFA_SAYISI + 1):
        print(f"--- Sayfa {sayfa}/{SAYFA_SAYISI} ---")
        veri = sorgu(LISTE_SORGU, {"page": sayfa, "perPage": 50})
        if not veri:
            print("Sayfa alinamadi, atlaniyor.")
            continue

        for m in veri["Page"]["media"]:
            aid = m["id"]
            ad = m["title"].get("romaji") or m["title"].get("english")
            cur.execute("SELECT COUNT(*) FROM Anime WHERE AnimeID=?", aid)
            if cur.fetchone()[0] > 0:
                print(f"[atla] {ad} zaten var")
                continue
            try:
                anime_kaydet(cur, m, tur_cache)
                k = karakter_kaydet(cur, aid)
                conn.commit()  # her anime bittiginde kaydet
                toplam += 1
                print(f"[ok] {ad}  (karakter: {k})")
            except Exception as ex:
                conn.rollback()
                tur_cache = turleri_yukle(cur)
                print(f"[HATA] {ad}: {ex}")

    conn.close()
    print(f"\nBitti. {toplam} yeni anime eklendi.")


if __name__ == "__main__":
    main()