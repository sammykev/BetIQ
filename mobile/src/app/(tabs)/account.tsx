import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import Constants from "expo-constants";
import { Ionicons } from "@expo/vector-icons";
import { colors, space } from "@/lib/theme";
import { useApi } from "@/lib/useApi";
import { useSession, type Tier } from "@/lib/session";
import { Button, Card, Eyebrow, Loading, Muted, openSite, Pill } from "@/components/ui";

const TIER_NAMES: Record<Tier, string> = { free: "Free", lite: "Lite", premium: "Premium" };
interface TrialConfig { enabled: boolean; days: number; tier: "lite" | "premium" }

const daysLeft = (iso: string) => Math.max(1, Math.ceil((Date.parse(iso) - Date.now()) / 86_400_000));

/** Who's signed in, their plan, and sign in / out. Plans are bought on the website. */
export default function Account() {
  const s = useSession();
  const trial = useApi<TrialConfig>("/api/trial");
  const [busy, setBusy] = useState(false);
  const run = (f: () => Promise<void>) => async () => {
    setBusy(true);
    try { await f(); } catch { /* cancelled */ } finally { setBusy(false); }
  };

  if (!s.loaded) return <Loading />;
  const t = trial.data;
  return (
    <ScrollView contentContainerStyle={{ padding: space.md, gap: space.md }}>
      {s.signedIn ? (
        <Card style={{ gap: space.md }}>
          <View style={styles.who}>
            <View style={styles.avatar}><Ionicons name="person" size={22} color={colors.accentInk} /></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.name} numberOfLines={1}>{s.name || "Your account"}</Text>
              {s.email ? <Muted numberOfLines={1}>{s.email}</Muted> : null}
            </View>
          </View>
          <View style={styles.plan}>
            <View style={{ gap: 4 }}>
              <Eyebrow>Plan</Eyebrow>
              <View style={{ flexDirection: "row", gap: space.sm, alignItems: "center" }}>
                <Text style={styles.name}>{TIER_NAMES[s.tier]}</Text>
                {s.trial ? <Pill tone="accent">Free trial</Pill> : null}
              </View>
            </View>
            {s.expires ? (
              <Muted style={{ textAlign: "right" }}>
                {s.trial ? `${daysLeft(s.expires)} day${daysLeft(s.expires) > 1 ? "s" : ""} left` :
                  `Until ${new Date(s.expires).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}`}
              </Muted>
            ) : null}
          </View>
          <Button title={s.tier === "free" || s.trial ? "See plans" : "Manage plan"} icon="open-outline"
            onPress={() => openSite("/")} />
          <Muted style={{ fontSize: 12 }}>Plans are bought on the BetIQ website. Your plan works here as soon as you sign in.</Muted>
          <Button title="Sign out" kind="secondary" icon="log-out-outline" busy={busy} onPress={run(s.signOut)} />
        </Card>
      ) : (
        <Card style={{ gap: space.md }}>
          <Text style={styles.name}>Sign in to BetIQ</Text>
          <Muted>
            Your plan, the daily odds slips, and every booking code you make, graded as the results come in.
            {t?.enabled ? ` New accounts get ${t.days} days of ${TIER_NAMES[t.tier]} free.` : ""}
          </Muted>
          {s.enabled ? (
            <>
              <Button title="Sign in" icon="log-in-outline" busy={busy} onPress={run(() => s.signIn("sign-in"))} />
              <Button title="Create an account" kind="secondary" icon="person-add-outline" onPress={run(() => s.signIn("sign-up"))} />
            </>
          ) : (
            <Muted style={{ color: colors.warn }}>Sign-in isn’t set up in this build of the app.</Muted>
          )}
        </Card>
      )}

      <Card style={{ paddingVertical: space.xs }}>
        {[
          { icon: "globe-outline" as const, label: "BetIQ website", path: "/" },
          { icon: "stats-chart-outline" as const, label: "Track record", path: "/history" },
        ].map((l, i) => (
          <Pressable key={l.label} onPress={() => openSite(l.path)} accessibilityRole="link"
            style={({ pressed }) => [styles.link, i > 0 && styles.linkBorder, pressed && { opacity: 0.7 }]}>
            <Ionicons name={l.icon} size={18} color={colors.text2} />
            <Text style={styles.linkText}>{l.label}</Text>
            <Ionicons name="open-outline" size={14} color={colors.faint} />
          </Pressable>
        ))}
      </Card>

      <Muted style={{ textAlign: "center", fontSize: 11, marginTop: space.md }}>
        Predictions are probabilities, not guarantees. 18+ only · Bet responsibly.{"\n"}
        BetIQ {Constants.expoConfig?.version ?? ""}
      </Muted>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  who: { flexDirection: "row", alignItems: "center", gap: space.md },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  name: { color: colors.text, fontSize: 18, fontWeight: "800" },
  plan: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", paddingTop: space.md,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  link: { flexDirection: "row", alignItems: "center", gap: space.md, paddingVertical: space.md },
  linkBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  linkText: { flex: 1, color: colors.text, fontSize: 15 },
});
