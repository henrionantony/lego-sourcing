"""Prix BrickLink : ventes conclues en Europe sur les 6 derniers mois.

API Store de BrickLink (OAuth 1.0, gratuite avec un compte). Le « price
guide » donne, pour un set neuf, le prix moyen des ventes conclues
(guide_type=sold) ou des annonces en cours (guide_type=stock), filtré sur
l'Europe et converti en euros. Clés : BRICKLINK_CONSUMER_KEY,
BRICKLINK_CONSUMER_SECRET, BRICKLINK_TOKEN, BRICKLINK_TOKEN_SECRET (jeton
créé avec l'IP 0.0.0.0, l'adresse du cloud changeant). Limite BrickLink :
5 000 appels par jour ; cache disque de 7 jours.

Usage : python3 bricklink.py 10312-1 75365-1
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
URL = "https://api.bricklink.com/api/store/v1"


def _q(s) -> str:
    return urllib.parse.quote(str(s), safe="~")


class BrickLink:
    def __init__(self):
        e = os.environ
        self.cles = [e.get(k) for k in ("BRICKLINK_CONSUMER_KEY", "BRICKLINK_CONSUMER_SECRET",
                                         "BRICKLINK_TOKEN", "BRICKLINK_TOKEN_SECRET")]
        self.f_cache = CACHE / "bricklink.json"
        self.cache = json.loads(self.f_cache.read_text()) if self.f_cache.exists() else {}
        self.erreur = None  # première erreur d'accès, pour la signaler une fois

    def actif(self) -> bool:
        return all(self.cles) and self.erreur is None

    def _get(self, chemin: str, params: dict) -> dict:
        ck, cs, tk, ts = self.cles
        url = URL + chemin
        oauth = {"oauth_consumer_key": ck, "oauth_token": tk, "oauth_signature_method": "HMAC-SHA1",
                 "oauth_timestamp": str(int(time.time())), "oauth_nonce": uuid.uuid4().hex,
                 "oauth_version": "1.0"}
        base = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted({**params, **oauth}.items()))
        signe = "&".join(["GET", _q(url), _q(base)])
        oauth["oauth_signature"] = base64.b64encode(
            hmac.new(f"{_q(cs)}&{_q(ts)}".encode(), signe.encode(), hashlib.sha1).digest()).decode()
        complet = f"{url}?{urllib.parse.urlencode(params)}&Authorization={_q(json.dumps(oauth))}"
        with urllib.request.urlopen(complet, timeout=30) as r:
            d = json.load(r)
        if d.get("meta", {}).get("code") != 200:
            raise RuntimeError(d.get("meta", {}).get("description") or d.get("meta"))
        return d["data"]

    def prix(self, set_num: str, guide: str = "sold", max_age_jours: int = 7) -> dict | None:
        """Prix neuf en Europe. guide='sold' (ventes 6 mois) ou 'stock' (annonces)."""
        cle = f"{set_num}|{guide}"
        c = self.cache.get(cle)
        if c and (dt.date.today() - dt.date.fromisoformat(c["date"])).days <= max_age_jours:
            return c
        if not self.actif():
            return c
        try:
            d = self._get(f"/items/SET/{set_num}/price",
                          {"guide_type": guide, "new_or_used": "N", "region": "europe",
                           "currency_code": "EUR", "vat": "Y"})
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            msg = str(e)
            if "NOT_FOUND" in msg or "404" in msg:
                c = {"date": dt.date.today().isoformat(), "inconnu": True}
            else:
                # Erreur d'accès (clé, IP, quota) : on arrête d'appeler.
                self.erreur = msg
                print(f"BrickLink indisponible : {msg}", file=sys.stderr)
                return c
        else:
            c = {"date": dt.date.today().isoformat(),
                 "moyenne": float(d.get("avg_price") or 0) or None,
                 "moyenne_qte": float(d.get("qty_avg_price") or 0) or None,
                 "min": float(d.get("min_price") or 0) or None,
                 "max": float(d.get("max_price") or 0) or None,
                 "lots": d.get("unit_quantity"), "quantite": d.get("total_quantity")}
        self.cache[cle] = c
        self.f_cache.write_text(json.dumps(self.cache))
        return c


def valeur(c: dict | None, min_ventes: int = 3):
    """Prix de vente BrickLink retenu : moyenne pondérée par les quantités,
    seulement s'il y a assez de ventes pour qu'elle veuille dire quelque chose."""
    if not c or c.get("inconnu") or (c.get("quantite") or 0) < min_ventes:
        return None
    return c.get("moyenne_qte") or c.get("moyenne")


if __name__ == "__main__":
    bl = BrickLink()
    for s in sys.argv[1:]:
        print(s, bl.prix(s), bl.prix(s, "stock"))
