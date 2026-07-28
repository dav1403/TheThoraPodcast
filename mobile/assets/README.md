# mobile/assets — sources icônes & splash

Ce dossier contient les **images sources** à partir desquelles
[`@capacitor/assets`](https://github.com/ionic-team/capacitor-assets) génère
**toutes** les déclinaisons iOS et Android (des dizaines de tailles).

Aucune image binaire n'est versionnée ici pour l'instant : **il faut les fournir.**

## Fichiers attendus (noms exacts, à déposer dans ce dossier)

| Fichier | Taille | Format | Rôle |
|---|---|---|---|
| `icon.png` | **1024 × 1024** | PNG **sans** transparence, **sans** coins arrondis | Icône de l'app (iOS + Android legacy). Apple refuse un canal alpha. |
| `icon-foreground.png` | **1024 × 1024** | PNG **avec** transparence | Calque avant de l'icône adaptative Android. Garder le motif dans le cercle central de ~660 px (le reste est rogné). |
| `icon-background.png` | **1024 × 1024** | PNG opaque (ou aplat de couleur) | Calque arrière de l'icône adaptative Android. |
| `splash.png` | **2732 × 2732** | PNG | Écran de lancement, thème clair. Logo centré dans un carré de ~1200 px (le reste est rogné selon le ratio de l'appareil). |
| `splash-dark.png` | **2732 × 2732** | PNG | Idem, thème sombre. Si absent, `splash.png` est réutilisé. |

## Source disponible dans le repo

`../../artwork/og-banner.png` (1 200 × 630 environ) est la bannière du site.
Elle est **au mauvais ratio** pour servir directement d'icône ou de splash :
c'est une source de **design**, pas un asset prêt à l'emploi.

Ce qu'il faut faire (geste graphique, à la main dans Figma/Photoshop/Canva) :

- **icône** : extraire le logo seul, le centrer sur un carré 1024 × 1024, fond
  `#1a1a2e` (couleur de marque déjà utilisée dans `capacitor.config.ts`),
  marge de sécurité d'au moins 10 % sur chaque bord ;
- **splash** : même logo centré, beaucoup plus petit (≈ 30 % de la largeur),
  sur un carré 2732 × 2732 uni `#1a1a2e`.

## Génération

Une fois les PNG déposés :

```bash
cd mobile
npm install
npx cap add ios       # sur macOS uniquement
npx cap add android
npm run assets        # génère icônes + splash dans ios/ et android/
npx cap sync
```

`npm run assets` écrit directement dans `mobile/ios/App/App/Assets.xcassets/`
et `mobile/android/app/src/main/res/`. Ces dossiers sont générés — ne pas les
éditer à la main, relancer la commande.

## Contrôle

- iOS : ouvrir Xcode → `App > Assets.xcassets > AppIcon`, toutes les cases
  doivent être remplies (une case vide = rejet à l'upload App Store Connect).
- Android : `android/app/src/main/res/mipmap-*` doit contenir
  `ic_launcher.png`, `ic_launcher_round.png`, `ic_launcher_foreground.png`.
