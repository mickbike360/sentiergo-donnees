#!/usr/bin/env python3
"""
Fabrique les paquets pays de SentierGo à partir des extraits OpenStreetMap
de Geofabrik : un fichier XX.json.gz par pays, avec les points utiles aux
camping-cars (eau, vidange, aires, campings, parkings, toilettes, douches,
laveries, GPL, gaz, supermarchés), et un XX.meta.json (nombre de points,
taille, date) qui sert à construire l'index.

Les règles de tri reprennent exactement celles de l'appli (Categorie.kt).

Usage : construire.py --groupe 0 --groupes 8 [--pays "FR,IT"]
Dépend de osmium-tool (apt install osmium-tool).
"""
import argparse
import datetime
import gzip
import json
import math
import os
import re
import subprocess
import sys
import urllib.request

# Code ISO → nom de l'extrait Geofabrik (europe/<nom>-latest.osm.pbf).
# Ordre approximatif du plus gros au plus petit : la répartition en groupes équilibre la charge.
PAYS = [
    ("DE", "germany"), ("FR", "france"), ("GB", "great-britain"), ("IT", "italy"), ("PL", "poland"),
    ("NL", "netherlands"), ("ES", "spain"), ("AT", "austria"), ("CZ", "czech-republic"), ("CH", "switzerland"),
    ("SE", "sweden"), ("NO", "norway"), ("FI", "finland"), ("BE", "belgium"), ("DK", "denmark"),
    ("RO", "romania"), ("HU", "hungary"), ("GR", "greece"), ("PT", "portugal"), ("SK", "slovakia"),
    ("IE", "ireland-and-northern-ireland"), ("HR", "croatia"), ("BG", "bulgaria"), ("RS", "serbia"),
    ("SI", "slovenia"), ("LT", "lithuania"), ("LV", "latvia"), ("EE", "estonia"), ("BA", "bosnia-herzegovina"),
    ("IS", "iceland"), ("AL", "albania"), ("MK", "macedonia"), ("ME", "montenegro"), ("LU", "luxembourg"),
    ("CY", "cyprus"), ("MT", "malta"), ("AD", "andorra"), ("LI", "liechtenstein"), ("MC", "monaco"),
]

# Filtre osmium : tout ce qui peut devenir un point utile (le tri fin se fait ensuite).
FILTRE = [
    "nwr/tourism=caravan_site,camp_site",
    "nwr/amenity=sanitary_dump_station,water_point,drinking_water,parking,toilets,shower,washing_machine,fuel,marketplace",
    "n/amenity=vending_machine",
    "nwr/man_made=water_tap",
    "nwr/natural=spring",
    "nwr/shop=laundry,gas,supermarket,convenience,farm",
    # Ce qui fait un beau spot : point de vue, plage, cascade (pas des points affichés, voir beaux_spots).
    "n/tourism=viewpoint",
    "nwr/natural=beach",
    "n/waterway=waterfall",
]

# Tags gardés : ce que l'appli affiche ou utilise pour trier.
TAGS_UTILES = {
    "name", "name:fr", "operator", "fee", "charge", "capacity", "capacity:motorhome", "capacity:caravans",
    "opening_hours", "maxstay", "drinking_water", "water_point", "sanitary_dump_station", "power_supply",
    "toilets", "shower", "showers", "internet_access", "access", "seasonal", "maxheight", "maxheight:physical",
    "check_date", "survey:date", "description", "description:fr", "website", "contact:website", "phone",
    "contact:phone", "motorhome", "caravans", "caravan", "parking", "amenity", "tourism", "shop", "natural",
    "man_made", "fuel:lpg", "wheelchair",
    # Stations-service : enseigne, gazole, AdBlue, accès poids lourds (gabarit).
    "brand", "fuel:diesel", "fuel:adblue", "hgv",
    # Ajoutés par beaux_spots : beau lieu à proximité.
    "sg:vue", "sg:vue_nom", "sg:vue_m",
    # Producteurs et marchés : produits, bio, type de distributeur.
    "produce", "organic", "vending",
}

PARKING_EXCLUS = {"underground", "multi-storey", "rooftop", "street_side", "lane", "on_kerb", "half_on_kerb"}
ACCES_EXCLUS = {"private", "no", "customers", "permit", "delivery"}


def confirme_camping_car(t):
    """Copie de Categorie.confirmeCampingCar : aire, camping qui accepte, ou parking marqué autorisé."""
    if t.get("tourism") == "caravan_site":
        return True
    if t.get("tourism") == "camp_site":
        return t.get("motorhome") != "no" and t.get("caravans") != "no"
    if t.get("amenity") == "parking":
        return t.get("motorhome") in ("yes", "designated") and t.get("access") not in ("private", "no")
    return False


