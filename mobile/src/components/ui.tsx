import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type TextStyle, type ViewStyle } from "react-native";
import * as WebBrowser from "expo-web-browser";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, space } from "@/lib/theme";
import { SITE_URL } from "@/lib/config";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Eyebrow({ children, style }: { children: ReactNode; style?: StyleProp<TextStyle> }) {
  return <Text style={[styles.eyebrow, style]}>{children}</Text>;
}

export function Title({ children }: { children: ReactNode }) {
  return <Text style={styles.title}>{children}</Text>;
}

export function Muted({ children, style, numberOfLines }: { children: ReactNode; style?: StyleProp<TextStyle>; numberOfLines?: number }) {
  return <Text style={[styles.muted, style]} numberOfLines={numberOfLines}>{children}</Text>;
}

type Tone = "accent" | "danger" | "warn" | "muted" | "live";
const TONES: Record<Tone, { bg: string; fg: string }> = {
  accent: { bg: "rgba(184,245,58,0.14)", fg: colors.accent },
  danger: { bg: "rgba(251,113,133,0.14)", fg: colors.danger },
  warn: { bg: "rgba(252,211,77,0.14)", fg: colors.warn },
  muted: { bg: colors.sunken, fg: colors.muted },
  live: { bg: "rgba(251,113,133,0.18)", fg: colors.danger },
};

export function Pill({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  return (
    <View style={[styles.pill, { backgroundColor: TONES[tone].bg }]}>
      <Text style={[styles.pillText, { color: TONES[tone].fg }]}>{children}</Text>
    </View>
  );
}

export function Button({ title, onPress, kind = "primary", icon, disabled, busy }: {
  title: string; onPress: () => void; kind?: "primary" | "secondary"; icon?: keyof typeof Ionicons.glyphMap;
  disabled?: boolean; busy?: boolean;
}) {
  const primary = kind === "primary";
  return (
    <Pressable onPress={onPress} disabled={disabled || busy} accessibilityRole="button"
      style={({ pressed }) => [styles.button, primary ? styles.buttonPrimary : styles.buttonSecondary,
        (pressed || disabled) && { opacity: 0.7 }]}>
      {busy ? <ActivityIndicator size="small" color={primary ? colors.accentInk : colors.text} /> :
        icon ? <Ionicons name={icon} size={16} color={primary ? colors.accentInk : colors.text} /> : null}
      <Text style={[styles.buttonText, { color: primary ? colors.accentInk : colors.text }]}>{title}</Text>
    </Pressable>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.accent} />
      <Muted style={{ marginTop: space.sm }}>{label}</Muted>
    </View>
  );
}

export function Empty({ title, body, icon = "football-outline", action }: {
  title: string; body?: string; icon?: keyof typeof Ionicons.glyphMap; action?: ReactNode;
}) {
  return (
    <View style={styles.center}>
      <Ionicons name={icon} size={32} color={colors.faint} />
      <Text style={[styles.title, { textAlign: "center", marginTop: space.sm }]}>{title}</Text>
      {body ? <Muted style={{ textAlign: "center", marginTop: space.xs }}>{body}</Muted> : null}
      {action ? <View style={{ marginTop: space.lg }}>{action}</View> : null}
    </View>
  );
}

export const openSite = (path = "") => WebBrowser.openBrowserAsync(`${SITE_URL}${path}`);

/** What to show when the backend refused: sign in, a plan, or switched off; else the error with a retry. */
export function ErrorState({ error, onRetry, what }: { error: Error; onRetry?: () => void; what: string }) {
  const { enabled, signIn } = useSession();
  const gate = error instanceof ApiError ? error.gate : null;
  if (gate === "sign_in") {
    return <Empty icon="person-circle-outline" title="Sign in to see this" body={`${what} are for BetIQ accounts.`}
      action={enabled ? <Button title="Sign in" icon="log-in-outline" onPress={() => signIn()} /> : undefined} />;
  }
  if (gate === "lite" || gate === "premium") {
    return <Empty icon="lock-closed-outline" title={`Part of BetIQ ${gate === "lite" ? "Lite" : "Premium"}`}
      body={`${what} come with ${gate === "lite" ? "Lite and Premium" : "Premium"}. See the plans on the website.`}
      action={<Button title="See plans" icon="open-outline" onPress={() => openSite("/")} />} />;
  }
  if (gate === "off") return <Empty icon="eye-off-outline" title="Not available" body="This isn't available right now. Check back soon." />;
  return <Empty icon="cloud-offline-outline" title="Couldn't load" body="Check your connection and try again."
    action={onRetry ? <Button title="Try again" kind="secondary" icon="refresh" onPress={onRetry} /> : undefined} />;
}

export const styles = StyleSheet.create({
  card: { backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: space.lg },
  eyebrow: { color: colors.muted, fontSize: 11, fontWeight: "700", letterSpacing: 1.2, textTransform: "uppercase" },
  title: { color: colors.text, fontSize: 17, fontWeight: "700" },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  pill: { borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 3, alignSelf: "flex-start" },
  pillText: { fontSize: 11, fontWeight: "700" },
  button: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: radius.md,
    paddingHorizontal: space.lg, paddingVertical: 11 },
  buttonPrimary: { backgroundColor: colors.accent },
  buttonSecondary: { backgroundColor: colors.raised, borderWidth: 1, borderColor: colors.border },
  buttonText: { fontSize: 15, fontWeight: "700" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl, minHeight: 280 },
});
