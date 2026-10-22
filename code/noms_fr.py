"""Noms français des sets, pour l'onglet « New ad » de LEGO HQ.

Sources, de la plus sûre à la moins sûre :
  1. le nom saisi dans l'application (db « noms_fr/<set> », source « toi ») ;
  2. le nom déjà utilisé dans une annonce Vinted (stock/app/annonces.json) ;
  3. la liste LEGO.com recopiée (data/lego_officiel.csv) ;
  4. le nom officiel trouvé sur lego.com/fr-fr par une recherche web lors d'un
     passage (db « noms_fr/<set> », source « lego.com »).
Sans nom connu, l'application écrit « noms_fr/<set> » {demande: true} et le
passage suivant le cherche.

Usage :
  python3 noms_fr.py                  reconstruit stock/app/noms_fr.json
  python3 noms_fr.py demandes [N]     sets à chercher : demandes de l'appli, articles
                                      « à traiter » (colis reçus d'abord), puis jusqu'à
                                      N autres sets en stock sans nom (défaut 0)
  python3 noms_fr.py ecrire SET "Nom" enregistre le nom officiel trouvé
"""
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import base

ICI = Path(__file__).resolve().parent
LEGO = ICI.parent
SORTIE = LEGO / "stock" / "app" / "noms_fr.json"


def depuis_annonces() -> dict:
    out = {}
    f = LEGO / "stock" / "app" / "annonces.json"
    if not f.exists():
        return out
    for a in json.load(open(f)):
        m = re.search(r"Vends (?:le )?set LEGO (\d{3,7}) - (.+?) de la gamme (.+?)\.?\n", a.get("d") or "")
        if not m:
            continue
        nom, en = m.group(2).strip(), None
        mm = re.match(r"(.+) \((.+)\)$", nom)
        if mm:
            nom, en = mm.group(1).strip(), mm.group(2).strip()
        gamme = re.sub(r"^LEGO\s+", "", m.group(3).strip(), flags=re.I)
        gamme = re.sub(r"\s+-\s+\d{4}$", "", gamme)
        out[m.group(1)] = {"nom": nom, "en": en, "gamme": gamme, "source": "annonce"}
    return out


def depuis_lego() -> dict:
    f = LEGO / "data" / "lego_officiel.csv"
    if not f.exists():
        return {}
    return {r["set"]: {"nom": r["nom"].replace("’", "'").strip(), "source": "lego.com"}
            for r in csv.DictReader(open(f)) if r.get("nom")}


def depuis_appli() -> dict:
    out = {}
    for x in base.docs("noms_fr/") or []:
        d = x.get("data") or {}
        if d.get("nom"):
            out[x["chemin"].split("/", 1)[1]] = {"nom": d["nom"], "source": d.get("source") or "toi"}
    return out


def construire() -> dict:
    noms = depuis_lego()
    for s, v in depuis_annonces().items():
        noms[s] = {**noms.get(s, {}), **{k: x for k, x in v.items() if x}}
    for s, v in depuis_appli().items():
        noms[s] = {**noms.get(s, {}), **v}
    SORTIE.write_text(json.dumps(noms, ensure_ascii=False, separators=(",", ":")))
    return noms


def demandes(n_stock: int = 0) -> list[str]:
    noms = json.loads(SORTIE.read_text()) if SORTIE.exists() else construire()
    att = [x["chemin"].split("/", 1)[1] for x in base.docs("noms_fr/") or []
           if (x.get("data") or {}).get("demande") and not (x.get("data") or {}).get("nom")]
    # Articles « à traiter » (pas encore en vente) : colis reçus d'abord, puis en livraison
    arts = base.lire("articles", "select=id_set,livre&statut=eq.%C3%A0%20traiter&categorie=eq.SET&order=livre.desc,id.desc") or []
    for x in arts:
        s = re.sub(r"-\d+$", "", str(x.get("id_set") or "")).strip()
        if re.fullmatch(r"\d{3,7}", s) and s not in noms and s not in att:
            att.append(s)
    if n_stock:
        apub = LEGO / "stock" / "app" / "a_publier.json"
        stock = json.load(open(apub)) if apub.exists() else {}
        att += [s for s in stock if s not in noms and s not in att][:n_stock]
    return att


def ecrire(s: str, nom: str, source: str = "lego.com"):
    nom = nom.replace("’", "'").replace("™", "").replace("®", "").strip()
    if not base.doc_ecrire("noms_fr/" + s, {"nom": nom, "source": source, "le": dt.datetime.now(dt.timezone.utc).isoformat()}):
        sys.exit("écriture refusée")
    construire()
    print(s, "→", nom)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(len(construire()), "noms français")
    elif a[0] == "demandes":
        print("\n".join(demandes(int(a[1]) if len(a) > 1 else 0)))
    elif a[0] == "ecrire" and len(a) >= 3:
        ecrire(a[1], a[2], a[3] if len(a) > 3 else "lego.com")
    else:
        sys.exit(__doc__)
