# The Torah Podcast — app iOS / Android (Capacitor)

Wrapper natif autour du site existant **https://thetorahpodcast.net**.
L'app embarque un WebView qui charge le site en direct : **tout nouveau contenu
publié sur le site apparaît instantanément dans l'app, sans passer par une
revue de store.** Seules les modifications du shell (icône, permissions,
plugins natifs) nécessitent un nouveau build et une nouvelle soumission.

---

## ⚠️ À lire en premier : ce qui NE peut PAS être fait depuis cet environnement

Ce dépôt est édité depuis **Windows / un environnement cloud**. Concrètement :

- **Aucun `.ipa` ne peut être produit ici.** Compiler et signer une app iOS
  exige **macOS + Xcode**. Pas de contournement légal (les fermes cloud type
  Codemagic / MacStadium sont des Mac distants, pas une exception).
- **Aucun `.aab` Android n'a été produit ici** (Android Studio / le SDK Android
  ne sont pas installés sur la machine de David — non vérifié en direct, mais
  aucun build n'a été tenté ni réussi).
- **Aucune publication sur les stores n'a été faite.** Elle exige des comptes
  développeur nominatifs et une authentification à deux facteurs.
- Les dossiers `ios/` et `android/` **n'existent pas dans ce repo** : ils sont
  générés par `npx cap add ios|android` (et volontairement gitignorés, cf. plus bas).

Ce qui EST livré ici : le projet Capacitor complet, configuré, prêt à
`npm install && npx cap add …`. Le reste est une suite de gestes machine.

---

## Structure

```
mobile/
├── package.json                  deps + scripts
├── capacitor.config.ts           config du shell (appId, server.url, splash…)
├── tsconfig.json                 typecheck de la config
├── www/
│   ├── index.html                shell de repli (webDir obligatoire)
│   └── error.html                page hors-connexion (server.errorPath)
├── assets/README.md              icônes/splash à fournir → @capacitor/assets
├── ios-Info.plist.snippet        clés Info.plist + AppDelegate (audio background)
└── android-background-audio.md   foreground service Android (audio background)
```

`ios/`, `android/` et `node_modules/` sont **générés** et non versionnés.

## Identité de l'app

| Champ | Valeur |
|---|---|
| Bundle ID / applicationId | `net.thetorahpodcast.app` |
| Nom affiché | The Torah Podcast |
| URL chargée | `https://thetorahpodcast.net` (HTTPS strict, `cleartext: false`) |
| Couleur de marque | `#1a1a2e` |
| Capacitor | 6.x |

Les liens hors `thetorahpodcast.net` (et R2/Cloudflare pour les médias)
s'ouvrent dans le navigateur système au lieu de prendre le contrôle du WebView —
c'est une exigence des deux stores.

---

## Prérequis

| Pour | Nécessaire |
|---|---|
| Tout | Node.js ≥ 20, npm |
| Android | Android Studio (Ladybug+), JDK 17, SDK API 35 |
| iOS | **macOS**, Xcode 15+, CocoaPods (`sudo gem install cocoapods`) |

Sur Windows on peut faire tourner la partie Android intégralement.
La partie iOS est **bloquée** : il faut un Mac.

## Runbook

```bash
cd mobile
npm install

# 1. Générer les projets natifs (une seule fois)
npx cap add android
npx cap add ios          # macOS uniquement

# 2. Appliquer les patchs manuels documentés
#    - iOS   : ios-Info.plist.snippet     -> ios/App/App/Info.plist + AppDelegate.swift
#    - Android: android-background-audio.md -> AndroidManifest.xml + 2 fichiers Java

# 3. Déposer les images dans assets/ (cf. assets/README.md) puis :
npm run assets

# 4. Synchroniser config + plugins vers les projets natifs
npx cap sync

# 5. Ouvrir dans l'IDE
npm run open:android     # Android Studio
npm run open:ios         # Xcode (macOS)
```

`npx cap sync` est à relancer **après toute modification** de
`capacitor.config.ts` ou de `package.json`.

### Build Android (release)

Dans Android Studio : `Build > Generate Signed App Bundle`.

1. Créer un keystore (`.jks`) — **à conserver précieusement et hors du repo** :
   le perdre rend impossible toute mise à jour de l'app publiée.
2. Choisir `release`, produire un `.aab`.
3. Uploader sur la Google Play Console.

