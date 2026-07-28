import type { CapacitorConfig } from '@capacitor/cli';

/**
 * The Torah Podcast — native shell.
 *
 * Strategy: the app is a thin native wrapper that loads the live site
 * (https://thetorahpodcast.net) inside the system WebView. Content updates
 * ship instantly without a store review; only shell changes (icons, plugins,
 * permissions) require a new build.
 *
 * `webDir: 'www'` is still mandatory for the Capacitor CLI. Its content is not
 * displayed while the remote site is reachable — it only backs `server.errorPath`
 * (shown when the device is offline or the site is unreachable).
 */
const config: CapacitorConfig = {
  appId: 'net.thetorahpodcast.app',
  appName: 'The Torah Podcast',
  webDir: 'www',
  backgroundColor: '#1a1a2e',
  server: {
    url: 'https://thetorahpodcast.net',
    // HTTPS only — no plaintext traffic allowed from the app.
    cleartext: false,
    androidScheme: 'https',
    // Anything outside this list opens in the system browser instead of
    // taking over the app WebView (required by both stores for external links).
    allowNavigation: [
      'thetorahpodcast.net',
      '*.thetorahpodcast.net',
      '*.r2.dev',
      '*.cloudflarestorage.com',
    ],
    // Offline / unreachable fallback page bundled in `www/`.
    errorPath: 'error.html',
  },
  ios: {
    // Let the web page own its safe-area insets (the site already handles them).
    contentInset: 'never',
    backgroundColor: '#1a1a2e',
    limitsNavigationsToAppBoundDomains: false,
    scrollEnabled: true,
  },
  android: {
    allowMixedContent: false,
    backgroundColor: '#1a1a2e',
    captureInput: true,
    webContentsDebuggingEnabled: false,
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
