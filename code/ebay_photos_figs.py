"""Photos officielles des figurines pour eBay, hébergées sur GitHub Pages (docs/ebay/figs/).

07/11 : img.bricklink.com renvoie une page anti-robot au serveur GitHub ET à eBay, donc les
annonces de figurines partaient sans photo. On fabrique ici une image 1000 × 1000 (fond blanc,
eBay demande au moins 500 px) à partir de la vignette officielle du catalogue de reconnaissance,
puis publier.sh la copie dans le dépôt. Jamais ses propres photos sur eBay (profil distinct).
Usage : python3 ebay_photos_figs.py njo0701 njo1047 …
"""
import io
import re
import sys
import urllib.request
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent.parent / "ebay" / "figs_img"
_PROPRIO = "henrionantony/lego-sourcing".split("/")[0]
URL_PAGES = f"https://{_PROPRIO}.github.io/lego-sourcing/ebay/figs/"
SOURCE = "https://storage.googleapis.com/brickognize-static/thumbnails/v2.23/fig/{}/0.webp"


def variantes(code: str) -> list:
    """njo701 → njo0701, njo701, njo701 sans zéros… (le catalogue n'est pas homogène)."""
    m = re.match(r"([a-z]+)0*(\d+)(.*)$", code.lower())
    if not m:
        return [code.lower()]
    p, n, fin = m.groups()
    return list(dict.fromkeys([f"{p}{n.zfill(4)}{fin}", f"{p}{n.zfill(3)}{fin}", f"{p}{n}{fin}"]))


def nom_fichier(code: str) -> str:
    return variantes(code)[0] + ".jpg"


def fabriquer(code: str) -> Path | None:
    """Crée l'image si elle manque ; None si aucune vignette officielle n'est trouvée."""
    f = DOSSIER / nom_fichier(code)
    if f.exists():
        return f
    from PIL import Image
    for v in variantes(code):
        try:
            with urllib.request.urlopen(urllib.request.Request(SOURCE.format(v), headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGBA")
        except Exception:
            continue
        fond = Image.new("RGB", (1000, 1000), "white")
        k = 900 / max(im.size)
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        fond.paste(im, ((1000 - im.width) // 2, (1000 - im.height) // 2), im)
        DOSSIER.mkdir(parents=True, exist_ok=True)
        fond.save(f, "JPEG", quality=90)
        return f
    return None


def url(code: str) -> str | None:
    return URL_PAGES + nom_fichier(code) if fabriquer(code) else None


if __name__ == "__main__":
    for c in sys.argv[1:]:
        print(c, fabriquer(c) or "introuvable")
