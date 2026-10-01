# SentierGo — données

Paquets de points utiles aux camping-cars, un par pays européen, fabriqués chaque semaine à partir d'OpenStreetMap (extraits [Geofabrik](https://download.geofabrik.de/europe.html)) :

- eau potable et sources ;
- vidange ;
- aires et campings ;
- parkings autorisés et grands parkings ;
- toilettes, douches et laveries ;
- stations-service (enseigne, gazole, AdBlue, accès poids lourds), GPL et gaz ;
- supermarchés.

L'appli SentierGo télécharge `index.json` au démarrage, puis les paquets des pays choisis lorsqu'ils ont changé.

- `construire.py` : fabrique `XX.json.gz` et `XX.meta.json` pour une liste de pays (nécessite `osmium-tool`).
- `index.py` : assemble `index.json`.
- `.github/workflows/paquets.yml` : fabrication hebdomadaire et publication dans la release `donnees`.

Données © contributeurs OpenStreetMap, sous licence ODbL : <https://www.openstreetmap.org/copyright>.
