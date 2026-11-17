"""Lignes d'achat pour l'inventaire des minifigs (LEGO HQ, Scan › Minifigures) :
stock/app/figart.json = {article_id: [achat_id, libellé, quantité, catégorie]}.
Données privées : injectées dans static_page.json, jamais dans le dépôt."""
import json
from pathlib import Path

import base

SORTIE = Path("/mnt/project-files/lego/stock/app/figart.json")


def main():
    arts = base.lire("articles", "select=id,achat_id,id_set,designation,quantite,categorie&order=id.asc&limit=5000")
    if not arts:
        print("figart : base injoignable, fichier gardé")
        return
    d = {}
    for a in arts:
        lib = (a.get("designation") or a.get("id_set") or a.get("categorie") or "").strip()
        lib = lib.removeprefix("LEGO ").strip()
        d[str(a["id"])] = [a.get("achat_id"), lib[:70], int(a.get("quantite") or 1), a.get("categorie") or ""]
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    print(f"figart : {len(d)} lignes")


if __name__ == "__main__":
    main()
