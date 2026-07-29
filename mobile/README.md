# The Torah Podcast — application mobile (iOS / Android)

Wrapper **Capacitor** autour du site existant <https://thetorahpodcast.net>.
L'application est une coque native qui affiche le site en plein ecran, sans barre
d'adresse de navigateur, avec icone, splash screen et presence sur l'App Store /
Google Play. **Aucun code du site n'est duplique** : toute mise a jour du site est
immediatement visible dans l'app, sans nouvelle soumission aux stores.

- `appId` : `net.thetorahpodcast.app`
- `appName` : `The Torah Podcast`
- URL chargee : `https://thetorahpodcast.net` (cf. `server.url` dans `capacitor.config.ts`)
- `www/` : page de repli affichee uniquement si le site est injoignable (hors-ligne)

---

## 1. Prerequis

| Cible | Necessaire |
|---|---|
| Les deux | Node.js >= 20, npm |
| **iOS** | **macOS obligatoire** + Xcode 15+ + CocoaPods (`sudo gem install cocoapods`) + un compte Apple Developer |
| **Android** | Android Studio (Hedgehog+) + JDK 17 + SDK Android 34+ |

> iOS ne peut pas etre construit depuis Windows. Il faut un Mac (ou un service de
> build cloud type Ionic Appflow / Codemagic / EAS-like).

## 2. Installation

```bash
cd mobile
npm install
```

## 3. Ajouter les plateformes natives

Les dossiers `ios/` et `android/` sont **generes** et volontairement gitignores
(ils contiennent des chemins machine et des artefacts de build).

```bash
npx cap add ios       # sur macOS uniquement
npx cap add android
npx cap sync          # a relancer apres chaque changement de config ou de plugin
```

## 4. Icone et splash screen

Les sources sont dans `resources/` (`icon.png` 1024x1024, `splash.png` et
`splash-dark.png` 2732x2732) — **placeholders derives du favicon du site**, a
remplacer par un vrai visuel avant soumission.

Generation de toutes les tailles :

```bash
npx capacitor-assets generate \
  --iconBackgroundColor '#0b0b0f' --iconBackgroundColorDark '#0b0b0f' \
  --splashBackgroundColor '#0b0b0f' --splashBackgroundColorDark '#0b0b0f'
# ou simplement : npm run assets
```

Contraintes stores : icone **1024x1024 PNG, opaque, sans coins arrondis, sans
transparence** (Apple rejette une icone avec canal alpha).

## 5. Lecture audio en arriere-plan

C'est le point le plus important pour un podcast : quand l'utilisateur verrouille
son telephone ou change d'app, le cours doit continuer.
`npx cap add` ne configure PAS cela — a appliquer **apres** la generation des
plateformes. Les extraits prets a copier sont dans `native-config/`.

### iOS — `ios/App/App/Info.plist`

Ajouter, dans le `<dict>` racine :

```xml
<key>UIBackgroundModes</key>
<array>
  <string>audio</string>
</array>
```

Puis, dans `ios/App/App/AppDelegate.swift`, activer la session audio (sinon le son
se coupe malgre la cle) — voir `native-config/AppDelegate.audio.swift.txt` :

```swift
import AVFoundation
// dans application(_:didFinishLaunchingWithOptions:)
try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
try? AVAudioSession.sharedInstance().setActive(true)
```

`.spokenAudio` est la categorie adaptee a de la parole (podcast/cours) : elle
active le comportement "pause" attendu par les autres apps audio.

