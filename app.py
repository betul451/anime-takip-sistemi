from flask import Flask, render_template, request
import pyodbc

app = Flask(__name__)

# SQL Server Bağlantı Bilgisi (Eğer sunucu adın farklıysa SERVER='localhost' yap)
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=AnimeTakipDB;"
    "Trusted_Connection=yes;"
)

@app.route("/")
def index():
    arama_kelimesi = request.args.get("q", "")
    conn = pyodbc.connect(CONN_STR)
    cursor = conn.cursor()

    if arama_kelimesi:
        # Arama yapıldığında animeleri filtrele
        sorgu = """
            SELECT AnimeID, Baslik, Format, BolumSayisi, MalPuani, PosterURL 
            FROM Anime 
            WHERE Baslik LIKE ? 
            ORDER BY MalPuani DESC
        """
        cursor.execute(sorgu, f"%{arama_kelimesi}%")
    else:
        # Ana sayfada en yüksek puanlı ilk 50 animeyi çek
        sorgu = """
            SELECT TOP 50 AnimeID, Baslik, Format, BolumSayisi, MalPuani, PosterURL 
            FROM Anime 
            ORDER BY MalPuani DESC
        """
        cursor.execute(sorgu)

    animeler = cursor.fetchall()
    conn.close()
    
    return render_template("index.html", animeler=animeler, arama=arama_kelimesi)

if __name__ == "__main__":
    app.run(debug=True, port=5000)