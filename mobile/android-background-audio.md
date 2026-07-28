# Android — lecture audio en arrière-plan

> À appliquer **après** `npx cap add android` (le dossier `mobile/android/` est
> généré par Capacitor, il n'est pas versionné ici).

## Le problème, honnêtement

Un `<audio>` HTML5 joué dans la WebView Android **continue** de jouer quand
l'utilisateur quitte l'app… jusqu'à ce qu'Android décide de tuer le process.
Sans **foreground service**, le système considère l'app comme « en cache » et
peut la stopper à tout moment (typiquement au bout de quelques minutes, ou dès
que la mémoire est sollicitée). Sur les surcouches agressives (Xiaomi, Huawei,
Samsung « optimisation batterie »), la coupure est quasi immédiate.

Il n'existe **pas** de plugin Capacitor officiel qui règle ça. Les trois voies
réellement disponibles :

| Voie | Effort | Fiabilité |
|---|---|---|
| A. Ne rien faire | 0 | ⚠️ lecture coupée aléatoirement en arrière-plan |
| B. `navigator.mediaSession` côté site (JS) | faible | Contrôles notification + casque OK, mais **ne protège pas** de la mise à mort du process |
| C. Foreground service natif (ci-dessous) | moyen (~1 h de Java) | ✅ la seule solution robuste |

**Recommandation : B + C.** B côté site (bénéfice aussi sur le web mobile),
C côté app pour garantir la survie du process.

---

## A. Prérequis manifeste

Dans `mobile/android/app/src/main/AndroidManifest.xml`, dans `<manifest>` :

```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
<uses-permission android:name="android.permission.WAKE_LOCK" />
```

`FOREGROUND_SERVICE_MEDIA_PLAYBACK` est **obligatoire** depuis Android 14
(API 34) ; sans elle le service lève `SecurityException` au démarrage.

Puis, dans `<application>` :

```xml
<service
    android:name=".PlaybackKeepAliveService"
    android:exported="false"
    android:foregroundServiceType="mediaPlayback" />
```

---

## B. `navigator.mediaSession` (côté site, pas côté app)

À implémenter dans le player du site (`js/`), pas ici. Rappel de l'API :

```js
if ('mediaSession' in navigator) {
  navigator.mediaSession.metadata = new MediaMetadata({
    title: episodeTitle,
    artist: speakerName,
    album: 'The Torah Podcast',
    artwork: [{ src: artworkUrl, sizes: '512x512', type: 'image/png' }],
  });
  navigator.mediaSession.setActionHandler('play',  () => audio.play());
  navigator.mediaSession.setActionHandler('pause', () => audio.pause());
  navigator.mediaSession.setActionHandler('seekbackward',  () => { audio.currentTime -= 15; });
  navigator.mediaSession.setActionHandler('seekforward',   () => { audio.currentTime += 30; });
}
```

Gain immédiat : notification média système, boutons du casque, écran verrouillé —
sur Android **et** iOS, app **et** navigateur.

---

## C. Foreground service « keep-alive »

Le service ne lit pas l'audio lui-même (c'est la WebView qui lit) : il maintient
le process en vie et affiche la notification obligatoire.

`mobile/android/app/src/main/java/net/thetorahpodcast/app/PlaybackKeepAliveService.java` :

```java
package net.thetorahpodcast.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import androidx.core.app.NotificationCompat;

public class PlaybackKeepAliveService extends Service {

    private static final String CHANNEL_ID = "ttp_playback";
    private static final int NOTIFICATION_ID = 1;

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "Lecture en cours", NotificationManager.IMPORTANCE_LOW);
            channel.setShowBadge(false);
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(
            this, 0, open, PendingIntent.FLAG_IMMUTABLE);

        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("The Torah Podcast")
            .setContentText("Lecture en cours")
            .setSmallIcon(R.drawable.ic_stat_icon_config_sample)
            .setContentIntent(pi)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build();

        startForeground(NOTIFICATION_ID, notification);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
```

> `R.drawable.ic_stat_icon_config_sample` est l'icône de notification générée par
> défaut par Capacitor. Remplacer par une icône monochrome dédiée si besoin.

### Démarrer / arrêter le service depuis le web

Le service doit démarrer au `play` et s'arrêter au `pause`/`ended`. Comme
l'app charge le site **distant**, il faut un petit plugin Capacitor local qui
expose deux méthodes au JS.

`mobile/android/app/src/main/java/net/thetorahpodcast/app/PlaybackPlugin.java` :

```java
package net.thetorahpodcast.app;

import android.content.Intent;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "Playback")
public class PlaybackPlugin extends Plugin {

    @PluginMethod
    public void start(PluginCall call) {
        Intent i = new Intent(getContext(), PlaybackKeepAliveService.class);
        getContext().startForegroundService(i);
        call.resolve();
    }

    @PluginMethod
    public void stop(PluginCall call) {
        getContext().stopService(new Intent(getContext(), PlaybackKeepAliveService.class));
        call.resolve();
    }
}
```

Enregistrement dans `MainActivity.java` :

```java
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PlaybackPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
```

Appel côté site (no-op hors app, donc sans risque pour le web) :

```js
const Playback = window.Capacitor?.Plugins?.Playback;
audio.addEventListener('play',  () => Playback?.start());
audio.addEventListener('pause', () => Playback?.stop());
audio.addEventListener('ended', () => Playback?.stop());
```

---

## D. Vérification

Aucun de ces points ne peut être testé depuis Windows/cloud. Sur un appareil réel :

1. Lancer un cours, mettre l'app en arrière-plan → le son continue et une
   notification « Lecture en cours » apparaît.
2. Verrouiller l'écran, attendre **10 minutes** → toujours en lecture.
3. Ouvrir 3-4 grosses apps pour saturer la mémoire → toujours en lecture.
4. Sur un Xiaomi/Samsung : vérifier que l'app n'est pas soumise à
   « optimisation de la batterie » (paramètres système), sinon le documenter
   dans une FAQ.
