"""Promos Lego.com (points Insiders doublés, set offert dès X €) : chiffre la
remise réelle et liste les sets qui deviennent rentables au prix Lego.com.

Usage : python3 promo_lego.py "titre du deal"   (affiche le message)
"""
import re
import sys

import pandas as pd

from regles import GWP_DEFAUT, PRELEVEMENTS, net

TAUX_POINTS = 7.5 * 5 / 750  # Insiders : 7,5 points par euro, 750 points = bon de 5 € (≈ 5 %)
PRIORITE_MOIS = 4


def detecter(titre: str) -> dict | None:
    """Reconnaît une promo Lego.com dans un titre Dealabs, sinon None."""
    t = titre.lower()
    chez_lego = "(lego)" in t or "insiders" in t or "lego.com" in t
    mult = re.search(r"points?\s*(doubl|tripl|x\s*([23]))", t)
    offert = re.search(r"(\d{5})[^()]*offerte?s?|offerte?s?[^()]*?(\d{5})", t)
    if not chez_lego or not (mult or offert):
        return None
    m = 1
    if mult:
        m = 3 if "tripl" in mult.group(1) or mult.group(2) == "3" else 2
    seuil = re.search(r"d[èe]s\s*(\d+)\s*€", t)
    return {"mult_points": m, "cadeau": (offert.group(1) or offert.group(2)) if offert else None,
            "seuil": float(seuil.group(1)) if seuil else None}




def valeur_cadeau(cadeau: str | None, ref) -> float | None:
    if not cadeau:
        return None
    x = ref[ref.base == cadeau]
    v = x.prix_vente_estime.iloc[0] if len(x) else None
    # Cadeau non chiffré : valeur de revente habituelle (fichier privé)
    return float(v if v == v and v else GWP_DEFAUT) * (1 - PRELEVEMENTS)


def remise(promo: dict, ref, panier: float | None = None) -> tuple[float, float]:
    """(remise points seule, remise points + cadeau au panier donné ou au seuil)."""
    rp = TAUX_POINTS * promo["mult_points"]
    vc = valeur_cadeau(promo["cadeau"], ref)
    panier = panier or promo["seuil"]
    rc = vc / panier if vc and panier else 0.0
    return rp, rp + rc


def sets_rentables(ref, taux: float, n: int = 12) -> pd.DataFrame:
    """Sets en vente sur Lego.com dont le prix après remise passe sous le prix max."""
    x = ref[(ref.statut == "en vente") & ref.prix_public.notna() & ref.prix_max.notna()].copy()
    x = x.sort_values("set_num").drop_duplicates("base")
    # Prix Lego.com du moment (soldes comprises) quand la page a été relevée, sinon prix public.
    from referentiel import DATA
    off = DATA / "lego_officiel.csv"
    if off.exists():
        pa = pd.read_csv(off, dtype={"set": str}).set_index("set").prix_actuel
        x["prix_public"] = x.base.map(pa).fillna(x.prix_public)
    x["prix_eff"] = x.prix_public * (1 - taux)
    x = x[(x.prix_eff <= x.prix_max) & (x.get("demande", "moyenne") != "faible")]
    rc = x.retrait_croise.where(x.retrait_croise.notna() & (x.retrait_croise != ""), x.retrait)
    x["quitte"] = pd.to_datetime(rc, errors="coerce")
    x["prio"] = x.quitte <= pd.Timestamp.today() + pd.DateOffset(months=PRIORITE_MOIS)
    pv = x.pv_garde.where(x.pv_garde.notna(), x.prix_vente_estime)
    x["gain"] = [net(v, p) for v, p in zip(pv, x.prix_eff)]
    x["marge"] = x.gain / x.prix_eff
    x["forte"] = x.get("demande", "") == "forte"
    x = x.sort_values(["forte", "prio", "marge"], ascending=[False, False, False])
    x.attrs["total"] = len(x)
    return x.head(n)


def message(titre: str, promo: dict, ref, lien: str = "") -> str:
    def e(v):
        return f"{v:.2f} €".replace(".", ",")
    rp, rt = remise(promo, ref)
    l = [f"📣 PROMO LEGO.COM : {titre}"]
    if promo["mult_points"] > 1:
        l.append(f"Points ×{promo['mult_points']} ≈ {rp:.0%} remboursés en bons d'achat Lego")
    if promo["cadeau"]:
        vc = valeur_cadeau(promo["cadeau"], ref)
        s = f" dès {e(promo['seuil'])} d'achat" if promo["seuil"] else ""
        l.append(f"Cadeau {promo['cadeau']}{s} : " + (f"revendable ≈ {e(vc)} nets" if vc else "valeur de revente inconnue (bonus non compté)"))
    # Points, cashback et cadeau ne sont pas déduits du prix : ils servent de marge de sécurité.
    l.append(f"Bonus non déduits du prix (marge de sécurité) ≈ {rt:.0%}"
             + (f" ; vise un panier juste au-dessus de {e(promo['seuil'])}" if promo["seuil"] else ""))
    ok = sets_rentables(ref, 0.0)
    if ok.empty:
        l.append("Aucun set ne devient rentable avec cette seule remise : à combiner avec une autre réduc.")
    else:
        l.append(f"{ok.attrs['total']} sets rentables à ce prix, les meilleurs (🔥 = forte demande, 🔚 = quitte le catalogue bientôt ; demande faible écartée) :")
        for _, s in ok.iterrows():
            l.append(f"{'🔥 ' if s.forte else ''}{'🔚 ' if s.prio else ''}{s.base} {s['name']} : {e(s.prix_eff)}"
                     + (f" au lieu de {e(s.prix_public)}" if s.prix_public - s.prix_eff > 0.01 else "") + " "
                     f"→ gain net ≈ {e(s.gain)} ({s.marge:+.0%})" + (f", retrait {s.quitte:%m/%Y}" if s.quitte == s.quitte else "")
                     + f", estimation {s.fiabilite}")
    if lien:
        l.append(lien)
    return "\n".join(l)


if __name__ == "__main__":
    from referentiel import charger
    t = sys.argv[1]
    p = detecter(t)
    print(message(t, p, charger()) if p else "pas une promo Lego.com")