# Copie de Categorie.PRODUITS_FERME : distributeurs à la ferme (œufs, lait, légumes…).
PRODUITS_FERME = re.compile(r"eggs|milk|fruit|vegetable|potato|cheese|meat|honey|œuf|oeuf|lait|légume|legume|fromage|miel", re.I)


def categorie(t, type_osm):
    """Copie de Categorie.depuisTags (Kotlin). Renvoie le nom de la catégorie, ou None."""
    amenity, tourism, shop = t.get("amenity"), t.get("tourism"), t.get("shop")
    if t.get("sg:vue") and confirme_camping_car(t):
        # Beau spot : près d'une belle vue ET confirmé pour les camping-cars (jamais deviné).
        return "SPOT"
    if tourism == "caravan_site":
        return "AIRE"
    if amenity == "sanitary_dump_station":
        return "VIDANGE"
    if amenity in ("water_point", "drinking_water"):
        return "EAU"
    if t.get("man_made") == "water_tap" and t.get("drinking_water") != "no":
        return "EAU"
    if t.get("natural") == "spring":
        return "SOURCE"
    if amenity == "parking" and t.get("motorhome") in ("yes", "designated"):
        return "PARKING"
    if (amenity == "parking" and type_osm != "n" and t.get("parking") not in PARKING_EXCLUS
            and t.get("access") not in ACCES_EXCLUS and t.get("motorhome") != "no" and t.get("caravan") != "no"):
        # Grands parkings : dessinés en surface seulement (comme la requête de l'appli).
        return "PARKING_VERIF"
    if tourism == "camp_site" and t.get("motorhome") != "no" and t.get("caravans") != "no":
        return "CAMPING"
    if amenity == "toilets":
        return "TOILETTES"
    if amenity == "shower":
        return "DOUCHE"
    if shop == "laundry" or amenity == "washing_machine":
        return "LAVERIE"
    if amenity == "fuel" and t.get("fuel:lpg") == "yes":
        return "GPL"
    if amenity == "fuel":
        return "STATION"
    if shop == "gas":
        return "GAZ"
    if shop == "farm":
        return "PRODUCTEUR"
    if amenity == "vending_machine" and PRODUITS_FERME.search(t.get("vending") or ""):
        return "PRODUCTEUR"
    if amenity == "marketplace":
        return "MARCHE"
    if shop in ("supermarket", "convenience"):
        return "COURSES"
    return None


def centre(geom):
    """Point représentatif : le point lui-même, ou la moyenne du contour extérieur."""
    g, c = geom["type"], geom["coordinates"]
    if g == "Point":
        pts = [c]
    elif g == "LineString":
        pts = c
    elif g == "Polygon":
        pts = c[0]
    elif g == "MultiPolygon":
        pts = c[0][0]
    else:
        return None
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return round(lat, 6), round(lon, 6)


def attrait(t):
    """« viewpoint », « beach » ou « waterfall » si l'objet est un beau lieu, sinon None."""
    if t.get("tourism") == "viewpoint":
        return "viewpoint"
    if t.get("natural") == "beach":
        return "beach"
    if t.get("waterway") == "waterfall":
        return "waterfall"
    return None


# Rayon autour d'un lieu de stationnement : un beau lieu à moins de 300 m (quelques minutes à pied).
RAYON_SPOT_M = 300.0


