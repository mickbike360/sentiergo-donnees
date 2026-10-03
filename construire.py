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
import unicodedata
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
    "nwr/amenity=sanitary_dump_station,water_point,drinking_water,parking,toilets,shower,washing_machine,fuel,marketplace,restaurant",
    "n/amenity=vending_machine",
    "nwr/man_made=water_tap",
    "nwr/natural=spring",
    "nwr/shop=laundry,gas,supermarket,convenience,farm",
    "nwr/amenity=hospital", "nwr/healthcare=hospital",
    # Randonnée, VTT, ski, cols.
    "nwr/highway=trailhead", "nwr/leisure=bike_park", "nwr/sport=mtb", "nwr/landuse=winter_sports", "n/mountain_pass=yes",
    # Remontées : le pied des pistes d'une station de ski, et celles qui prennent les vélos l'été
    # (la plupart des bike parks de station ne sont notés que comme ça).
    # Villes et villages : « près de Morzine » pour une station ou un bike park au nom de domaine.
    "n/place=city,town,village",
    # Pistes VTT (difficulté notée) : réservées aux vélos près des remontées, c'est un bike park.
    "w/mtb:scale",
    "w/aerialway=gondola,chair_lift,cable_car,mixed_lift,drag_lift,t-bar,j-bar,platter,rope_tow,magic_carpet",
    # Obstacles pour un camping-car (passages bas, étroits, poids, fortes pentes) : alertes sans réseau.
    "w/maxheight", "w/maxheight:physical", "n/maxheight", "w/maxwidth", "w/maxweight", "w/incline",
    # Ce qui fait un beau spot : point de vue, plage, cascade (pas des points affichés, voir beaux_spots).
    "n/tourism=viewpoint",
    "nwr/natural=beach",
    "n/waterway=waterfall",
    # Zones à faibles émissions (ZFE, Umweltzonen…) : annoncées dès l'aperçu du trajet.
    "r/boundary=low_emission_zone",
]

# Tags gardés : ce que l'appli affiche ou utilise pour trier.
TAGS_UTILES = {
    "name", "name:fr", "operator", "fee", "charge", "capacity", "capacity:motorhome", "capacity:caravans",
    "opening_hours", "maxstay", "drinking_water", "water_point", "sanitary_dump_station", "power_supply",
    "toilets", "shower", "showers", "internet_access", "access", "seasonal", "maxheight", "maxheight:physical",
    "check_date", "survey:date", "description", "description:fr", "website", "contact:website", "phone",
    "contact:phone", "motorhome", "caravans", "caravan", "parking", "amenity", "tourism", "shop", "natural",
    "man_made", "fuel:lpg", "wheelchair",
    # Randonnée, VTT, ski, cols : de quoi les reconnaître dans l'appli, et l'altitude.
    "highway", "leisure", "sport", "landuse", "mountain_pass", "ele", "cuisine", "sg:velos", "sg:velos_ete", "sg:pres", "sg:pistes",
    # Stations-service : enseigne, gazole, AdBlue, accès poids lourds (gabarit).
    "brand", "fuel:diesel", "fuel:adblue", "hgv", "fuel:octane_95", "fuel:octane_98", "fuel:e85",
    # Ajoutés par beaux_spots : beau lieu à proximité.
    "sg:vue", "sg:vue_nom", "sg:vue_m",
    # Producteurs et marchés : produits, bio, type de distributeur.
    "produce", "organic", "vending",
    # Hôpitaux : urgences (emergency=yes/no).
    "emergency", "healthcare",
}

PARKING_EXCLUS = {"underground", "multi-storey", "rooftop", "street_side", "lane", "on_kerb", "half_on_kerb"}
ACCES_EXCLUS = {"private", "no", "customers", "permit", "delivery"}


def camping_ouvert(t):
    """Copie de Categorie.campingOuvert : ni interdit, ni réservé (scouts, groupes, bivouac, accès privé)."""
    return (t.get("motorhome") != "no" and t.get("caravans") != "no" and t.get("scout") != "yes"
            and t.get("group_only") != "yes" and t.get("backcountry") != "yes"
            and t.get("camp_site") not in ("basic", "scout") and t.get("access") not in ("private", "no"))


