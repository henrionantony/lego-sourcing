"""Minifigs dont on a déjà des photos d'annonce (06/11 : au scan, savoir lesquelles
sont à prendre en photo). Sortie privée stock/app/figs_photos.json = {cle: [annonces avec
photos sauvegardées, annonces vues sans photo sauvegardée, statut de la plus récente]}.
cle = code BrickLink sans zéros de tête (figs_sets.cle). Injecté en __FIGPHOTOS__."""
import csv
import json
import re
from pathlib import Path

from figs_sets import cle

STOCK = Path("/mnt/project-files/lego/stock")
SORTIE = STOCK / "app/figs_photos.json"
CODE = re.compile(r"\b([a-z]{2,5}\d{2,4}[a-z]?)\b", re.I)


def main():
    photos = json.loads((STOCK / "app/photos.json").read_text()) if (STOCK / "app/photos.json").exists() else {}
    titres = {}  # id annonce -> (titre, statut), export le plus récent gagne
    for f in sorted((STOCK / "vinted").glob("vinted_annonces_*.csv")):
        if f.name.endswith(".annule"):
            continue
        with open(f, encoding="utf-8-sig", newline="") as h:
            for r in csv.DictReader(h, delimiter=";"):
                if r.get("id"):
                    titres[r["id"]] = (r.get("titre") or "", (r.get("statut") or "").strip())
    for x in json.loads((STOCK / "app/annonces.json").read_text()):
        titres[str(x["id"])] = (x.get("t") or "", x.get("s") or "")
    d = {}
    for i, (t, st) in titres.items():
        for c in {cle(m) for m in CODE.findall(t) if not m.isdigit()}:
            e = d.setdefault(c, [0, 0, ""])
            e[0 if i in photos else 1] += 1
            if i in photos or not e[2]:
                e[2] = st
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    print(f"figs_photos : {len(d)} codes, {sum(1 for e in d.values() if e[0])} avec photos")


if __name__ == "__main__":
    main()
