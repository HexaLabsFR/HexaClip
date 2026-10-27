# HexaClip (Hexa Labs)

Outil Windows pour **CS2**, façon Allstar : il enregistre tout seul quand le jeu est lancé, garde **chacun de tes kills**, te laisse **choisir les kills** qui vont dans la **compil TikTok verticale** (logo, compteur de kills, écran de fin Elo), et suit ton **grind Faceit**. Mises à jour automatiques comme HexaRise.

## Ce que fait HexaClip

| Moment | Ce qui se passe |
|---|---|
| Démarrage de Windows | HexaClip se lance discrètement dans la barre des tâches (option au premier lancement) |
| CS2 lancé | Enregistrement en boucle des 3 dernières minutes, encodé par la carte graphique (NVIDIA, AMD ou Intel) |
| Chaque kill | Détecté en direct par l'intégration officielle de CS2 (GSI) : un clip par round, avec un passage par kill |
| Touche F9 | Sauve à la main les 15 dernières secondes |
| CS2 fermé | Notification « Session terminée » : tu choisis tes kills dans l'onglet **Montage** (sélection auto déjà cochée), puis **Créer la compil** (1080×1920) |
| Nouvelle version | Téléchargée en fond, bouton **Redémarrer** pour l'installer (ou installée en quittant) |
| En continu | Elo Faceit relevé, courbe et Elo gagné/perdu par match |

Rien n'est lu dans la mémoire du jeu : HexaClip utilise uniquement l'intégration GSI de Valve (un fichier `.cfg` installé dans le dossier de CS2) et la capture d'écran de Windows.

## Structure

| Dossier | Contenu |
|---|---|
| `src/hexaclip.py` | Point d'entrée et interface (Accueil, Montage, Clips, Compils, Faceit, Réglages) |
| `src/engine.py` | Moteur : surveillance de CS2, sessions, clips, compils, Faceit |
| `src/recorder.py` | Enregistrement en boucle (FFmpeg ddagrab + son WASAPI) et extraction des clips |
| `src/moments.py` | Détection des kills (GSI), note des rounds, sélection automatique et choix des kills |
| `src/updater.py` | Mises à jour automatiques depuis les Releases GitHub |
| `src/montage.py` | Montage de la compil verticale |
| `src/faceit.py` | API Faceit officielle (Data API v4) |
| `src/selftest.py` | Vérification complète sans jeu (fausse partie + faux écran) |
| `src/assets/` | Logo (piste « Coupe »), police Space Grotesk |
| `build/` | Fabrication de l'exe (PyInstaller) et des images du logo (`make_assets.py`) |
| `.github/workflows/publier.yml` | Fabrication, vérification et publication automatiques |

## Publier une nouvelle version

1. Modifie les fichiers dans `src/`.
2. Augmente le numéro dans `src/hexaclip.py` : `VERSION = "0.1.1"`.
3. Décris les nouveautés dans `NOUVEAUTES.txt`.
4. Envoie sur `main`.

GitHub fabrique `HexaClip.exe` sur Windows, le vérifie (`--selftest` : détection des kills simulés, enregistrement d'un faux écran, clip à deux passages recollés, compil 1080×1920 avec les kills choisis) et le publie dans les **Releases**. Les HexaClip déjà installées la trouvent toutes seules (au démarrage puis toutes les 4 h) : le dépôt doit rester **public** pour ça. Si le numéro ne change pas, l'exe est seulement fabriqué et vérifié (onglet Actions).

## Installer

1. Télécharge `HexaClip.exe` dans les Releases et range-le où tu veux (ex. `C:\Hexa Labs\HexaClip`).
2. Au premier lancement, Windows SmartScreen peut afficher « Windows a protégé votre ordinateur » (exe non signé) : **Informations complémentaires > Exécuter quand même**.
3. HexaClip télécharge une seule fois son moteur vidéo (FFmpeg, ~100 Mo) et installe la liaison CS2.
4. Relance CS2 s'il était ouvert.

### Suivi Faceit

1. Va sur [developers.faceit.com](https://developers.faceit.com/), connecte-toi avec ton compte Faceit.
2. Crée une app dans **App Studio**, puis une clé **API Keys > Server side**.
3. Dans HexaClip, onglet **Réglages** : ton pseudo Faceit exact + la clé, puis **Tester**.

## Où sont les fichiers

| Quoi | Où |
|---|---|
| Clips et compils | `Vidéos\HexaClip` (modifiable dans les réglages) |
| Réglages, base, journal, FFmpeg | `Documents\Hexa Labs\HexaClip` |

Les clips de plus de 7 jours sont supprimés automatiquement (réglable), sauf les favoris (★). Les compils sont gardées. Avec tous les kills gardés, compte environ 0,5 Go par match.

## Lancer depuis le code (développement)

```
pip install -r requirements.txt
python src/hexaclip.py            # interface (Windows)
python src/hexaclip.py --selftest # vérification (Windows ou Linux, FFmpeg dans le PATH)
```

## Choisir ses kills (onglet Montage)

- Une ligne par round, groupée par partie (map), avec l'image du round et une puce par kill (« Kill 2 · HS »).
- Clic sur une puce : ajoute ou enlève le kill. Clic droit : revoit ce kill. **▶ Voir** : revoit le round.
- **Sélection auto** : favoris, puis aces, 4k, 3k… et kills à la tête, jusqu'à la durée de compil (1 min par défaut).
- Le compteur en bas donne le nombre de kills et la durée ; **Créer la compil** monte la vidéo.

## Limites connues

- Les clutchs ne sont pas détectés en tant que tels : CS2 ne dit pas au joueur combien d'alliés sont en vie (un clutch à 3 kills et plus est quand même gardé).
- Capture de l'écran principal par défaut : si CS2 tourne sur un autre écran, change-le dans les réglages.
- Le son capté est celui de la sortie par défaut de Windows (ton casque) : Discord aussi s'entend s'il passe par la même sortie.