def confirme_camping_car(t):
    """Copie de Categorie.confirmeCampingCar : aire, camping qui accepte, ou parking marqué autorisé."""
    if t.get("tourism") == "caravan_site":
        return True
    if t.get("tourism") == "camp_site":
        return camping_ouvert(t)
    if t.get("amenity") == "parking":
        return t.get("motorhome") in ("yes", "designated") and t.get("access") not in ("private", "no")
    return False


# Copie de Categorie.PRODUITS_FERME : distributeurs à la ferme (œufs, lait, légumes…).
PRODUITS_FERME = re.compile(r"eggs|milk|fruit|vegetable|potato|cheese|meat|honey|œuf|oeuf|lait|légume|legume|fromage|miel", re.I)


def station_ouverte(t):
    """Copie de Categorie.stationOuverte : ni dépôt privé, ni station fermée, ni réservée aux bateaux ou camions."""
    return (t.get("access") not in ("private", "no", "permit", "delivery", "agricultural", "forestry")
            and t.get("motorcar") != "no" and t.get("motor_vehicle") != "no" and t.get("vehicle") != "no"
            and t.get("disused") != "yes" and t.get("abandoned") != "yes" and t.get("opening_hours") != "closed")


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
    if tourism == "camp_site" and camping_ouvert(t):
        return "CAMPING"
    if amenity == "toilets":
        return "TOILETTES"
    if amenity == "shower":
        return "DOUCHE"
    if shop == "laundry" or amenity == "washing_machine":
        return "LAVERIE"
    if amenity == "fuel" and station_ouverte(t) and t.get("fuel:lpg") == "yes":
        return "GPL"
    if amenity == "fuel" and station_ouverte(t):
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
    if amenity == "restaurant" and t.get("name"):
        return "RESTAURANT"
    if amenity == "hospital" or t.get("healthcare") == "hospital":
        return "HOPITAL"
    if t.get("highway") == "trailhead":
        return "RANDO"
    if t.get("sg:velos") or t.get("sg:pistes") or t.get("leisure") == "bike_park" or ("mtb" in (t.get("sport") or "").split(";")
                                          and t.get("leisure") in ("sports_centre", "park", "pitch", "track")):
        return "BIKE_PARK"
    if t.get("landuse") == "winter_sports" and t.get("name"):
        return "SKI"
    if t.get("mountain_pass") == "yes":
        return "COL"
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