En ligne de commande : `cd android && ./gradlew bundleRelease` (après avoir
configuré le signing dans `android/keystore.properties`, **gitignoré**).

### Build iOS (release) — macOS obligatoire

Dans Xcode :

1. `App > Signing & Capabilities` : sélectionner l'équipe Apple Developer,
   laisser le signing automatique.
2. Ajouter la capability **Background Modes → Audio, AirPlay, and Picture in Picture**
   (équivalent UI de `UIBackgroundModes` du snippet).
3. Incrémenter `Version` / `Build`.
4. `Product > Destination > Any iOS Device` puis `Product > Archive`.
5. `Distribute App > App Store Connect > Upload`.

---

## Lecture audio en arrière-plan

Point le plus délicat d'un wrapper WebView. Traité dans deux fichiers dédiés,
avec les limites réelles explicitées :

- **iOS** → [`ios-Info.plist.snippet`](./ios-Info.plist.snippet)
  (`UIBackgroundModes: [audio]` **+** `AVAudioSession` en catégorie `playback` —
  la clé Info.plist seule ne suffit pas).
- **Android** → [`android-background-audio.md`](./android-background-audio.md)
  (foreground service `mediaPlayback` + permissions Android 14).

Amélioration transverse recommandée, **à faire côté site** (bénéficie aussi au
web mobile) : implémenter `navigator.mediaSession` dans le player pour obtenir
les contrôles sur l'écran verrouillé et les boutons du casque. Détails dans
`android-background-audio.md` § B.

---

## Ce qui reste strictement à David (rien de tout ça n'est automatisable ici)

### Comptes & argent

- [ ] **Apple Developer Program** — 99 $/an, https://developer.apple.com/programs/
      (inscription en tant qu'individu ou au nom d'EMSHEH HADEREH 770 LTD ; une
      inscription « Organization » exige un numéro **D-U-N-S**, à demander en
      amont, comptez 1 à 2 semaines).
- [ ] **Google Play Console** — 25 $ une fois, https://play.google.com/console
      (⚠️ un compte développeur *individuel* créé après nov. 2023 doit prouver
      **12 testeurs pendant 14 jours** avant de pouvoir publier en production ;
      un compte *organisation* y échappe — choisir en connaissance de cause).

### Machine

- [ ] Accès à un **Mac** (physique ou loué) pour builder/archiver iOS.
- [ ] Installer Android Studio pour builder l'`.aab`.
- [ ] Générer et **sauvegarder** le keystore Android.

### Assets & contenu

- [ ] Fournir `icon.png`, `icon-foreground.png`, `icon-background.png`,
      `splash.png` dans `mobile/assets/` (cf. `assets/README.md`).
- [ ] Captures d'écran des stores : iPhone 6.7" et 6.5" (App Store),
      téléphone + 7" + 10" (Play Store).
- [ ] Textes des fiches : nom, sous-titre, description, mots-clés, catégorie
      (« Religion & spiritualité »), classification d'âge.

### Juridique / conformité

- [ ] **Politique de confidentialité** publiée à une URL publique — **obligatoire
      sur les deux stores**, même si l'app ne collecte rien.
      Le site a déjà `cgv.html` ; il faut une page `confidentialite.html` dédiée.
- [ ] **App Privacy** (Apple) / **Data safety** (Google) : déclarer ce qui est
      collecté. Si le site utilise un analytics ou des cookies tiers, **il faut le
      déclarer** — une déclaration « rien collecté » démentie par le trafic réel
      est un motif de rejet.
- [ ] Se prémunir contre la **guideline 4.2 d'Apple (« Minimum Functionality »)** :
      Apple rejette régulièrement les apps qui ne sont qu'un site web reformaté.
      Il faut pouvoir démontrer une valeur native — au minimum : lecture audio en
      arrière-plan, téléchargement hors-ligne, notifications de nouvel épisode.
      **C'est le principal risque de ce projet ; l'anticiper dans les notes de
      revue.** Prévoir un texte expliquant ces fonctions natives à l'examinateur.

### Soumission

- [ ] Créer les fiches app dans App Store Connect et Play Console.
- [ ] Uploader les binaires, remplir les questionnaires, soumettre.
- [ ] Répondre aux éventuels rejets (délai typique : 24-48 h par cycle Apple).
