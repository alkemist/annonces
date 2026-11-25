# Collecte des annonces

La colonne `distance` et le filtre `max_distance` utilisent la distance routière
en voiture entre le point `base_location` et le centre de la commune de l'annonce,
en kilomètres. `base_location` est obligatoire dans chaque recherche de `SEARCHES`,
au format `(longitude, latitude)`. Le code postal reste utilisé pour rechercher
les annonces ; même pour un code postal identique, la distance est calculée vers
le centre de la commune. La commune est identifiée avec son nom et son code postal
par le géocodage IGN ; une ville ou un code postal introuvable laisse la
distance vide. L'itinéraire reste une estimation du trajet en voiture : le
géocodage ne garantit pas l'exactitude des sens de circulation.
Les numéros d'arrondissement de Paris, Lyon et Marseille sont retirés des noms
de ville lorsque le code postal est présent ; ce dernier reste inchangé.
Pour une ancienne commune absente des résultats administratifs, un lieu ou une
rue mentionnant explicitement son nom peut servir de point approximatif ; ce
choix est signalé dans les logs.
Lorsqu'un nom contient plusieurs communes, le premier résultat IGN dont le nom
correspond à une partie du nom et dont le code postal correspond est utilisé
comme point approximatif.

Le CSV et le tableau sont triés par distance croissante, puis par loyer total
croissant à distance égale. Les valeurs manquantes sont placées après les
valeurs renseignées. Dans le tableau, les annonces restent regroupées par ville.
Les variantes de casse, d'accents, de séparateurs et d'abréviations « St » et
« Ste » sont regroupées sous une même graphie présente dans les annonces.
Les codes postaux différents, ou absents, restent distincts.

Les trajets sont calculés par l'API
[Géoplateforme IGN](https://cartes.gouv.fr/aide/fr/guides-utilisateur/utiliser-les-services-de-la-geoplateforme/calcul-itineraire/),
sans clé API. La collecte limite ces appels à une requête par seconde par processus.

CDC Habitat (`cdchabitat`) parcourt les annonces de location intermédiaires,
sociales et à loyer libre dans l'Ain avant application de `max_distance`.
Les annonces des départements voisins ne sont donc pas incluses, même si elles
respectent cette distance. Cette collecte n'est pas limitée aux 100 premières
annonces et peut être longue, notamment à cause du calcul des distances routières.

Pour les locations Leboncoin sans loyer hors charges ni montant de charges
détaillés, le prix affiché est utilisé comme loyer et loyer total, avec 0 en
charges pour éviter de les compter deux fois. Ce 0 ne signifie pas que les
charges réelles sont nulles.

En cas d'erreur 419 ou 429 sur un site, la collecte attend cinq minutes puis
réessaie une fois la même page. Si le blocage persiste, elle s'arrête avec une
erreur.

```powershell
python collect.py
```

Les CSV et le rapport existants ne sont actualisés qu'à la prochaine collecte.