def point_sur(geom):
    """Un point SUR l'objet (le sommet du milieu d'une rue) : la moyenne d'une rue en lacets peut tomber à côté."""
    g, c = geom.get("type"), geom.get("coordinates")
    if g == "Point":
        return round(c[1], 6), round(c[0], 6)
    if g == "LineString" and c:
        p = c[len(c) // 2]
        return round(p[1], 6), round(p[0], 6)
    if g == "Polygon" and c and c[0]:
        p = c[0][len(c[0]) // 2]
        return round(p[1], 6), round(p[0], 6)
    return centre(geom) if g else None


def attrait(t):
    """« viewpoint », « beach » ou « waterfall » si l'objet est un beau lieu, sinon None."""
    if t.get("tourism") == "viewpoint":
        return "viewpoint"
    if t.get("natural") == "beach":
        return "beach"
    if t.get("waterway") == "waterfall":
        return "waterfall"
    return None


# Remontées mécaniques qui transportent des vélos (le tapis ou le téléski, non : on n'y monte pas avec un vélo).
REMONTEES_VELO = {"gondola", "chair_lift", "cable_car", "mixed_lift"}
# Remontées dont la gare du bas peut servir de pied des pistes.
GARES = {"gondola", "chair_lift", "cable_car", "mixed_lift", "drag_lift", "t-bar", "j-bar", "platter", "rope_tow", "magic_carpet"}
# Pied des pistes : la gare du bas qui a le plus d'autres gares du bas à moins de 1 km.
RAYON_PIED_M = 1_000.0
# Deux remontées dont les gares du bas sont à moins de 1,2 km : la même station. Plus large, les
# remontées s'enchaînent d'un village à l'autre (Morzine, Les Gets, Châtel : un seul point).
RAYON_DOMAINE_M = 1_200.0
# Deux points « bike park » à moins de 800 m : le même (pistes notées une à une).
RAYON_BIKE_PARK_M = 800.0


def metres(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111_320.0, (a[1] - b[1]) * 111_320.0 * k)


def dans_anneau(lat, lon, anneau):
    """Point dans un contour [[lon, lat], …] (lancer de rayon)."""
    dedans = False
    j = len(anneau) - 1
    for i in range(len(anneau)):
        xi, yi = anneau[i][0], anneau[i][1]
        xj, yj = anneau[j][0], anneau[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans


def contours(geom):
    """Contours extérieurs d'un polygone ou multipolygone GeoJSON."""
    g, c = geom.get("type"), geom.get("coordinates")
    if g == "Polygon" and c:
        return [c[0]]
    if g == "MultiPolygon" and c:
        return [p[0] for p in c if p]
    return []


class Villages:
    """Villes et villages en cases de 0,1° : distance au plus proche (une gare au village, pas en montagne)."""

    def __init__(self, lieux):
        self.grille = {}
        for l in lieux:
            self.grille.setdefault((int(l[0] * 10 // 1), int(l[1] * 10 // 1)), []).append(l)

    def distance(self, pt):
        gi, gj = int(pt[0] * 10 // 1), int(pt[1] * 10 // 1)
        d = [metres(l, pt) for di in (-1, 0, 1) for dj in range(-3, 4) for l in self.grille.get((gi + di, gj + dj), ())]
        return min(d) if d else 99_999.0


# Une gare à moins de 1,5 km d'un village : au village (on y arrive en camping-car).
GARE_AU_VILLAGE_M = 1_500.0


def bike_parks(pois, remontees, domaines, villages=None):
    """
    Bike parks de station : les remontées qui prennent les vélos, regroupées par
    station (gares du bas à moins de 1,2 km). Un bike park déjà noté à moins de 1,2 km
    reçoit le nombre de remontées ; sinon un point est créé à la gare du bas la plus
    centrale, au nom du domaine skiable qui la contient (sinon celui de la remontée).
    Un téléphérique de ville qui accepte les vélos n'est pas un bike park : sans
    domaine skiable (une gare dedans) ni mention « l'été », le groupe est ignoré.
    Ensuite, les pistes notées une à une (leisure=track) d'un même bike park ne
    font plus qu'un point. Renvoie le nombre de bike parks ajoutés.
    """
    # Regroupement : union de proche en proche.
    groupes = []
    for r in remontees:
        proches = [g for g in groupes if any(metres(r, x) <= RAYON_DOMAINE_M for x in g)]
        nouveau = [r]
        for g in proches:
            nouveau += g
            groupes.remove(g)
        groupes.append(nouveau)
    existants = [p for p in pois if categorie(p["tags"], p["id"][0]) == "BIKE_PARK"]
    ajoutes = 0
    for g in groupes:
        ete = all(r[3] == "summer" for r in g)
        deja = [p for p in existants if any(metres((p["lat"], p["lon"]), r) <= RAYON_DOMAINE_M for r in g)]
        if deja:
            for p in deja:
                p["tags"]["sg:velos"] = str(len(g))
                if ete:
                    p["tags"]["sg:velos_ete"] = "yes"
            continue
        clat = sum(r[0] for r in g) / len(g)
        clon = sum(r[1] for r in g) / len(g)
        # La gare du bas la plus proche d'un village (Médran à Verbier, pas un télésiège à 2 200 m).
        bas = min(g, key=lambda r: (villages.distance(r) if villages else 0, metres(r, (clat, clon))))

        def domaine(r):
            return next((d[0] for d in domaines for a in d[1]
                         if dans_anneau(r[0], r[1], a) or dans_anneau(r[5], r[6], a)), None)
        noms = [n for n in (domaine(r) for r in [bas] + g) if n]
        if not noms and not any(r[3] == "summer" for r in g):
            continue
        nom = (noms[0] if noms else None) or bas[2]
        tags = {"sg:velos": str(len(g))}
        if ete:
            tags["sg:velos_ete"] = "yes"
        if nom:
            tags["name"] = nom
        pois.append({"id": bas[4], "lat": round(bas[0], 6), "lon": round(bas[1], 6), "tags": tags})
        ajoutes += 1
    # Un seul point par bike park : on garde le mieux décrit (bike_park, puis nommé, puis remontées).
    bp = [p for p in pois if categorie(p["tags"], p["id"][0]) == "BIKE_PARK"]
    bp.sort(key=lambda p: (p["tags"].get("leisure") != "bike_park", "name" not in p["tags"], "sg:velos" not in p["tags"]))
    gardes, retires = [], set()
    for p in bp:
        if any(metres((p["lat"], p["lon"]), (q["lat"], q["lon"])) <= RAYON_BIKE_PARK_M for q in gardes):
            retires.add(id(p))
        else:
            gardes.append(p)
    pois[:] = [p for p in pois if id(p) not in retires]
    return ajoutes


# Pistes VTT à moins de 1,5 km d'une gare de remontée : desservies par elle.
RAYON_PISTES_M = 1_500.0
# Deux bike parks repérés par leurs pistes : au moins 5 km entre leurs gares du bas.
FUSION_PISTES_M = 5_000.0
# Au moins 3 pistes VTT différentes près des remontées : un bike park.
PISTES_MIN = 3


def simple(nom):
    """Nom sans accents ni majuscules : « Commençal Superior » = « Commencal superior »."""
    return "".join(ch for ch in unicodedata.normalize("NFD", nom) if unicodedata.category(ch) != "Mn").lower().strip()


def piste_vtt(t):
    """Une piste VTT : chemin avec une difficulté VTT, réservé aux vélos (pas un sentier de randonnée)."""
    return (t.get("highway") in ("path", "track", "cycleway", "bridleway") and t.get("mtb:scale") is not None
            and (t.get("bicycle") == "designated" or t.get("mtb") == "designated"))


def bike_parks_pistes(pois, telesieges, pistes, domaines, villages=None):
    """
    Bike parks que rien ne nomme ainsi (Vallnord, Châtel…) : des remontées (gares du
    bas à moins de 1,2 km) avec au moins 3 pistes VTT réservées aux vélos à moins de
    1,5 km d'une de leurs gares. Les groupes qui partagent des pistes ne font qu'un.
    Le point : la gare du bas d'une télécabine s'il y en a une (le village), sinon
    celle qui a le plus de pistes autour. Un bike park déjà connu tout près reçoit le
    nombre de pistes. Rend le nombre de bike parks ajoutés.
    """
    grille = {}
    for p in pistes:
        grille.setdefault((int(p[0] * 50 // 1), int(p[1] * 50 // 1)), []).append(p)

    def autour(pt):
        gi, gj = int(pt[0] * 50 // 1), int(pt[1] * 50 // 1)
        return {p[2] for di in (-1, 0, 1) for dj in range(-2, 3) for p in grille.get((gi + di, gj + dj), ())
                if metres(p, pt) <= RAYON_PISTES_M}
    groupes = []
    for r in telesieges:
        proches = [g for g in groupes if any(metres(r, x) <= RAYON_DOMAINE_M for x in g[0])]
        n = ([r], autour(r) | autour((r[5], r[6])))
        for g in proches:
            n = (n[0] + g[0], n[1] | g[1])
            groupes.remove(g)
        groupes.append(n)
    # Un bike park par groupe de remontées qui a ses pistes, à plus de 5 km d'un autre : au village
    # d'abord, puis le plus de pistes (Vallnord : la télécabine de La Massana ; Leogang et Saalbach :
    # deux points, même si leurs pistes se rejoignent au sommet).
    def au_village(g):
        return bool(villages) and any(villages.distance(r) <= GARE_AU_VILLAGE_M for r in g[0])
    retenus = []
    for g in sorted([g for g in groupes if len(g[1]) >= PISTES_MIN], key=lambda g: (not au_village(g), -len(g[1]))):
        if any(metres(a, b) <= FUSION_PISTES_M for a in g[0] for f in retenus for b in f[0]):
            continue
        retenus.append(g)
    existants = [p for p in pois if categorie(p["tags"], p["id"][0]) == "BIKE_PARK"]
    ajoutes = 0
    for lifts, noms in retenus:
        # Nombre affiché : les pistes nommées (une piste sans nom est souvent faite de plusieurs tronçons).
        nommees = len([n for n in noms if not n.startswith("#")]) or len(noms)
        deja = [p for p in existants if any(metres((p["lat"], p["lon"]), r) <= RAYON_DOMAINE_M for r in lifts)]
        if deja:
            for p in deja:
                p["tags"]["sg:pistes"] = str(nommees)
            continue
        cabines = [r for r in lifts if r[3] in ("gondola", "cable_car", "mixed_lift")]
        gares_village = [r for r in (cabines or lifts) if villages and villages.distance(r) <= GARE_AU_VILLAGE_M]
        bas = max(gares_village or cabines or lifts, key=lambda r: len(autour(r)))
        nom = next((d[0] for d in domaines for a in d[1]
                    if any(dans_anneau(r[0], r[1], a) or dans_anneau(r[5], r[6], a) for r in lifts)), None)
        tags = {"sg:pistes": str(nommees)}
        if nom:
            tags["name"] = nom
        pois.append({"id": bas[4], "lat": round(bas[0], 6), "lon": round(bas[1], 6), "tags": tags})
        existants.append(pois[-1])
        ajoutes += 1
    return ajoutes


# Village le plus proche d'une station ou d'un bike park : à moins de 8 km.
RAYON_PRES_M = 8_000.0


def pres_de(pois, lieux):
    """Tag sg:pres (nom du village, de la ville la plus proche) des stations de ski et bike parks."""
    grille = {}
    for l in lieux:
        grille.setdefault((int(l[0] * 10 // 1), int(l[1] * 10 // 1)), []).append(l)
    n = 0
    for p in pois:
        if categorie(p["tags"], p["id"][0]) not in ("SKI", "BIKE_PARK"):
            continue
        gi, gj = int(p["lat"] * 10 // 1), int(p["lon"] * 10 // 1)
        # Cases de 0,1° : 11 km en latitude, moins en longitude vers le nord (3,8 km en Laponie).
        proches = [l for di in (-1, 0, 1) for dj in range(-3, 4) for l in grille.get((gi + di, gj + dj), ())]
        if not proches:
            continue
        l = min(proches, key=lambda l: metres(l, (p["lat"], p["lon"])))
        # Un village cité dans le nom du domaine, à 3 km de plus au plus, passe avant le hameau le
        # plus proche (« Skicircus Saalbach-Hinterglemm Leogang Fieberbrunn » : Leogang, pas Rain).
        nom_p = simple(p["tags"].get("name") or "")
        cites = [x for x in proches if len(x[2]) >= 3 and simple(x[2]) in nom_p
                 and metres(x, (p["lat"], p["lon"])) <= min(RAYON_PRES_M, metres(l, (p["lat"], p["lon"])) + 3_000)]
        if cites:
            l = min(cites, key=lambda x: metres(x, (p["lat"], p["lon"])))
        # Le nom du lieu est déjà celui du village (« Morzine » près de Morzine) : rien à ajouter.
        if metres(l, (p["lat"], p["lon"])) <= RAYON_PRES_M and l[2] not in (p["tags"].get("name"), p["tags"].get("name:fr")):
            p["tags"]["sg:pres"] = l[2]
            n += 1
    return n


def fusion_par_village(pois):
    """
    Bike parks de station au même nom de domaine et près du même village (L'Alpe d'Huez
    a des gares à plus de 1,2 km l'une de l'autre) : un seul point, celui qui a le plus
    de remontées, avec leur total. Rend le nombre de points retirés.
    """
    groupes = {}
    for p in pois:
        t = p["tags"]
        if (t.get("sg:velos") or t.get("sg:pistes")) and not t.get("leisure") and t.get("sg:pres") and t.get("name"):
            groupes.setdefault((t["name"], t["sg:pres"]), []).append(p)
    retires = set()
    for g in groupes.values():
        if len(g) < 2:
            continue
        # Celui qui a le plus de remontées à vélo, puis de pistes ; il reçoit le total des remontées
        # et le plus grand nombre de pistes (les pistes de l'un sont souvent celles de l'autre).
        garde = max(g, key=lambda p: (int(p["tags"].get("sg:velos", "0")), int(p["tags"].get("sg:pistes", "0"))))
        velos = sum(int(p["tags"].get("sg:velos", "0")) for p in g)
        pistes = max(int(p["tags"].get("sg:pistes", "0")) for p in g)
        if velos:
            garde["tags"]["sg:velos"] = str(velos)
            if any(p["tags"].get("sg:velos") and p["tags"].get("sg:velos_ete") != "yes" for p in g):
                garde["tags"].pop("sg:velos_ete", None)
        if pistes:
            garde["tags"]["sg:pistes"] = str(pistes)
        retires.update(id(p) for p in g if p is not garde)
    pois[:] = [p for p in pois if id(p) not in retires]
    return len(retires)


def pied_des_pistes(pois, gares, domaines, villages=None):
    """
    Une station de ski placée au centre de son domaine tombe en pleine montagne (un
    itinéraire y mènerait n'importe où) : elle est ramenée au pied des pistes, la gare
    du bas de remontée du domaine autour de laquelle il y en a le plus (le village,
    le front de neige). Sans remontée connue dans le domaine, le point ne bouge pas.
    Renvoie le nombre de stations déplacées.
    """
    par_id = {p["id"]: p for p in pois}
    n = 0
    for nom, anneaux_d, ident in domaines:
        p = par_id.get(ident)
        if p is None:
            continue
        lats = [q[1] for a in anneaux_d for q in a]
        lons = [q[0] for a in anneaux_d for q in a]
        dedans = [g for g in gares
                  if min(lats) <= g[0] <= max(lats) and min(lons) <= g[1] <= max(lons)
                  and any(dans_anneau(g[0], g[1], a) for a in anneaux_d)]
        if not dedans:
            continue
        centre_d = (p["lat"], p["lon"])
        # Une gare au village d'abord (s'il y en a), puis la plus entourée.
        au_village = [g for g in dedans if villages and villages.distance(g) <= GARE_AU_VILLAGE_M]
        pied = max(au_village or dedans, key=lambda g: (sum(1 for h in dedans if metres(g, h) <= RAYON_PIED_M), -metres(g, centre_d)))
        p["lat"], p["lon"] = round(pied[0], 6), round(pied[1], 6)
        n += 1
    return n


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


NOMBRE = re.compile(r"(\d+(?:[.,]\d+)?)")


def nombre(v):
    m = NOMBRE.search(v or "")
    return float(m.group(1).replace(",", ".")) if m else None


def obstacle(t, type_osm):
    """
    Copie des seuils utiles d'Alertes.kt (au plus large : les gabarits varient) :
    hauteur < 4,5 m, largeur < 3,5 m, poids < 12 t, pente d'au moins 8 %. Renvoie
    les tags à garder, ou None. Les pentes ne comptent que sur une route (highway).
    """
    garde = {}
    # Une rue seulement : la hauteur d'un parking (zone dessinée) ne concerne pas la route.
    if type_osm == "w" and not t.get("highway"):
        return None
    h = nombre(t.get("maxheight") or t.get("maxheight:physical"))
    if h is not None and 0.5 < h < 4.5:
        for k in ("maxheight", "maxheight:physical"):
            if k in t:
                garde[k] = t[k]
    if type_osm == "w":
        l = nombre(t.get("maxwidth"))
        if l is not None and 0.5 < l < 3.5:
            garde["maxwidth"] = t["maxwidth"]
        p = nombre(t.get("maxweight"))
        if p is not None and 0.5 < p < 12:
            garde["maxweight"] = t["maxweight"]
        inc = t.get("incline") or ""
        if t.get("highway") and ("%" in inc or "°" in inc):
            n = nombre(inc)
            if n is not None:
                pct = math.tan(math.radians(n)) * 100 if "°" in inc else n
                if abs(pct) >= 8:
                    garde["incline"] = inc
    # Cabine de péage : l'appli ne la retient que sur notre voie.
    if garde and t.get("barrier"):
        garde["barrier"] = t["barrier"]
    if garde and t.get("amenity") == "parking_entrance":
        garde["amenity"] = "parking_entrance"
    return garde or None


def rues_porteuses(obstacles, pbf, dossier):
    """
    Pour chaque obstacle ponctuel (portique, cabine de péage…), les rues qui le
    portent (tag sg:voies). L'appli ne le signale que si le trajet emprunte l'une
    d'elles : un portique de la rue voisine, à 3 ou 5 m, n'est plus une fausse alerte.
    Sans « osmium getparents » (version ancienne), rien n'est ajouté : l'appli garde
    alors sa règle de proximité.
    """
    noeuds = {o["id"] for o in obstacles if o["id"].startswith("n")}
    if not noeuds:
        return
    ids = os.path.join(dossier, "noeuds.txt")
    opl = os.path.join(dossier, "parents.opl")
    with open(ids, "w") as f:
        f.write("\n".join(sorted(noeuds)))
    try:
        executer(["osmium", "getparents", "--overwrite", "-i", ids, "-f", "opl", "-o", opl, pbf])
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"getparents indisponible : {e}", flush=True)
        return
    finally:
        os.remove(ids)
    porteuses = {}
    with open(opl, encoding="utf-8") as f:
        for ligne in f:
            champs = ligne.split()
            if not champs or not champs[0].startswith("w"):
                continue
            tags = next((c[1:] for c in champs if c.startswith("T")), "")
            if "highway=" not in tags:
                continue
            refs = next((c[1:] for c in champs if c.startswith("N")), "")
            for r in refs.split(","):
                if r in noeuds:
                    porteuses.setdefault(r, []).append(champs[0][1:])
    os.remove(opl)
    for o in obstacles:
        v = porteuses.get(o["id"])
        if v:
            o["tags"]["sg:voies"] = ";".join(v)
    print(f"{len(porteuses)}/{len(noeuds)} obstacles ponctuels rattachés à leur rue", flush=True)


# Tags gardés pour une zone à faibles émissions.
TAGS_ZONE = ("name", "name:fr", "website", "description", "start_date", "operator")


def anneaux(geom):
    """Contours d'un (multi)polygone, allégés : un point tous les 30 m environ, 5 décimales (~1 m)."""
    g, c = geom.get("type"), geom.get("coordinates") or []
    polys = [c] if g == "Polygon" else c if g == "MultiPolygon" else []
    res = []
    for poly in polys:
        for ring in poly:
            garde = []
            for lon, lat in ring:
                if garde:
                    dl, dn = lat - garde[-1][0], (lon - garde[-1][1]) * math.cos(math.radians(lat))
                    if (dl * dl + dn * dn) ** 0.5 * 111_000 < 30:
                        continue
                garde.append([round(lat, 5), round(lon, 5)])
            if len(garde) >= 3:
                res.append(garde)
    return res


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
    executer(["osmium", "export", "--overwrite", "-f", "geojsonseq", "-o", seq,
              "--geometry-types=point,linestring,polygon", "--add-unique-id=type_id", filtre])
    os.remove(filtre)

    pois, vus, attraits, obstacles, deja_obstacles, zones = [], set(), [], [], set(), []
    remontees, domaines, gares, lieux, telesieges, pistes = [], [], [], [], [], []
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
            if tags.get("boundary") == "low_emission_zone":
                a = anneaux(o.get("geometry") or {})
                if a:
                    zones.append({"id": ident, "tags": {k: v for k, v in tags.items() if k in TAGS_ZONE}, "anneaux": a})
                continue
            if tags.get("place") in ("city", "town", "village") and ident[0] == "n":
                g = o.get("geometry") or {}
                if g.get("type") == "Point" and (tags.get("name:fr") or tags.get("name")):
                    lieux.append((g["coordinates"][1], g["coordinates"][0], tags.get("name:fr") or tags["name"]))
                if categorie(tags, ident[0]) is None:
                    continue
            if piste_vtt(tags):
                c = point_sur(o.get("geometry") or {})
                if c:
                    # Sans nom : chaque tronçon compte à part (identifiant), pour le seuil seulement.
                    pistes.append((c[0], c[1], simple(tags["name"]) if tags.get("name") else "#" + ident))
            if tags.get("aerialway") in GARES:
                g = o.get("geometry") or {}
                if g.get("type") == "LineString" and g.get("coordinates"):
                    # Une remontée est tracée de bas en haut : le premier point est la gare du bas.
                    lon0, lat0 = g["coordinates"][0][:2]
                    lon1, lat1 = g["coordinates"][-1][:2]
                    gares.append((lat0, lon0))
                    if tags["aerialway"] in REMONTEES_VELO:
                        telesieges.append((lat0, lon0, tags.get("name"), tags["aerialway"], ident, lat1, lon1))
                    if tags["aerialway"] in REMONTEES_VELO and tags.get("aerialway:bicycle") in ("yes", "summer", "designated", "yes|summer"):
                        ete = "summer" if tags["aerialway:bicycle"] == "summer" else "yes"
                        remontees.append((lat0, lon0, tags.get("name:fr") or tags.get("name"), ete, ident, lat1, lon1))
                continue
            if tags.get("landuse") == "winter_sports" and tags.get("name"):
                a = contours(o.get("geometry") or {})
                if a:
                    domaines.append((tags.get("name:fr") or tags["name"], a, ident))
            ob = obstacle(tags, ident[0]) if ident[0] in "nw" and ident not in deja_obstacles else None
            if ob:
                deja_obstacles.add(ident)
                c = point_sur(o.get("geometry") or {})
                if c:
                    obstacles.append({"id": ident, "lat": round(c[0], 6), "lon": round(c[1], 6), "tags": ob})
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
    rues_porteuses(obstacles, pbf, dossier)
    os.remove(pbf)
    nb_spots = beaux_spots(pois, attraits)
    villages = Villages(lieux)
    nb_velos = bike_parks(pois, remontees, domaines, villages)
    print(f"{code} : {len(remontees)} remontées avec vélos, {nb_velos} bike parks ajoutés", flush=True)
    nb_pistes = bike_parks_pistes(pois, telesieges, pistes, domaines, villages)
    print(f"{code} : {len(pistes)} pistes VTT, {nb_pistes} bike parks ajoutés par leurs pistes", flush=True)
    nb_pieds = pied_des_pistes(pois, gares, domaines, villages)
    print(f"{code} : {nb_pieds} stations de ski sur {len(domaines)} ramenées au pied des pistes", flush=True)
    print(f"{code} : {pres_de(pois, lieux)} stations et bike parks situés près d'un village", flush=True)
    print(f"{code} : {fusion_par_village(pois)} bike parks fusionnés (même domaine, même village)", flush=True)
    print(f"{code} : {len(attraits)} beaux lieux, {nb_spots} stationnements à proximité", flush=True)

    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sortie = os.path.join(dossier, f"{code}.json.gz")
    with gzip.open(sortie, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"pays": code, "version": date, "pois": pois, "obstacles": obstacles, "zones": zones}, f,
                  ensure_ascii=False, separators=(",", ":"))
    compte = {}
    for p in pois:
        cat = categorie(p["tags"], p["id"][0])
        compte[cat] = compte.get(cat, 0) + 1
    meta = {"code": code, "fichier": f"{code}.json.gz", "taille": os.path.getsize(sortie),
            "nb": len(pois), "categories": compte, "obstacles": len(obstacles), "zones": len(zones), "version": date}
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
