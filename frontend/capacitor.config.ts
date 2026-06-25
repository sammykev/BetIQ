import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.betiq.app',
  appName: 'BetIQ',
  webDir: 'out',   // only used for local static builds; ignored when server.url is set

  // ── Point to your live Vercel deployment ──────────────────────────────
  // This makes the app load the real web app inside the native shell.
  // Remove this block and run `npm run build:mobile` for a fully offline build.
  server: {
    url: 'https://predict-withbetiq.vercel.app',
    cleartext: false,
  },

  ios: {
    contentInset: 'always',
    backgroundColor: '#0f172a',
    scrollEnabled: true,
    allowsLinkPreview: false,
  },
  android: {
    backgroundColor: '#0f172a',
    allowMixedContent: false,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#0f172a',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },
    StatusBar: {
      style: 'dark',
      backgroundColor: '#0f172a',
    },
  },
};

export default config;
