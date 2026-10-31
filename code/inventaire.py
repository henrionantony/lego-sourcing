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


def decoupes(annonces: dict):
    """Bouton « ✂ Part-out » de l'onglet Stock (collection « decoupes » de l'application) :
    un exemplaire en stock de ce set passe en stratégie LOT/DETAILS dans la base (il apparaît alors
    dans l'onglet Part-out). « Keep as whole set » remet sa stratégie d'avant. Le lien bouton →
    article est gardé dans le fichier partagé « decoupes_base » pour ne jamais en changer deux."""
    lien = base.doc("fichiers/decoupes_base") or {}
    docs = base.docs("decoupes/") or []
    if not docs and not lien:
        return
    stock = base.lire("stock", "select=id,id_set,etat,prix_achat,strategie&reste=gt.0&categorie=eq.SET&limit=10000") or []
    change = 0
    for d in docs:
        cle, on = d["chemin"].removeprefix("decoupes/"), bool((d.get("data") or {}).get("on"))
        deja = lien.get(cle)
        if on and not deja:
            s = id_set(cle, annonces)
            pris = {x["art"] for x in lien.values()}
            libres = [l for l in stock if s and str(l.get("id_set") or "").split("-")[0].removesuffix(".0") == s
                      and l["id"] not in pris and "LOT" not in str(l.get("strategie") or "")]
            if not libres:
                continue
            # d'occasion d'abord (souvent incomplet), puis le moins cher
            l = sorted(libres, key=lambda x: (x.get("etat") == "NEUF", float(x.get("prix_achat") or 0)))[0]
            base.modifier("articles", f"id=eq.{int(l['id'])}", {"strategie": "LOT/DETAILS"})
            lien[cle] = {"art": l["id"], "avant": l.get("strategie")}
            change += 1
        elif not on and deja:
            base.modifier("articles", f"id=eq.{int(deja['art'])}", {"strategie": deja.get("avant")})
            del lien[cle]
            change += 1
    if change:
        base.doc_ecrire("fichiers/decoupes_base", lien)
        print(f"{change} stratégie(s) part-out mise(s) à jour")


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
    try:
        decoupes(annonces)
    except Exception as e:  # le bouton Part-out attendra le passage suivant
        print("part-out (stratégie) :", type(e).__name__)


if __name__ == "__main__":
    main(sys.argv[1])
