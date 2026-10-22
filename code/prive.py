"""Fichiers privés des passages GitHub (table des prix, réglages) : rangés dans
l'espace de stockage privé de Supabase, jamais dans le dépôt public.

Usage : python3 prive.py envoyer <prix.csv> <parametres.json>   (depuis la session Claude)
        python3 prive.py recevoir <dossier data>                (au début d'un passage GitHub)
        python3 prive.py journal <nom> < sortie                 (garde la fin d'une sortie en erreur, pour l'analyse)
"""
import datetime as dt
import sys
from pathlib import Path

import base

FICHIERS = {"prix.csv": "referentiel.csv", "parametres.json": "parametres.json"}


def main():
    action = sys.argv[1]
    if action == "envoyer":
        for nom, src in zip(FICHIERS, sys.argv[2:4]):
            base.fichier_prive_ecrire(nom, Path(src).read_bytes())
        # Vraie date de la table des prix (sur GitHub, le fichier reçu a toujours la date du jour)
        quand = dt.datetime.fromtimestamp(Path(sys.argv[2]).stat().st_mtime, dt.timezone.utc)
        base.fichier_prive_ecrire("referentiel_maj.txt", quand.isoformat().encode())
        print("fichiers privés envoyés")
    elif action == "recevoir":
        dossier = Path(sys.argv[2])
        dossier.mkdir(parents=True, exist_ok=True)
        for nom, dest in FICHIERS.items():
            (dossier / dest).write_bytes(base.fichier_prive_lire(nom))
        (dossier / "referentiel_maj.txt").write_bytes(base.fichier_prive_lire("referentiel_maj.txt"))
        print("fichiers privés reçus")
    elif action == "journal":
        texte = sys.stdin.read()[-4000:]
        base.doc_ecrire("journal/" + sys.argv[2], {"le": dt.datetime.now(dt.timezone.utc).isoformat(), "texte": texte})


if __name__ == "__main__":
    main()
