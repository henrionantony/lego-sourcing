"""Vérification unique d'un prix de vente Vinted / Leboncoin, avec TOUTES les règles
d'un coup (à passer sur chaque prix avant de le proposer).

Règles (strategie-revente.md, section « LES 4 RÈGLES DE PRIX », + décisions du 03/11) :
1. Prix décidé à la main (stock/app/prix_forces.json : sets à solder…) : il gagne.
2. Set à garder (retiré dans les 3 mois et valeur après retrait ≥ prix × 1,3) :
   « garder » (si plusieurs exemplaires : 1 seul en vente).
3. Set encore en catalogue : prix + frais acheteur + port ≤ prix LEGO, sauf si
   une de ses ventes passées a été plus haute.
4. Plancher de la commande (a_publier « plancher », +40 % calculé sur la transaction) :
   si le plafond 3 est sous ce plancher → « pas le moment ».
5. Jamais sous sa propre vente passée la plus haute (sauf prix forcé).
6. eBay : si les annonces eBay (≥ 5) partent nettement plus haut, on monte
   jusqu'au plus bas eBay − 5 %, sans dépasser le plafond 3.
Prix ronds.

Usage : python3 verif_prix.py <set> [prix proposé] [quantité]
"""
import datetime as dt
import json
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
APP = ICI.parent / "stock" / "app"


def _charger():
    import base
    ap = json.loads((APP / "a_publier.json").read_text())
    f = APP / "prix_forces.json"
    forces = json.loads(f.read_text()) if f.exists() else {}
    try:
        eb = base.doc("ebay/prix") or {}
    except Exception:
        eb = {}
    return ap, forces, eb


_CACHE = None


def donnees():
    global _CACHE
    if _CACHE is None:
        _CACHE = _charger()
    return _CACHE


def rond(p: float) -> int:
    """Prix rond, vers le bas : < 30 € à l'euro, sinon au multiple de 5."""
    return int(p) if p < 30 else int(p // 5 * 5)


def plafond_catalogue(pp: float) -> int:
    port = 4 if pp < 40 else 6 if pp < 100 else 10
    return rond(pp * 0.95 - 0.70 - port)


def verifier(s: str, prix: float | None = None, qte: int = 1) -> dict:
    """{"action": "vendre"|"garder"|"pas_le_moment"|"inconnu", "prix": int|None, "raisons": [..]}"""
    ap, forces, eb = donnees()
    s = str(s)
    raisons = []
    if s in forces:
        f = forces[s]
        return {"action": "vendre", "prix": int(f["prix"]), "raisons": [f"prix décidé : {f.get('raison', '')}"]}
    a = ap.get(s)
    if not a:
        return {"action": "inconnu", "prix": int(prix) if prix else None, "raisons": ["set absent des prix de l'appli"]}
    cout = a.get("cout") or 0
    plancher = a.get("plancher") or cout * 1.4
    vendus = [v[1] for v in a.get("vendus") or [] if v[0] >= (dt.date.today() - dt.timedelta(days=550)).isoformat()]
    vmax = max(vendus) if vendus else 0
    pp = a.get("pp")
    en_cat = a.get("st") == "en vente" and pp

    # 2. garder jusqu'au retrait
    retrait = a.get("retrait") or ""
    if retrait and dt.date.today().isoformat() <= retrait <= (dt.date.today() + dt.timedelta(days=92)).isoformat():
        if a.get("garde") and a["garde"] >= (prix or a.get("prix") or 0) * 1.3:
            if qte <= 1:
                return {"action": "garder", "prix": None, "raisons": [f"retiré le {retrait}, vaut ~{a['garde']:.0f} € après"]}
            raisons.append(f"retiré le {retrait} : 1 seul en vente, garder {qte - 1}")

    p = p0 = prix or a.get("prix") or plancher
    # 6. eBay plus haut
    e = eb.get(s) or {}
    if e.get("bas") and e.get("n", 0) >= 5 and e["bas"] * 0.95 > p:
        p = e["bas"] * 0.95
        raisons.append(f"eBay à partir de {e['bas']:.0f} €")
    # 5. pas sous sa propre vente
    if vmax > p:
        p = vmax
        raisons.append(f"déjà vendu {vmax:.0f} €")
    # 3. plafond catalogue
    if en_cat:
        cap = max(plafond_catalogue(pp), int(vmax))
        if p > cap:
            p = cap
            raisons.append(f"en catalogue à {pp} € : max {cap} €")
        if cap < plancher - 1:
            return {"action": "pas_le_moment", "prix": None,
                    "raisons": raisons + [f"en catalogue, max {cap} € < plancher {plancher:.0f} €"]}
    # 4. plancher
    if p < plancher - 1:
        p = plancher
        raisons.append(f"plancher de la commande {plancher:.0f} €")
    if p == p0:
        return {"action": "vendre", "prix": int(round(p)), "raisons": raisons}
    return {"action": "vendre", "prix": max(rond(p), int(plancher + 0.99)) if p >= 30 else int(round(p)), "raisons": raisons}


if __name__ == "__main__":
    a = sys.argv[1:]
    print(json.dumps(verifier(a[0], float(a[1]) if len(a) > 1 else None, int(a[2]) if len(a) > 2 else 1),
                     ensure_ascii=False))
