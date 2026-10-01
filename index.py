#!/usr/bin/env python3
"""
Assemble index.json à partir des XX.meta.json fabriqués, en gardant les
entrées de l'index précédent pour les pays qui n'ont pas été refaits
(un pays en échec garde ainsi sa dernière version publiée).

Usage : index.py <dossier des métas> <index précédent ou ""> <index de sortie>
"""
import datetime
import glob
import json
import os
import sys


def main():
    dossier, ancien, sortie = sys.argv[1], sys.argv[2], sys.argv[3]
    pays = {}
    if ancien and os.path.exists(ancien):
        try:
            pays = json.load(open(ancien, encoding="utf-8")).get("pays", {})
        except Exception:
            pays = {}
    for f in glob.glob(os.path.join(dossier, "**", "*.meta.json"), recursive=True):
        m = json.load(open(f, encoding="utf-8"))
        pays[m["code"]] = m
    index = {
        "format": 1,
        "genere": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "© contributeurs OpenStreetMap (ODbL), extraits Geofabrik",
        "pays": dict(sorted(pays.items())),
    }
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"index : {len(pays)} pays")


if __name__ == "__main__":
    main()
