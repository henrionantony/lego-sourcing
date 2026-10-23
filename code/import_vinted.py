"""Imports d'exports Vinted faits depuis l'application (Platforms › My listings).

L'application range le fichier (db imports_csv/<clé>) et son résumé (imports_log/<clé>,
etat « en attente »). Ce passage revérifie le fichier avec les mêmes règles que
l'écran d'import, puis remplace la liste des annonces : stock/vinted/ reçoit le fichier,
annonces.py, leboncoin.py, les photos et la publication suivent. Il ne touche jamais aux
achats, ventes, stock ni trésorerie.
« Annuler le dernier import » (etat « à annuler ») met le fichier de côté (.annule) et
reconstruit avec l'export d'avant.

Usage : python3 import_vinted.py   → affiche « rien », ou ce qui a été fait (puis publier)
"""
import csv
import datetime as dt
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import base

ICI = Path(__file__).resolve().parent
VINTED = ICI.parent / "stock" / "vinted"
APP = ICI.parent / "stock" / "app"
SEUIL_DISPARUES = 0.10
COLONNES = {"id", "titre", "prix", "statut", "url"}


def _cle(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def apparier(parties: list[dict], nouvelles: list[dict]) -> list[dict]:
    """Republications : une annonce disparue et une nouvelle au même titre (sinon au même set,
    s'il n'y en a qu'une de chaque côté). Mêmes règles que l'écran d'import."""
    from annonces import numero  # noqa: E402  (numéro de set lu dans le titre)
    pris, paires = set(), []
    nv = [{"id": x["id"].strip(), "t": x["titre"], "p": float(x["prix"] or 0)} for x in nouvelles]
    for x in nv + [{"id": a["id"], "t": a["t"], "p": a["p"]} for a in parties]:
        x["n"] = numero(x["t"]) or ""
    old = [{"id": a["id"], "t": a["t"], "p": float(a.get("p") or 0), "n": numero(a["t"]) or ""} for a in parties]

    def passe(cle):
        for x in nv:
            if x["id"] in pris or not cle(x, nv, old):
                continue
            k = cle(x, nv, old)
            c = [a for a in old if a["id"] not in pris and cle(a, nv, old) == k]
            if c:
                a = min(c, key=lambda a: abs(a["p"] - x["p"]))
                pris.update((x["id"], a["id"]))
                paires.append({"ancien": a["id"], "nouveau": x["id"], "t": x["t"], "avant": a["p"], "p": x["p"], "n": x["n"] or a["n"]})

    passe(lambda o, nv, old: _cle(o["t"]))

    def seul(o, nv, old):
        n = o["n"]
        ok = n and sum(1 for x in nv if x["id"] not in pris and x["n"] == n) == 1 \
            and sum(1 for a in old if a["id"] not in pris and a["n"] == n) == 1
        return n if ok else ""
    passe(seul)
    return paires


def noter_republications(paires: list[dict]):
    """Historique pour l'étude « la republication fait-elle vendre plus vite / plus cher ? »."""
    if not paires:
        return
    f = VINTED / "republications.csv"
    neuf = not f.exists()
    with f.open("a", encoding="utf-8", newline="") as h:
        w = csv.writer(h, delimiter=";")
        if neuf:
            w.writerow(["date", "ancien", "nouveau", "set", "titre", "prix_avant", "prix"])
        for p in paires:
            w.writerow([dt.date.today().isoformat(), p["ancien"], p["nouveau"], p["n"], p["t"], p["avant"], p["p"]])


def verifier(texte: str, force: bool) -> tuple[str | None, dict]:
    """(motif de refus ou None, résumé)."""
    lignes = list(csv.DictReader(io.StringIO(texte.lstrip("﻿")), delimiter=";"))
    if not lignes or not COLONNES <= set(lignes[0].keys()):
        return "colonnes de l'extension absentes", {}
    lignes = [r for r in lignes if re.fullmatch(r"\d+", (r.get("id") or "").strip())]
    ids = [r["id"].strip() for r in lignes]
    if len(ids) != len(set(ids)):
        return "une annonce apparaît deux fois", {}
    vivant = {r["id"].strip() for r in lignes if r["statut"] in ("en ligne", "masqué")}
    vendu = {r["id"].strip() for r in lignes if r["statut"] == "vendu"}
    anciennes = {a["id"]: a for a in json.loads((APP / "annonces.json").read_text())}
    avant = set(anciennes)
    nouvelles = {r["id"].strip(): r for r in lignes if r["id"].strip() in vivant - avant}
    paires = apparier([anciennes[i] for i in avant - vivant - vendu], list(nouvelles.values()))
    disparues = len(avant - vivant - vendu) - len(paires)
    r = {"lignes": len(lignes), "vivants": len(vivant), "avant": len(avant), "disparues": disparues,
         "vendues": len(avant & vendu), "nouv": len(nouvelles) - len(paires), "repub": len(paires), "paires": paires}
    if not vivant:
        return "aucune annonce en ligne ou masquée", r
    if avant and (disparues / len(avant) > SEUIL_DISPARUES or len(vivant) < len(avant) * 0.5) and not force:
        return f"{disparues} annonces disparues sans être vendues : export peut-être incomplet", r
    return None, r


def reconstruire():
    for cmd in (["python3", "annonces.py"], ["python3", "leboncoin.py"], ["python3", "photos_annonces.py"],
                ["python3", str(ICI.parent / "app-source" / "photos_supabase.py")]):
        p = subprocess.run(cmd, cwd=ICI, capture_output=True, text=True, timeout=3000)
        if p.returncode:
            raise RuntimeError(f"{cmd[1]} : {p.stderr.strip()[-300:]}")


def main():
    logs = base.docs("imports_log/")
    if logs is None:
        sys.exit("base illisible")
    fait = []
    for d in sorted(logs, key=lambda x: x["chemin"]):
        cle, x = d["chemin"].split("/", 1)[1], d["data"]
        if x.get("etat") == "en attente":
            texte = (base.doc("imports_csv/" + cle) or {}).get("csv") or ""
            motif, r = verifier(texte, bool(x.get("force")))
            if motif:
                base.doc_ecrire("imports_log/" + cle, {**x, "etat": "refusé", "motif": motif})
                fait.append(f"{x.get('nom')} refusé ({motif})")
                continue
            VINTED.mkdir(parents=True, exist_ok=True)
            # Nom daté du jour de l'import : c'est le plus récent pour annonces.py et leboncoin.py
            f = VINTED / f"vinted_annonces_{dt.date.today().isoformat()}_imp{cle[11:19].replace('-', '')}.csv"
            f.write_text(texte.lstrip("﻿"), encoding="utf-8-sig")
            reconstruire()
            noter_republications(r.pop("paires", []))
            base.doc_ecrire("imports_log/" + cle, {**x, "etat": "appliqué", "fichier": f.name, "verif": r})
            fait.append(f"{x.get('nom')} appliqué : +{r['nouv']} nouvelles, {r['repub']} republiées, {r['vendues']} vendues, {r['disparues']} disparues")
        elif x.get("etat") == "à annuler":
            f = VINTED / (x.get("fichier") or "-")
            if f.exists():
                f.rename(f.with_suffix(".csv.annule"))
            reconstruire()
            base.doc_ecrire("imports_log/" + cle, {**x, "etat": "annulé"})
            fait.append(f"{x.get('nom')} annulé")
    if fait:
        # Données de l'appli (liste des annonces, photos) republiées
        p = subprocess.run(["bash", "publier.sh"], cwd=ICI, capture_output=True, text=True, timeout=900)
        fait.append("publié" if p.returncode == 0 else "publication échouée : " + p.stdout.strip()[-200:])
    print("\n".join(fait) if fait else "rien")


if __name__ == "__main__":
    main()
