import { useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Ionicons } from "@expo/vector-icons";
import { colors, font, space } from "@/lib/theme";
import { ago, odds, pct } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useSession } from "@/lib/session";
import type { Ticket, TicketsResponse } from "@/lib/types";
import { Button, Card, Empty, ErrorState, Eyebrow, Loading, Muted } from "@/components/ui";
import { LegRow, StatusPill } from "@/components/LegRow";

const LIVE_REFRESH_MS = 60_000;
const SOURCES: Record<string, string> = { daily: "Daily odds", optimizer: "Optimizer", slip: "Bet slip",
  code_check: "Code check", chat: "Chat", match: "Match page" };

/** The account's booking codes, each graded leg by leg as the results come in. */
export default function Tickets() {
  const { enabled, loaded, signedIn, userId, signIn } = useSession();
  // Scores every minute while a pick is being played
  const q = useApi<TicketsResponse>(signedIn && userId ? `/api/user/tickets?uid=${encodeURIComponent(userId)}` : null,
    d => (d.tickets.some(t => t.legs.some(l => l.status === "pending" && l.live?.status === "live")) ? LIVE_REFRESH_MS : null));

  if (!loaded) return <Loading />;
  if (!signedIn) {
    return <Empty icon="ticket-outline" title="Your tickets" body="Sign in to see the booking codes you've made, graded as the results come in."
      action={enabled ? <Button title="Sign in" icon="log-in-outline" onPress={() => signIn()} /> : undefined} />;
  }
  if (q.error && !q.data) return <ErrorState error={q.error} what="Tickets" onRetry={q.reload} />;
  if (!q.data) return <Loading label="Loading your tickets…" />;

  const sum = q.data.summary;
  return (
    <FlatList
      data={q.data.tickets}
      keyExtractor={t => `${t.code}-${t.created_at}`}
      contentContainerStyle={{ padding: space.md, gap: space.md, flexGrow: 1 }}
      refreshControl={<RefreshControl refreshing={q.loading && !!q.data} onRefresh={() => q.reload()} tintColor={colors.accent} />}
      ListHeaderComponent={sum.tickets > 0 ? (
        <View style={styles.summary}>
          {[["Tickets", String(sum.tickets)], ["Won", String(sum.won)], ["Lost", String(sum.lost)],
            ["Hit rate", sum.hit_rate == null ? "—" : pct(sum.hit_rate)]].map(([k, v]) => (
            <View key={k} style={styles.stat}>
              <Eyebrow>{k}</Eyebrow>
              <Text style={[styles.statValue, font.mono]}>{v}</Text>
            </View>
          ))}
        </View>
      ) : null}
      renderItem={({ item }) => <TicketCard t={item} />}
      ListEmptyComponent={<Empty icon="ticket-outline" title="No tickets yet"
        body="Codes you book on BetIQ show up here. On Daily odds, tap “Track in my tickets” to add a slip." />}
    />
  );
}

function TicketCard({ t }: { t: Ticket }) {
  const [open, setOpen] = useState(t.status === "pending" || t.status === "open");
  const [copied, setCopied] = useState(false);
  const settled = t.legs.filter(l => l.status !== "pending").length;
  const copy = async () => {
    await Clipboard.setStringAsync(t.code).catch(() => false);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Card style={[t.status === "won" && { borderColor: colors.accent }, t.status === "lost" && { borderColor: colors.danger }]}>
      <Pressable onPress={() => setOpen(o => !o)} style={styles.head} accessibilityRole="button"
        accessibilityLabel={`Ticket ${t.code}, ${open ? "hide" : "show"} picks`}>
        <View style={{ flex: 1, gap: 2 }}>
          <Text selectable style={styles.code}>{t.code}</Text>
          <Muted style={{ fontSize: 12 }}>
            {SOURCES[t.source] ?? "Booking code"} · {ago(t.created_at)} · {t.legs.length} picks{t.legs.length ? ` · ${settled} settled` : ""}
          </Muted>
        </View>
        <View style={{ alignItems: "flex-end", gap: 4 }}>
          <Text style={[styles.odds, font.mono]}>{t.total_odds ? `${odds(t.total_odds)}x` : ""}</Text>
          <StatusPill status={t.status} />
        </View>
      </Pressable>
      <View style={styles.actions}>
        <Pressable onPress={copy} style={styles.action} accessibilityRole="button">
          <Ionicons name={copied ? "checkmark" : "copy-outline"} size={14} color={colors.accent} />
          <Text style={styles.actionText}>{copied ? "Copied" : "Copy code"}</Text>
        </Pressable>
        {t.legs.length ? (
          <Pressable onPress={() => setOpen(o => !o)} style={styles.action} accessibilityRole="button">
            <Ionicons name={open ? "chevron-up" : "chevron-down"} size={14} color={colors.muted} />
            <Text style={[styles.actionText, { color: colors.muted }]}>{open ? "Hide picks" : "Show picks"}</Text>
          </Pressable>
        ) : null}
      </View>
      {open && t.legs.map((l, i) => (
        <LegRow key={i} leg={{ ...l, pick: `${l.marketName ? `${l.marketName}: ` : ""}${l.label || l.code}` }} />
      ))}
    </Card>
  );
}

const styles = StyleSheet.create({
  summary: { flexDirection: "row", gap: space.sm },
  stat: { flex: 1, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: space.md, gap: 4 },
  statValue: { color: colors.text, fontSize: 20, fontWeight: "800" },
  head: { flexDirection: "row", gap: space.md, alignItems: "flex-start" },
  code: { color: colors.text, fontSize: 20, fontWeight: "800", letterSpacing: 2, fontFamily: "monospace" },
  odds: { color: colors.text, fontSize: 16, fontWeight: "800" },
  actions: { flexDirection: "row", gap: space.lg, marginTop: space.md, marginBottom: space.xs },
  action: { flexDirection: "row", alignItems: "center", gap: 4 },
  actionText: { color: colors.accent, fontSize: 13, fontWeight: "700" },
});