def beaux_spots(pois, attraits):
    """
    Marque les aires, campings et parkings proches d'un beau lieu : tags
    sg:vue (type) et sg:vue_nom (son nom, s'il en a un). Recherche par grille
    de 0,01° (environ 1 km) : rapide même pour un grand pays.
    """
    grille = {}
    for a in attraits:
        grille.setdefault((int(a[0] * 100 // 1), int(a[1] * 100 // 1)), []).append(a)
    n = 0
    for p in pois:
        t = p["tags"]
        if t.get("amenity") != "parking" and t.get("tourism") not in ("caravan_site", "camp_site"):
            continue
        lat, lon = p["lat"], p["lon"]
        gi, gj = int(lat * 100 // 1), int(lon * 100 // 1)
        meilleur = None
        k = math.cos(math.radians(lat))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for a in grille.get((gi + di, gj + dj), ()):
                    d = math.hypot((a[0] - lat) * 111_320.0, (a[1] - lon) * 111_320.0 * k)
                    if d <= RAYON_SPOT_M and (meilleur is None or d < meilleur[0]):
                        meilleur = (d, a)
        if meilleur:
            t["sg:vue"] = meilleur[1][2]
            if meilleur[1][3]:
                t["sg:vue_nom"] = meilleur[1][3]
            t["sg:vue_m"] = str(int(meilleur[0]))
            n += 1
    return n


def identifiant(brut):
    """Identifiant au format de l'appli : n123, w456, r789 (les « aires » osmium sont décodées)."""
    if brut[0] in "nwr":
        return brut
    if brut[0] == "a":
        n = int(brut[1:])
        return ("w%d" % (n // 2)) if n % 2 == 0 else ("r%d" % ((n - 1) // 2))
    return brut


def executer(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def construire(code, nom, dossier):
    pbf = os.path.join(dossier, f"{code}.osm.pbf")
    filtre = os.path.join(dossier, f"{code}.filtre.pbf")
    seq = os.path.join(dossier, f"{code}.geojsonseq")
    url = f"https://download.geofabrik.de/europe/{nom}-latest.osm.pbf"
    # Gros extraits (Allemagne : plus de 4 Go) : une coupure en route reprend où elle s'est arrêtée.
    for essai in range(6):
        try:
            executer(["curl", "-sSfL", "--retry", "8", "--retry-all-errors", "--retry-delay", "20",
                      "-C", "-", "-o", pbf, url])
            break
        except subprocess.CalledProcessError:
            if essai == 5:
                raise
    executer(["osmium", "tags-filter", "--overwrite", "-o", filtre, pbf] + FILTRE)
    os.remove(pbf)
    executer(["osmium", "export", "--overwrite", "-f", "geojsonseq", "-o", seq,
              "--geometry-types=point,linestring,polygon", "--add-unique-id=type_id", filtre])
    os.remove(filtre)

    pois, vus, attraits = [], set(), []
    with open(seq, encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip().lstrip("\x1e")
            if not ligne:
                continue
            o = json.loads(ligne)
            props = o.get("properties") or {}
            brut = o.get("id") or props.get("@id")
            if not brut:
                continue
            ident = identifiant(str(brut))
            tags = {k: str(v) for k, v in props.items() if not k.startswith("@")}
            a = attrait(tags)
            if a:
                c = centre(o.get("geometry") or {})
                if c:
                    attraits.append((c[0], c[1], a, tags.get("name:fr") or tags.get("name") or ""))
                # Une plage ou un point de vue n'est pas lui-même un point affiché
                # (sauf s'il est aussi, par exemple, un point d'eau).
                if categorie(tags, ident[0]) is None:
                    continue
            cat = categorie(tags, ident[0])
            if cat is None or ident in vus:
                continue
            c = centre(o.get("geometry") or {})
            if c is None:
                continue
            vus.add(ident)
            pois.append({"id": ident, "lat": c[0], "lon": c[1],
                         "tags": {k: v for k, v in tags.items() if k in TAGS_UTILES}})
    os.remove(seq)
    nb_spots = beaux_spots(pois, attraits)
    print(f"{code} : {len(attraits)} beaux lieux, {nb_spots} stationnements à proximité", flush=True)

    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sortie = os.path.join(dossier, f"{code}.json.gz")
    with gzip.open(sortie, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"pays": code, "version": date, "pois": pois}, f, ensure_ascii=False, separators=(",", ":"))
    compte = {}
    for p in pois:
        cat = categorie(p["tags"], p["id"][0])
        compte[cat] = compte.get(cat, 0) + 1
    meta = {"code": code, "fichier": f"{code}.json.gz", "taille": os.path.getsize(sortie),
            "nb": len(pois), "categories": compte, "version": date}
    with open(os.path.join(dossier, f"{code}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    print(f"{code} : {len(pois)} points, {meta['taille'] // 1024} Ko", flush=True)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--groupe", type=int, default=0)
    a.add_argument("--groupes", type=int, default=1)
    a.add_argument("--pays", default="")
    a.add_argument("--sortie", default="sortie")
    args = a.parse_args()
    voulus = {c.strip().upper() for c in args.pays.split(",") if c.strip()}
    liste = [p for p in PAYS if not voulus or p[0] in voulus]
    mes_pays = [p for i, p in enumerate(liste) if i % args.groupes == args.groupe]
    os.makedirs(args.sortie, exist_ok=True)
    echecs = []
    for code, nom in mes_pays:
        try:
            construire(code, nom, args.sortie)
        except Exception as e:  # un pays en échec ne bloque pas les autres
            print(f"ÉCHEC {code} : {e}", file=sys.stderr, flush=True)
            echecs.append(code)
    if echecs:
        print("Pays en échec :", ", ".join(echecs), file=sys.stderr)


if __name__ == "__main__":
    main()
