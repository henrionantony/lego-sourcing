"""Opération LEGO repérée dans une newsletter d'enseigne : la chiffrer et prévenir sur Telegram.

Claude lit les pubs de la boîte dédiée ; quand une parle de LEGO, il résume l'opération en une
phrase (« -30 % sur une sélection LEGO Star Wars ») et lance ce script. Les sets rentables à ce
taux sont listés comme pour une opération Dealabs. L'heure du mail est notée dans
../data/pubs_mail.csv pour comparer avec l'heure du même deal sur Dealabs (test de réactivité).

Usage : python3 pub_mail.py "<enseigne>" "<opération>" "<date ISO du mail>" [prix exemple] [--essai]
"""
import csv
import datetime as dt
import sys

import base
import operations
from alertes import details, duree, euros, pastille, seuils_gain, telegram, GAIN_FORT
from referentiel import DATA, charger

LOG = DATA / "pubs_mail.csv"


def main(enseigne: str, texte: str, recu: str, prix: float | None, essai: bool):
    ref = charger()
    seuils_gain(ref)
    titre = f"{texte} ({enseigne.lower()})"
    op = operations.taux(titre, prix)
    stock = base.stock_par_set()
    liste = operations.sets_rentables(op[0], titre, ref, stock, details) if op else []
    top = liste[0]["gain"] if liste else None
    corps = [f"📧 NEWSLETTER {enseigne.upper()}" + (f" · {pastille(top)} jusqu'à +{euros(top)} net par boîte" if liste else ""),
             texte]
    if not op:
        corps.append("Remise non chiffrable automatiquement : à regarder.")
    else:
        corps.append(f"🧮 {op[1]}" + (f" · gammes : {', '.join(liste[0]['gammes'])}" if liste and liste[0]["gammes"] else ""))
        if liste:
            corps.append("Sets rentables à ce taux, s'ils font partie de l'opération (prix public moins la remise) :")
            corps += [f"{pastille(o['gain'])} +{euros(o['gain'])} · {o['set']} {o['nom']} · {euros(o['prix'])} "
                      f"(public {euros(o['public'])}) · {duree(o['jours'])}" + (f" · 📦 {o['stock']} en stock" if o["stock"] else "")
                      for o in liste]
        else:
            corps.append("Aucun set du catalogue n'est rentable à ce taux.")
    corps.append("Vérifie dans l'appli de l'enseigne quels sets sont dans l'opération.")
    if not op or liste:
        telegram("\n".join(corps), top is not None and top >= GAIN_FORT, essai)
    if not essai:
        neuf = not LOG.exists()
        with LOG.open("a", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if neuf:
                w.writerow(["recu", "traite", "enseigne", "operation", "sets_rentables", "gain_max"])
            w.writerow([recu, dt.datetime.now().isoformat(timespec="minutes"), enseigne, texte, len(liste), top or ""])


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--essai"]
    main(a[0], a[1], a[2], float(a[3]) if len(a) > 3 else None, "--essai" in sys.argv)
