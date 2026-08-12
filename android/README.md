# The Torah Podcast — App Android TWA (Trusted Web Activity)

Emballe la PWA **https://thetorahpodcast.net** dans une app Android publiable sur
le Play Store, via **Bubblewrap**. Contrairement à un wrapper WebView, une TWA
tourne dans **Chrome lui-même** (plein écran, sans barre d'URL) → la lecture
audio en arrière-plan et l'API Media Session (déjà câblée dans `js/utils.js`)
fonctionnent comme dans Chrome, sans code natif.

> Approche Android retenue = **TWA** (ce dossier). Le dossier `../mobile/`
> (Capacitor) reste la voie **iOS** (Chrome/TWA n'existe pas sur iOS). Les deux
> partagent volontairement le même identifiant Android/App `net.thetorahpodcast.app`
> et la même fiche store (`../mobile/store-listing.md`). **Ne publier qu'UNE
> seule app Android** sous ce packageId — c'est permanent une fois sur le Play Store.

## Fichiers de ce dossier
- `twa-manifest.json` — config Bubblewrap de référence (host, packageId, couleurs,
  icônes, raccourcis). `bubblewrap init` peut la régénérer ; elle est ici pour
  garder la config versionnée et reproductible.

## Prérequis PWA (déjà en place)
- `manifest.json` : `name`, `short_name`, `start_url`, `scope`, `display:standalone`,
  `theme_color`, `background_color`, icônes 1024×1024 `any` (`/favicon.png`) +
  `maskable` (`/mobile/resources/icon.png`). ✔ conforme Bubblewrap.
- `sw.js` : service worker présent (précache + stratégie réseau). ✔
- Manque (non bloquant pour le build, utile pour la fiche Play) : **screenshots**
  d'app dans le manifest et captures d'écran téléphone pour la fiche.

## Digital Asset Links — POINT CRITIQUE
Le fichier `../.well-known/assetlinks.json` (racine du site, servi par GitHub Pages)
lie le domaine à l'app. **Sans le bon SHA256, la TWA affiche la barre d'URL Chrome
(échec de vérification).** Piège classique : avec **Play App Signing** (activé par
défaut), l'APK livré est **re-signé par la clé de Google**, pas par ton keystore
d'upload. Le SHA256 à mettre dans `assetlinks.json` est donc celui de la
**clé de signature d'app affichée dans Play Console → Test et publication →
Intégrité de l'app → Certificat de la clé de signature d'app** (mets AUSSI le
SHA256 de ta clé d'upload pour tester en local avant la mise en prod). Les deux
placeholders sont déjà dans `assetlinks.json`, à remplacer.

---

## RUNBOOK David (commandes mono-ligne, à lancer depuis `android/`)

1. Installer Bubblewrap :
   `npm i -g @bubblewrap/cli`

2. Initialiser le projet TWA depuis le manifest en ligne (répond aux prompts ;
   accepte le packageId `net.thetorahpodcast.app`) :
   `bubblewrap init --manifest https://thetorahpodcast.net/manifest.json`

   (Alternative reproductible sans prompts, en réutilisant la config versionnée :
   `bubblewrap init --manifest https://thetorahpodcast.net/manifest.json --directory . && cp twa-manifest.json ./twa-manifest.json`)

3. Générer le keystore d'upload UNE seule fois (garde le mot de passe + le fichier
   `android.keystore` en lieu sûr — sans lui, plus aucune mise à jour possible) :
   `keytool -genkeypair -v -keystore android.keystore -alias android -keyalg RSA -keysize 2048 -validity 9125 -storepass CHANGE_ME -keypass CHANGE_ME -dname "CN=The Torah Podcast, O=The Torah Podcast, C=IL"`

4. Construire l'App Bundle `.aab` :
   `bubblewrap build`

5. Récupérer le SHA256 de la **clé d'upload** (pour test local) :
   `keytool -list -v -keystore android.keystore -alias android -storepass CHANGE_ME | grep SHA256`

6. Créer l'app dans **Play Console** → uploader l'`app-release-bundle.aab`
   (Production ou, pour un nouveau compte perso, d'abord le **test fermé 12
   testeurs / 14 jours** exigé par Google), puis **activer Play App Signing**.

7. Dans Play Console → **Intégrité de l'app**, copier le **SHA256 de la clé de
   signature d'app de Google**. Le coller (+ celui de l'étape 5) dans
   `.well-known/assetlinks.json`, remplacer les 2 placeholders, committer :
   `git add .well-known/assetlinks.json && git commit -m "chore(android): real assetlinks SHA256" && git push`

8. Vérifier que le lien est bon (doit renvoyer le JSON avec les fingerprints) :
   `curl -s https://thetorahpodcast.net/.well-known/assetlinks.json`
   et via l'outil Google :
   `curl -s "https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://thetorahpodcast.net&relation=delegate_permission/common.handle_all_urls"`

9. Compléter la fiche Play (textes/mots-clés déjà prêts dans
   `../mobile/store-listing.md`), le formulaire **Data safety**, la classification
   de contenu, l'URL de confidentialité
   (`https://thetorahpodcast.net/politique-confidentialite.html`), uploader les
   captures d'écran, puis publier.

## Mises à jour ultérieures
Le contenu du site se met à jour tout seul (la TWA charge le site live). Une
nouvelle version de l'app n'est nécessaire que pour changer icône/nom/config :
incrémente `appVersionCode`/`appVersionName` dans `twa-manifest.json`, `bubblewrap build`,
ré-upload de l'`.aab` (signé avec le MÊME keystore d'upload).
