# Extraits de configuration native

Les dossiers `ios/` et `android/` sont generes par `npx cap add` et gitignores.
Les fichiers de ce dossier sont les **modifications a reappliquer manuellement**
apres chaque `npx cap add` (donc sur une machine neuve, ou apres suppression des
plateformes).

| Fichier ici | A reporter dans |
|---|---|
| `Info.plist.snippet.xml` | `ios/App/App/Info.plist` |
| `AppDelegate.audio.swift.txt` | `ios/App/App/AppDelegate.swift` |
| `AndroidManifest.snippet.xml` | `android/app/src/main/AndroidManifest.xml` |

Aucun de ces extraits ne contient de secret.
