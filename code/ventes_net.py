"""Revenu net de chaque vente : (prix + port payé par l'acheteur) − prélèvements − frais d'envoi réels.

Usage : python3 ventes_net.py remplir                     (ventes sans revenu net)
        python3 ventes_net.py frais <vente_id> <montant> [port_paye]
"""
import sys

import base
from regles import PRELEVEMENTS


def net(prix, port_paye=None, frais_envoi=None) -> float:
    return round((float(prix or 0) + float(port_paye or 0)) * (1 - PRELEVEMENTS) - float(frais_envoi or 0), 2)


def remplir() -> int:
    n = 0
    while True:
        lot = base.lire("ventes", "select=id,prix_vente,port_paye,frais_envoi&revenu_net=is.null&order=id.asc&limit=500")
        if not lot:
            return n
        for v in lot:
            base.modifier("ventes", f"id=eq.{v['id']}", {"revenu_net": net(v["prix_vente"], v["port_paye"], v["frais_envoi"])})
            n += 1


def frais(vente_id: int, montant: float, port_paye=None):
    v = base.lire("ventes", f"select=id,prix_vente,port_paye&id=eq.{vente_id}")[0]
    port = v["port_paye"] if port_paye is None else float(port_paye)
    r = net(v["prix_vente"], port, montant)
    base.modifier("ventes", f"id=eq.{vente_id}", {"frais_envoi": float(montant), "port_paye": port, "revenu_net": r})
    return r


if __name__ == "__main__":
    if sys.argv[1:2] == ["remplir"]:
        print(remplir(), "ventes complétées")
    elif sys.argv[1:2] == ["frais"]:
        print("revenu net :", frais(int(sys.argv[2]), float(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None))
    else:
        print(__doc__)
