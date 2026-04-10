"""
VocalCare AI Backend — v2.2
Tüm modellerde extra="ignore": Retell tüm call objesini gönderiyor, fazla alanlar yoksayılır.
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from database import init_db, db
from calendar_helper import (
    calendar_available,
    musait_slotlar,
    randevu_olustur,
    randevu_iptal,
    randevu_guncelle,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vocalcare.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("VocalCare AI başlatılıyor...")
    init_db()
    cal = calendar_available()
    logger.info(f"Google Calendar: {'bağlı' if cal else 'bağlı DEĞİL'}")
    yield
    logger.info("VocalCare AI kapatılıyor...")


app = FastAPI(title="VocalCare AI", version="2.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def ok(data: dict) -> dict:
    return {"success": True, **data}

def hata(mesaj: str, kod: int = 400) -> JSONResponse:
    logger.warning(f"Hata: {mesaj}")
    return JSONResponse(status_code=kod, content={"success": False, "error": mesaj})


class MusteriTanimaReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_number: Optional[str] = None
    phone_number: Optional[str] = None

    def get_telefon(self) -> str:
        t = self.from_number or self.phone_number or ""
        return t.strip().replace(" ", "").replace("-", "")


class MusteriKaydetReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: str
    ad: str
    soyad: Optional[str] = None
    hizmet_turu: Optional[str] = None
    notlar: Optional[str] = None


class MusteriGuncelleReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: Optional[str] = None
    from_number: Optional[str] = None
    ad: Optional[str] = None
    soyad: Optional[str] = None
    hizmet_turu: Optional[str] = None
    randevu_tarih: Optional[str] = None
    randevu_saat: Optional[str] = None

    def get_telefon(self) -> str:
        return (self.telefon or self.from_number or "").strip()


class RandevuOlusturReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: Optional[str] = None
    from_number: Optional[str] = None
    ad: str
    soyad: Optional[str] = None
    hizmet_turu: str
    tarih: str
    saat: str
    sure_dakika: Optional[int] = 60

    def get_telefon(self) -> str:
        return (self.telefon or self.from_number or "").strip()

    @field_validator("tarih")
    @classmethod
    def tarih_format(cls, v: str) -> str:
        try:
            from datetime import datetime
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Tarih formatı YYYY-MM-DD olmalı")
        return v

    @field_validator("saat")
    @classmethod
    def saat_format(cls, v: str) -> str:
        try:
            from datetime import datetime
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("Saat formatı HH:MM olmalı")
        return v


class RandevuIptalReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: Optional[str] = None
    from_number: Optional[str] = None
    ad: Optional[str] = None

    def get_telefon(self) -> str:
        return (self.telefon or self.from_number or "").strip()


class RandevuGuncelleReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: Optional[str] = None
    from_number: Optional[str] = None
    yeni_tarih: str
    yeni_saat: str

    def get_telefon(self) -> str:
        return (self.telefon or self.from_number or "").strip()


class MusaitSlotReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tarih: str
    sure_dakika: Optional[int] = 60
    calisma_baslangic: Optional[str] = "09:00"
    calisma_bitis: Optional[str] = "18:00"


class AramaLogReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    call_id: str
    telefon: Optional[str] = None
    from_number: Optional[str] = None
    ad: Optional[str] = None
    soyad: Optional[str] = None
    hizmet_turu: Optional[str] = None
    randevu_tarih: Optional[str] = None
    randevu_saat: Optional[str] = None
    basarili: Optional[bool] = None
    sentiment: Optional[str] = None
    ozet: Optional[str] = None
    recording_url: Optional[str] = None
    islem_turu: Optional[str] = None

    def get_telefon(self) -> str:
        return (self.telefon or self.from_number or "").strip()


class KaraListeReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: str
    neden: str


class TelefonModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    telefon: str


@app.post("/musteri-tanima")
def musteri_tanima(req: MusteriTanimaReq):
    telefon = req.get_telefon()
    logger.info(f"Müşteri tanıma: {telefon}")

    if not telefon:
        return hata("Telefon numarası eksik")

    try:
        with db() as c:
            c.execute("SELECT * FROM musteriler WHERE telefon = ?", (telefon,))
            row = c.fetchone()
    except Exception as e:
        logger.error(f"DB hatası: {e}")
        return hata("Veritabanı hatası", 500)

    if not row:
        return ok({"taninidi": False, "yeni_musteri": True, "kara_liste": False, "mesaj": "Yeni müşteri"})

    m = dict(row)

    if m["kara_liste"]:
        return ok({
            "taninidi": True, "yeni_musteri": False, "kara_liste": True,
            "kara_liste_neden": m["kara_liste_neden"], "ad": m["ad"],
            "mesaj": f"DİKKAT: {m['ad']} kara listede. Sebep: {m['kara_liste_neden']}"
        })

    return ok({
        "taninidi": True, "yeni_musteri": False, "kara_liste": False,
        "ad": m["ad"], "soyad": m["soyad"], "hizmet_turu": m["hizmet_turu"],
        "vip": bool(m["vip"]), "toplam_arama": m["toplam_arama"],
        "iptal_sayisi": m["iptal_sayisi"], "son_randevu_tarih": m["son_randevu_tarih"],
        "son_randevu_saat": m["son_randevu_saat"], "notlar": m["notlar"],
        "mesaj": f"Hoş geldiniz {m['ad']} Bey/Hanım" if m["ad"] else "Müşteri tanındı"
    })


@app.post("/musteri-kaydet")
def musteri_kaydet(req: MusteriKaydetReq):
    logger.info(f"Müşteri kaydet: {req.telefon}")
    try:
        with db() as c:
            c.execute("""
                INSERT INTO musteriler (telefon, ad, soyad, hizmet_turu, notlar)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telefon) DO UPDATE SET
                    ad=excluded.ad, soyad=excluded.soyad,
                    hizmet_turu=excluded.hizmet_turu, notlar=excluded.notlar
            """, (req.telefon, req.ad, req.soyad, req.hizmet_turu, req.notlar))
        return ok({"mesaj": "Müşteri kaydedildi"})
    except Exception as e:
        logger.error(f"Kaydet hatası: {e}")
        return hata("Kayıt hatası", 500)


@app.post("/musteri-guncelle")
def musteri_guncelle(req: MusteriGuncelleReq):
    telefon = req.get_telefon()
    logger.info(f"Müşteri güncelle: {telefon}")
    try:
        with db() as c:
            c.execute("SELECT id FROM musteriler WHERE telefon = ?", (telefon,))
            var = c.fetchone()
            if var:
                c.execute("""
                    UPDATE musteriler SET
                        ad=COALESCE(?,ad), soyad=COALESCE(?,soyad),
                        hizmet_turu=COALESCE(?,hizmet_turu),
                        son_randevu_tarih=COALESCE(?,son_randevu_tarih),
                        son_randevu_saat=COALESCE(?,son_randevu_saat),
                        toplam_arama=toplam_arama+1
                    WHERE telefon=?
                """, (req.ad, req.soyad, req.hizmet_turu, req.randevu_tarih, req.randevu_saat, telefon))
            else:
                c.execute("""
                    INSERT INTO musteriler
                        (telefon,ad,soyad,hizmet_turu,son_randevu_tarih,son_randevu_saat,toplam_arama)
                    VALUES (?,?,?,?,?,?,1)
                """, (telefon, req.ad, req.soyad, req.hizmet_turu, req.randevu_tarih, req.randevu_saat))
        return ok({"mesaj": "Güncellendi"})
    except Exception as e:
        logger.error(f"Güncelle hatası: {e}")
        return hata("Güncelleme hatası", 500)


@app.post("/musait-slotlar")
def musait_slotlar_endpoint(req: MusaitSlotReq):
    logger.info(f"Slot sorgusu: {req.tarih}")
    try:
        slotlar = musait_slotlar(
            tarih=req.tarih,
            sure_dakika=req.sure_dakika or 60,
            calisma_baslangic=req.calisma_baslangic or "09:00",
            calisma_bitis=req.calisma_bitis or "18:00",
        )
        return ok({
            "tarih": req.tarih,
            "musait_slotlar": slotlar,
            "toplam": len(slotlar),
            "mesaj": f"{req.tarih} tarihinde {len(slotlar)} boş saat var"
        })
    except EnvironmentError:
        return hata("Google Calendar bağlantısı ayarlanmamış", 503)
    except Exception as e:
        logger.error(f"Slot hatası: {e}")
        return hata("Takvim sorgulanamadı", 500)


@app.post("/randevu-olustur")
def randevu_olustur_endpoint(req: RandevuOlusturReq):
    telefon = req.get_telefon()
    ad_soyad = f"{req.ad} {req.soyad or ''}".strip()
    logger.info(f"Randevu oluştur: {ad_soyad} / {req.tarih} {req.saat}")
    try:
        sonuc = randevu_olustur(
            ad_soyad=ad_soyad, telefon=telefon, hizmet_turu=req.hizmet_turu,
            tarih=req.tarih, saat=req.saat, sure_dakika=req.sure_dakika or 60,
        )
        with db() as c:
            c.execute("""
                INSERT INTO musteriler (telefon,ad,soyad,hizmet_turu,son_randevu_tarih,son_randevu_saat,toplam_arama)
                VALUES (?,?,?,?,?,?,1)
                ON CONFLICT(telefon) DO UPDATE SET
                    ad=COALESCE(?,ad), soyad=COALESCE(?,soyad),
                    hizmet_turu=excluded.hizmet_turu,
                    son_randevu_tarih=excluded.son_randevu_tarih,
                    son_randevu_saat=excluded.son_randevu_saat
            """, (telefon, req.ad, req.soyad, req.hizmet_turu, req.tarih, req.saat, req.ad, req.soyad))
        return ok({"event_id": sonuc["event_id"], "tarih": req.tarih, "saat": req.saat,
                   "mesaj": f"Randevu oluşturuldu: {req.tarih} saat {req.saat}"})
    except EnvironmentError:
        return hata("Google Calendar bağlantısı ayarlanmamış", 503)
    except Exception as e:
        logger.error(f"Randevu oluştur hatası: {e}")
        return hata("Randevu oluşturulamadı", 500)


@app.post("/randevu-iptal")
def randevu_iptal_endpoint(req: RandevuIptalReq):
    telefon = req.get_telefon()
    logger.info(f"Randevu iptal: {telefon}")
    try:
        sonuc = randevu_iptal(telefon)
        if not sonuc["bulundu"]:
            return ok({"bulundu": False, "mesaj": "Gelecekte randevu bulunamadı"})
        with db() as c:
            c.execute("UPDATE musteriler SET iptal_sayisi=iptal_sayisi+1 WHERE telefon=?", (telefon,))
            c.execute("SELECT iptal_sayisi FROM musteriler WHERE telefon=?", (telefon,))
            row = c.fetchone()
            if row and row[0] >= 3:
                c.execute("""
                    UPDATE musteriler SET kara_liste=1, kara_liste_neden='3 veya daha fazla iptal'
                    WHERE telefon=? AND kara_liste=0
                """, (telefon,))
                logger.warning(f"Otomatik kara liste: {telefon}")
        return ok({"bulundu": True, "iptal_tarih": sonuc["tarih"], "iptal_saat": sonuc["saat"],
                   "mesaj": f"Randevu iptal edildi: {sonuc['tarih']} saat {sonuc['saat']}"})
    except EnvironmentError:
        return hata("Google Calendar bağlantısı ayarlanmamış", 503)
    except Exception as e:
        logger.error(f"İptal hatası: {e}")
        return hata("İptal sırasında hata oluştu", 500)


@app.post("/randevu-guncelle")
def randevu_guncelle_endpoint(req: RandevuGuncelleReq):
    telefon = req.get_telefon()
    logger.info(f"Randevu güncelle: {telefon} → {req.yeni_tarih} {req.yeni_saat}")
    try:
        sonuc = randevu_guncelle(telefon, req.yeni_tarih, req.yeni_saat)
        if not sonuc["bulundu"]:
            return ok({"bulundu": False, "mesaj": "Güncellenecek randevu bulunamadı"})
        with db() as c:
            c.execute("UPDATE musteriler SET son_randevu_tarih=?, son_randevu_saat=? WHERE telefon=?",
                      (req.yeni_tarih, req.yeni_saat, telefon))
        return ok({"bulundu": True, "yeni_tarih": req.yeni_tarih, "yeni_saat": req.yeni_saat,
                   "mesaj": f"Randevu güncellendi: {req.yeni_tarih} saat {req.yeni_saat}"})
    except EnvironmentError:
        return hata("Google Calendar bağlantısı ayarlanmamış", 503)
    except Exception as e:
        logger.error(f"Güncelle hatası: {e}")
        return hata("Güncelleme sırasında hata oluştu", 500)


@app.post("/arama-log")
def arama_log(req: AramaLogReq):
    telefon = req.get_telefon()
    logger.info(f"Arama log: {req.call_id} / {telefon}")
    try:
        with db() as c:
            c.execute("""
                INSERT OR REPLACE INTO arama_loglari
                    (call_id,telefon,ad,soyad,hizmet_turu,randevu_tarih,randevu_saat,
                     basarili,sentiment,ozet,recording_url,islem_turu)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (req.call_id, telefon, req.ad, req.soyad, req.hizmet_turu,
                  req.randevu_tarih, req.randevu_saat, 1 if req.basarili else 0,
                  req.sentiment, req.ozet, req.recording_url, req.islem_turu))
        return ok({"mesaj": "Log kaydedildi"})
    except Exception as e:
        logger.error(f"Log hatası: {e}")
        return hata("Log kaydedilemedi", 500)


