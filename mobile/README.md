# BetIQ mobile app

The BetIQ app for Android and iOS, built with Expo (SDK 57) and Expo Router. It uses the
same backend as the website and the same Clerk sign-in, so a user's plan, trial and tickets are
the same in both.

Screens:

- **Predictions**: the day's matches by competition, with a strip of days. Live scores refresh every minute while a match is on.
- **Match**: chances, markets, live stats, team averages over the last 10 matches, and recent form.
- **Daily odds**: the server's 10x / 15x / 20x / 50x / 100x slips, each with its SportyBet
  code. You can copy the code, open it on SportyBet, or track it in your tickets.
- **My tickets**: the account's booking codes, graded leg by leg.
- **Account**: sign in or out, and your plan, trial and expiry.

Plans are bought on the website. App-store rules don't allow outside payments inside the app, so
**See plans** opens the site. A plan bought there works in the app as soon as you sign in.

## Set up

```bash
cd mobile
npm install
cp .env.example .env.local   # fill in the Clerk publishable key (the website's NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY)
```

In the Clerk dashboard, go to **Native applications** and turn on native sign-in. The app signs in
through Clerk's hosted page and comes back through the `betiq://` scheme.

## Run it

The app uses native modules (Clerk, secure storage), so it needs a **development build**. Expo
Go won't work.

```bash
npx eas-cli@latest login
npx eas-cli@latest build --profile development --platform android   # install the APK on your phone
npx expo start                                                       # then open it in that build
```

Without a Clerk key the app still runs: predictions are public, and sign-in shows as not set up.
`npx expo start --web` gives a quick preview in the browser, always signed out.

## Build for release

| Profile | What it makes |
|---|---|
| `development` | An APK with the dev client, for working on the app |
| `preview` | An installable APK to share for testing |
| `production` | A store build (AAB for Google Play, IPA for the App Store) |

```bash
npx eas-cli@latest build --profile preview --platform android
npx eas-cli@latest build --profile production --platform all
npx eas-cli@latest submit --platform android
```

`EXPO_PUBLIC_*` values are built into the app. For EAS builds, set them in **EAS → Environment
variables** (or in `.env.local` before building locally).

## Checks

```bash
npm run typecheck
npm run lint
```
