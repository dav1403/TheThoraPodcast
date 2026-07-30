import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Wrapper Capacitor autour du site existant https://thetorahpodcast.net
 *
 * Mode "server.url" : l'app charge directement le site en production.
 * Le contenu de `webDir` (www/) ne sert que de repli hors-ligne / d'écran
 * de secours si le WebView ne parvient pas a charger l'URL distante.
 */
const config: CapacitorConfig = {
  appId: 'net.thetorahpodcast.app',
  appName: 'The Torah Podcast',
  webDir: 'www',
  server: {
    url: 'https://thetorahpodcast.net',
    hostname: 'thetorahpodcast.net',
    androidScheme: 'https',
    iosScheme: 'https',
    // HTTPS uniquement : aucun contenu en clair autorise.
    cleartext: false,
    // Domaines ouverts DANS l'app ; tout le reste part dans le navigateur externe.
    allowNavigation: ['thetorahpodcast.net', '*.thetorahpodcast.net'],
  },
  ios: {
    contentInset: 'always',
    limitsNavigationsToAppBoundDomains: false,
    backgroundColor: '#1a1a2e',
  },
  android: {
    allowMixedContent: false,
    backgroundColor: '#1a1a2e',
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1500,
      launchAutoHide: true,
      backgroundColor: '#1a1a2e',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: false,
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#1a1a2e',
      overlaysWebView: false,
    },
  },
};

export default config;
