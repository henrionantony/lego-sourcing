"""Ventes faites avec un associé (co_investisseur) : on encaisse tout, et sa part
(nette des 21 % de prélèvements, payés sur la totalité) devient une ligne
« associe » de la trésorerie, à lui rendre plus tard (a_rendre_le).
History garde seulement la part du vendeur (ventes.prix_vente)."""
import base
from regles import PRELEVEMENTS

DEPUIS = "2026-10-18"          # départ de la trésorerie
A_RENDRE_LE = "2027-09-30"     # fin de l'année suivante


def main():
    ventes = base.lire("ventes", "select=id,date_vente,prix_vente,co_investisseur,nom_article"
                       f"&co_investisseur=not.is.null&vendu_par=is.null&date_vente=gte.{DEPUIS}&limit=10000") or []
    deja = {t["vente_id"] for t in base.lire("tresorerie", "select=vente_id&type=eq.associe&limit=10000") or []}
    lignes = [{"le": v["date_vente"], "type": "associe", "vente_id": v["id"], "a_rendre_le": A_RENDRE_LE,
               "montant": round(float(v["prix_vente"]) * (1 - PRELEVEMENTS), 2),
               "libelle": f"À rendre à {v['co_investisseur']} : {v['nom_article'] or ''}"[:300]}
              for v in ventes if v["id"] not in deja and v["prix_vente"]]
    base.ajouter("tresorerie", lignes)
    return len(lignes)


if __name__ == "__main__":
    print(main(), "part(s) d'associé ajoutée(s)")