### Android — `android/app/src/main/AndroidManifest.xml`

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.WAKE_LOCK" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />
```

et sur la `<application>` : `android:usesCleartextTraffic="false"`.

> ⚠️ **Limite reelle a connaitre** : sur Android, le WebView **suspend la lecture
> media quand l'app passe en arriere-plan**. Les permissions ci-dessus sont
> necessaires mais **pas suffisantes**. Deux options :
> 1. (retenue par defaut) accepter que la lecture s'arrete quand l'ecran est
>    eteint sur Android, comme dans Chrome sans PWA ;
> 2. (v2) brancher un vrai lecteur natif — plugin type `@capacitor-community/media-session`
>    + un `MediaSessionService` en foreground service, avec le site qui expose la
>    liste des episodes via `feeds/`/`episodes.json`. C'est un chantier separe,
>    a chiffrer.
>
> Sur iOS, `UIBackgroundModes: audio` + `AVAudioSession.playback` suffisent pour
> que la lecture continue ecran verrouille.

Bonus cote **site** (pas cote app) : exposer `navigator.mediaSession.metadata`
(titre, auteur, artwork) donne les controles lecture sur l'ecran verrouille et
dans le centre de controle — a faire dans le lecteur de `episode.html`.

## 6. Ouvrir / builder

```bash
npx cap open ios       # ouvre Xcode → Product > Archive → Distribute App
npx cap open android   # ouvre Android Studio → Build > Generate Signed Bundle (.aab)
```

Android : generer **un keystore une seule fois** et le conserver precieusement
(sans lui, impossible de publier une mise a jour). Il ne doit **jamais** etre
commite (deja couvert par le `.gitignore`).

## 7. Checklist de publication sur les stores

### Comptes et couts
- [ ] **Apple Developer Program** — 99 $/an, validation 24-48 h (compte individuel
      ou societe ; pour une societe il faut un numero DUNS).
- [ ] **Google Play Console** — 25 $ une seule fois.
- [ ] Google exige, pour un **nouveau compte developpeur personnel**, une phase de
      **test ferme avec 12 testeurs pendant 14 jours** avant publication publique.
      A anticiper : c'est 2 semaines de delai incompressibles.

### Elements a preparer
- [ ] Icone 1024x1024 (opaque, sans alpha)
- [ ] Captures d'ecran : iPhone 6.7" et 6.5" ; Android telephone (+ 7" et 10" si tablette declaree)
- [ ] Nom (30 car.), sous-titre, description, mots-cles
- [ ] **Politique de confidentialite** en ligne et publiquement accessible — page
      dediee sur thetorahpodcast.net (URL obligatoire des deux cotes)
- [ ] Formulaire **App Privacy** (Apple) / **Data safety** (Google) : declarer ce
      qui est reellement collecte (analytics, cookies du site inclus)
- [ ] Classification du contenu / age rating
- [ ] Compte de test si une partie du contenu est protegee
- [ ] URL de support + email de contact

### Risque a connaitre — Apple guideline 4.2 « Minimum Functionality »
Apple rejette regulierement les apps qui ne sont « qu'un site web repackage ».
Pour reduire le risque :
- [ ] splash screen et icone soignes (pas de placeholder)
- [ ] navigation qui ne ressemble pas a un navigateur (pas de barre d'URL, gestes natifs)
- [ ] au moins une capacite native reelle : **lecture audio en arriere-plan**
      (point 5), et idealement notifications ou telechargement hors-ligne
- [ ] aucun lien qui sort vers Safari pour la fonction principale
- [ ] aucun lien de don / paiement externe dans l'app (Apple impose l'achat
      in-app pour du contenu numerique ; le don caritatif est tolere mais doit
      passer par un mecanisme conforme — verifier avant soumission)

Google Play est nettement plus permissif sur le wrapper.

### Soumission
- [ ] iOS : Xcode > Archive > Distribute > App Store Connect, puis remplir la fiche
      et soumettre pour revue (24-72 h en general)
- [ ] Android : Play Console > Production > nouveau release, upload du `.aab`
- [ ] Prevoir 1 a 2 allers-retours de revue

---

## 8. Ce qui reste volontairement hors perimetre

- Notifications push (necessiterait Firebase + un backend d'envoi)
- Telechargement / ecoute hors-ligne des episodes
- Lecteur audio 100 % natif (cf. limite Android au point 5)
