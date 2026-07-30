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

Les sources sont dans `resources/` et sont **derivees de l'identite visuelle du
site** (logo navy `#1a1a2e` + or : rouleau de Torah + micro + ondes, cf.
`../favicon.png`) :

- `icon.png` 1024x1024 — **embleme seul** (le mot-logo « THE TORAH PODCAST » est
  retire car illisible en petite taille), sur fond blanc, **opaque, sans canal
  alpha** (exigence Apple).
- `splash.png` / `splash-dark.png` 2732x2732 — embleme sur une carte blanche
  arrondie, centree sur le navy `#1a1a2e` de la marque.

Ces fichiers sont **regenerables** a partir du logo du site :

```bash
py scripts/build_source_assets.py   # depuis le dossier mobile/ (ou la racine du repo)
```

> Ce ne sont pas des placeholders geometriques : ce sont de vrais visuels
> derives de la charte. David peut deposer un visuel definitif sur mesure dans
> `resources/icon.png` / `resources/splash.png` puis relancer `npm run assets`.

Generation de toutes les tailles natives (a lancer apres `npx cap add`) :

```bash
npx capacitor-assets generate \
  --iconBackgroundColor '#1a1a2e' --iconBackgroundColorDark '#1a1a2e' \
  --splashBackgroundColor '#1a1a2e' --splashBackgroundColorDark '#1a1a2e'
# ou simplement : npm run assets
```

Contraintes stores : icone **1024x1024 PNG, opaque, sans coins arrondis, sans
transparence** (Apple rejette une icone avec canal alpha) — respecte par
`icon.png` ci-dessus.

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

Cote **site** : `navigator.mediaSession` est **desormais implemente** (voir
`js/utils.js`, fonction `setupMediaSession`). Il branche le lecteur partage
`#player-audio` sur l'API Media Session de l'OS : titre + auteur + artwork sur
l'ecran verrouille et dans le centre de controle, avec boutons play/pause/seek
fonctionnels. C'est la **moitie cote-site** de la lecture en arriere-plan :
- sur **iOS**, combine a `UIBackgroundModes:audio` + `AVAudioSession.playback`,
  cela suffit pour une lecture continue et pilotable ecran verrouille ;
- sur **Android**, cela affiche la notification media mais ne suffit pas a
  empecher le WebView de suspendre le son en arriere-plan profond — il faut le
  service natif ci-dessus (option 2), a brancher au build.

> ⚠️ **A VERIFIER par David au 1er build device** : la continuite ecran-eteint
> ne peut pas etre prouvee sans un vrai build iOS (Mac) / Android (Studio). Le
> cote site (Media Session) et la config native (Info.plist / AndroidManifest /
> AppDelegate) sont en place ; le comportement reel se valide sur appareil.

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
- [x] **Politique de confidentialite** en ligne et publiquement accessible — page
      dediee **<https://thetorahpodcast.net/politique-confidentialite.html>**
      (alias : `/privacy.html`). FR/EN/HE, mise en ligne via ce depot. C'est
      l'URL a coller des deux cotes (App Store Connect + Play Console).
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
