"""Photos des annonces pour l'application (onglet Listings).

Télécharge les photos de chaque annonce (URLs de l'export de l'extension),
les réduit à 800 px de large et les colle les unes sous les autres en
bandes de 7 photos au plus. Une bande = un seul fichier à envoyer dans
l'application, qui la redécoupe en photos sur le téléphone.

Usage : python3 photos_annonces.py   → ../stock/app/photos/ + photos.json
Déjà fait = sauté (même liste de photos).
"""
import hashlib
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

APP = Path(__file__).resolve().parent.parent / "stock" / "app"
DOSSIER = APP / "photos"
MANIFESTE = APP / "photos.json"
LARGEUR, PAR_BANDE, QUALITE = 800, 7, 80


def charger(url: str) -> Image.Image | None:
    for _ in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.ok:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            pass
    return None


def bandes(annonce: dict) -> dict | None:
    urls = annonce["ph"]
    cle = hashlib.sha1(" ".join(u.split("?")[0] for u in urls).encode()).hexdigest()[:12]
    images = [charger(u) for u in urls]
    images = [i for i in images if i]
    if not images:
        return None
    images = [i.resize((LARGEUR, round(i.height * LARGEUR / i.width)), Image.LANCZOS) for i in images]
    fichiers, cases = [], []
    for k in range(0, len(images), PAR_BANDE):
        lot = images[k:k + PAR_BANDE]
        bande = Image.new("RGB", (LARGEUR, sum(i.height for i in lot)), "white")
        y = 0
        for i in lot:
            bande.paste(i, (0, y))
            cases.append([len(fichiers), y, i.height])
            y += i.height
        nom = f"{annonce['id']}_{cle}_{len(fichiers)}.jpg"
        bande.save(DOSSIER / nom, quality=QUALITE, optimize=True, progressive=True)
        fichiers.append(nom)
    return {"cle": cle, "fichiers": fichiers, "cases": cases, "w": LARGEUR}


def main():
    DOSSIER.mkdir(parents=True, exist_ok=True)
    annonces = json.loads((APP / "annonces.json").read_text())
    man = json.loads(MANIFESTE.read_text()) if MANIFESTE.exists() else {}
    a_faire = []
    for a in annonces:
        cle = hashlib.sha1(" ".join(u.split("?")[0] for u in a["ph"]).encode()).hexdigest()[:12]
        m = man.get(a["id"])
        if a["ph"] and not (m and m["cle"] == cle and all((DOSSIER / f).exists() for f in m["fichiers"])):
            a_faire.append(a)
    print(f"{len(a_faire)} annonces à traiter sur {len(annonces)}", flush=True)
    with ThreadPoolExecutor(12) as ex:
        for n, (a, res) in enumerate(zip(a_faire, ex.map(bandes, a_faire)), 1):
            if res:
                ancien = man.get(a["id"])
                if ancien and ancien["cle"] != res["cle"]:
                    res["assets"] = {}
                man[a["id"]] = {**(ancien or {}), **res} if ancien and ancien["cle"] == res["cle"] else res
            if n % 50 == 0:
                MANIFESTE.write_text(json.dumps(man))
                print(n, flush=True)
    MANIFESTE.write_text(json.dumps(man))
    taille = sum(f.stat().st_size for f in DOSSIER.glob("*.jpg"))
    print(f"{len(man)} annonces avec photos, {sum(len(m['fichiers']) for m in man.values())} bandes, "
          f"{taille / 1e6:.0f} Mo", file=sys.stderr)


if __name__ == "__main__":
    main()
