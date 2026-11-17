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


def zoom(code: str) -> Path | None:
    """2e photo : gros plan tête + torse (détail des imprimés)."""
    f = DOSSIER / nom_fichier(code).replace(".jpg", "-z.jpg")
    if f.exists():
        return f
    src = fabriquer(code)
    if not src:
        return None
    from PIL import Image, ImageChops
    im = Image.open(src).convert("RGB")
    boite = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox() or (0, 0, 1000, 1000)
    x0, y0, x1, y1 = boite
    h = (y1 - y0) * 0.55
    cx = (x0 + x1) / 2
    cote = max(h, (x1 - x0) * 0.75)
    im = im.crop((round(cx - cote / 2), round(y0 - 20), round(cx + cote / 2), round(y0 - 20 + cote))).resize((1000, 1000), Image.LANCZOS)
    im.save(f, "JPEG", quality=90)
    return f


def photo_set(s: str) -> Path | None:
    """3e photo : la boîte du set d'origine, avec la mention « figurine seule »."""
    if not s:
        return None
    f = DOSSIER / f"set-{s}.jpg"
    if f.exists():
        return f
    from PIL import Image, ImageDraw, ImageFont
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://cdn.rebrickable.com/media/sets/{s}-1.jpg",
                                                          headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            im = Image.open(io.BytesIO(r.read())).convert("RGB")
    except Exception:
        return None
    fond = Image.new("RGB", (1000, 1000), "white")
    k = min(960 / im.width, 820 / im.height)
    im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    fond.paste(im, ((1000 - im.width) // 2, 20 + (820 - im.height) // 2))
    d = ImageDraw.Draw(fond)
    d.rectangle((0, 860, 1000, 1000), fill=(255, 207, 0))
    try:
        police = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
    except OSError:
        police = ImageFont.load_default()
    for i, ligne in enumerate([f"Figurine seule, issue du set {s}", "(set non inclus)"]):
        w = d.textlength(ligne, font=police)
        d.text(((1000 - w) / 2, 875 + i * 58), ligne, fill="black", font=police)
    DOSSIER.mkdir(parents=True, exist_ok=True)
    fond.save(f, "JPEG", quality=88)
    return f


def urls(code: str, s: str | None = None) -> list:
    """Photos eBay d'une figurine : entière, gros plan, set d'origine (si connu)."""
    if not fabriquer(code):
        return []
    out = [URL_PAGES + nom_fichier(code)]
    if zoom(code):
        out.append(URL_PAGES + nom_fichier(code).replace(".jpg", "-z.jpg"))
    if photo_set(str(s or "")):
        out.append(URL_PAGES + f"set-{s}.jpg")
    return out


def url(code: str) -> str | None:
    return URL_PAGES + nom_fichier(code) if fabriquer(code) else None


if __name__ == "__main__":
    for c in sys.argv[1:]:
        print(c, fabriquer(c) or "introuvable")
