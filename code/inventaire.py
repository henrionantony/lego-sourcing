"""Recopie dans la base (table « inventaire ») les comptages faits dans l'application,
à la main dans Stock ou avec l'onglet Scan. La session lit la collection « comptes »
de l'application dans un dossier (un fichier JSON par élément), puis lance ce script.

Usage : python3 inventaire.py <dossier contenant comptes/*.json>
"""
import json
import sys
from pathlib import Path

import base

ICI = Path(__file__).resolve().parent
ANNONCES = ICI.parent / "stock" / "app" / "annonces.json"


def id_set(cle: str, annonces: dict) -> str | None:
    """Numéro de set d'un élément de l'application : « b<set> », « v<annonce Vinted> »."""
    if cle.startswith("b"):
        return cle[1:]
    if cle.startswith("v"):
        return annonces.get(cle[1:])
    return None


def main(dossier: str):
    # Numéros de set des annonces : depuis l'export Vinted (cloud), sinon depuis la base (GitHub)
    if ANNONCES.exists():
        annonces = {a["id"]: a.get("n") for a in json.loads(ANNONCES.read_text())}
        base.doc_ecrire("fichiers/annonces_sets", {"v": annonces})
    else:
        annonces = (base.doc("fichiers/annonces_sets") or {}).get("v") or {}
    lignes = []
    for f in sorted((Path(dossier) / "comptes").glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("n") is None or not d.get("maj"):
            continue
        lignes.append({"id": f.stem, "id_set": id_set(f.stem, annonces), "n": int(d["n"]), "compte_le": d["maj"]})
    base.ajouter("inventaire", lignes, fusion=True)
    print(f"{len(lignes)} comptages recopiés dans la base")


if __name__ == "__main__":
    main(sys.argv[1])