@app.post("/kara-listeye-al")
def kara_listeye_al(req: KaraListeReq):
    try:
        with db() as c:
            c.execute("UPDATE musteriler SET kara_liste=1, kara_liste_neden=? WHERE telefon=?",
                      (req.neden, req.telefon))
        return ok({"mesaj": f"{req.telefon} kara listeye alındı"})
    except Exception as e:
        logger.error(f"Kara liste hatası: {e}")
        return hata("İşlem hatası", 500)


@app.post("/kara-listeden-cikar")
def kara_listeden_cikar(req: TelefonModel):
    try:
        with db() as c:
            c.execute("UPDATE musteriler SET kara_liste=0, kara_liste_neden=NULL WHERE telefon=?",
                      (req.telefon,))
        return ok({"mesaj": "Kara listeden çıkarıldı"})
    except Exception as e:
        logger.error(f"Kara liste çıkar hatası: {e}")
        return hata("İşlem hatası", 500)


@app.get("/")
def saglik():
    cal = calendar_available()
    return {
        "status": "calisıyor",
        "versiyon": "2.2.0",
        "google_calendar": "bagli" if cal else "baglanmadi",
        "endpointler": [
            "POST /musteri-tanima", "POST /musteri-kaydet", "POST /musteri-guncelle",
            "POST /musait-slotlar", "POST /randevu-olustur", "POST /randevu-iptal",
            "POST /randevu-guncelle", "POST /arama-log",
            "POST /kara-listeye-al", "POST /kara-listeden-cikar",
        ]
    }
