"""Jeton de renouvellement eBay (valable ~18 mois), obtenu une fois avec l'accord du vendeur.

1. `python3 ebay_jeton.py lien` : écrit le lien d'accord eBay dans le document privé
   « ebay/consentement » (identifiant d'appli + RuName, rien de secret).
2. Le vendeur ouvre le lien, accepte ; eBay l'envoie sur une page dont l'adresse contient « code=… »
   (valable 5 minutes, une seule fois).
3. `EBAY_CODE=<adresse ou code> python3 ebay_jeton.py` : échange le code contre le jeton de
   renouvellement, qui est rangé CHIFFRÉ (clé = secret GitHub EBAY_CLIENT_SECRET) dans « ebay/jeton ».
   Il n'apparaît jamais en clair, ni dans les journaux ni dans la base.
Le RuName (nom de l'adresse de retour, pas secret) est dans « ebay/reglages » → "runame".
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import base

API = "https://api.ebay.com"
SCOPES = "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.account"


def chiffrer(texte: str) -> str:
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A", "-pass", "env:EBAY_CLIENT_SECRET"],
                          input=texte.encode(), capture_output=True, check=True).stdout.decode()


def dechiffrer(texte: str) -> str:
    return subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-a", "-A", "-pass", "env:EBAY_CLIENT_SECRET"],
                          input=texte.encode(), capture_output=True, check=True).stdout.decode()


def jeton_range() -> str | None:
    """Jeton de renouvellement rangé par ce script, déchiffré (None s'il n'y en a pas)."""
    d = base.doc("ebay/jeton") or {}
    try:
        return dechiffrer(d["chiffre"]) if d.get("chiffre") else None
    except (subprocess.CalledProcessError, KeyError):
        return None


def runame() -> str:
    r = (base.doc("ebay/reglages") or {}).get("runame")
    if not r:
        sys.exit("RuName absent de ebay/reglages")
    return r


def lien():
    q = urllib.parse.urlencode({"client_id": os.environ["EBAY_CLIENT_ID"], "response_type": "code",
                                "redirect_uri": runame(), "scope": SCOPES})
    base.doc_ecrire("ebay/consentement", {"lien": "https://auth.ebay.com/oauth2/authorize?" + q, "le": date.today().isoformat()})
    print("lien d'accord écrit")


def echanger(brut: str):
    brut = brut.strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(brut).query).get("code", [None])[0] if "code=" in brut else brut
    if not code:
        sys.exit("aucun code dans l'adresse donnée")
    print(f"::add-mask::{code}")
    cle = f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
    corps = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": runame()}).encode()
    req = urllib.request.Request(API + "/identity/v1/oauth2/token", data=corps, method="POST", headers={
        "Authorization": "Basic " + base64.b64encode(cle).decode(), "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rep = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"échange refusé ({e.code}) : {e.read()[:200].decode(errors='replace')}")
    rt = rep.get("refresh_token")
    if not rt:
        sys.exit("pas de jeton de renouvellement dans la réponse")
    print(f"::add-mask::{rt}")
    ok = base.doc_ecrire("ebay/jeton", {"chiffre": chiffrer(rt), "le": date.today().isoformat(),
                                        "jours": int(rep.get("refresh_token_expires_in", 0)) // 86400})
    print("jeton de renouvellement rangé (chiffré)" if ok else "écriture refusée")


if __name__ == "__main__":
    if sys.argv[1:2] == ["lien"]:
        lien()
    else:
        echanger(os.environ.get("EBAY_CODE", ""))
