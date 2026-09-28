import { useState } from "react";
import { Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Ionicons } from "@expo/vector-icons";
import { colors, font, radius, space } from "@/lib/theme";
import { dayLabel, odds, pct, pctFine } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useSession } from "@/lib/session";
import type { DailyResponse, Slip } from "@/lib/types";
import { Button, Card, Empty, ErrorState, Eyebrow, Loading, Muted } from "@/components/ui";
import { LegRow, StatusPill } from "@/components/LegRow";

const LIVE_REFRESH_MS = 60_000;

function daysBefore(iso: string, n: number): string[] {
  const d = new Date(`${iso}T12:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(d.getTime() - i * 864e5).toISOString().slice(0, 10));
}

const notStarted = (p: Slip["picks"][number]) =>
  p.status === "pending" && !p.live && Date.parse(`${p.date}T${p.time || "23:59"}:00Z`) > Date.now();

/** The server's slips at about 10, 15, 20, 50 and 100 odds for a day, each with its SportyBet code. */
export default function Daily() {
  const [day, setDay] = useState<string | null>(null);
  // Scores every minute while picks are being played
  const q = useApi<DailyResponse>(`/api/daily-slips${day ? `?date=${day}` : ""}`,
    d => (d.slips.some(s => s.picks.some(p => p.status === "pending" && p.live?.status === "live")) ? LIVE_REFRESH_MS : null));
  const [picked, setPick] = useState(0);
  const data = q.data;

  if (q.error && !data) return <ErrorState error={q.error} what="Daily odds" onRetry={q.reload} />;
  if (!data) return <Loading label="Loading today's slips…" />;

  const isToday = data.date === data.today;
  const days = daysBefore(data.today, 7);
  const pick = Math.min(picked, Math.max(0, data.slips.length - 1));
  const s = data.slips[pick];
  const publishAt = new Date(`${data.today}T${data.publish_at_utc ?? "06:05"}:00Z`)
    .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <ScrollView contentContainerStyle={{ paddingBottom: space.xl }}
      refreshControl={<RefreshControl refreshing={q.loading && !!data} onRefresh={() => q.reload()} tintColor={colors.accent} />}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.days}>
        {days.map(d => {
          const active = d === data.date;
          return (
            <Pressable key={d} onPress={() => { setDay(d === data.today ? null : d); setPick(0); }}
              style={[styles.chip, active && styles.chipActive]}>
              <Text style={[styles.chipText, active && { color: colors.accentInk }]}>{dayLabel(d, data.today)}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {data.slips.length === 0 ? (
        <Empty icon="flame-outline" title={isToday ? "Coming this morning" : "No slips that day"}
          body={isToday ? `Today's slips and booking codes come out at ${publishAt} each morning.` : "No slips were made that day."} />
      ) : (
        <>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs}>
            {data.slips.map((x, i) => {
              const rec = data.record[String(x.target)];
              const active = pick === i;
              return (
                <Pressable key={x.target} onPress={() => setPick(i)} accessibilityRole="tab" accessibilityState={{ selected: active }}
                  style={[styles.tab, active && styles.tabActive]}>
                  <Eyebrow>{x.target}x</Eyebrow>
                  <Text style={[styles.tabOdds, font.mono]}>{x.status === "none" ? "—" : `${odds(x.total_odds)}x`}</Text>
                  <Muted style={{ fontSize: 11 }}>{x.status === "none" ? "Not today" : `${x.games} games · ${pctFine(x.win_chance)}`}</Muted>
                  {rec && rec.won + rec.lost > 0 ? <Muted style={{ fontSize: 10 }}>Record {rec.won}/{rec.won + rec.lost}</Muted> : null}
                </Pressable>
              );
            })}
          </ScrollView>
          {s && <SlipView key={`${data.date}-${s.target}`} s={s} isToday={isToday} date={data.date}
            retry={data.retry_minutes ?? 15} minProb={data.min_prob} />}
        </>
      )}
      <Muted style={styles.footer}>
        Picks our model rates {pct(data.min_prob)} or more, from that day’s matches. A slip needs every pick to land.
        18+ · Bet responsibly.
      </Muted>
    </ScrollView>
  );
}

function SlipView({ s, isToday, date, retry, minProb }: { s: Slip; isToday: boolean; date: string; retry: number; minProb: number }) {
  const { signedIn, getToken, userId } = useSession();
  const [copied, setCopied] = useState(false);
  const [tracking, setTracking] = useState<"idle" | "busy" | "done" | string>("idle");

  if (s.status === "none") {
    return (
      <Card style={styles.slip}>
        <Text style={styles.h}>No {s.target}x slip {isToday ? "today" : "that day"}</Text>
        <Muted style={{ marginTop: space.xs }}>{s.error ||
          `Not enough picks at ${pct(minProb)} or more in ${isToday ? "today's" : "that day's"} matches to reach ${s.target}x.`}</Muted>
      </Card>
    );
  }

  const b = s.booking;
  const toPlay = s.picks.filter(notStarted).length;
  const started = s.picks.filter(p => p.status === "pending" && !notStarted(p)).length;
  const settled = s.picks.filter(p => p.status !== "pending").length;

  const copy = async (code: string) => {
    await Clipboard.setStringAsync(code).catch(() => false);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const track = async () => {
    setTracking("busy");
    try {
      await api("/api/daily-slips/track", getToken, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, target: s.target, uid: userId }),
      });
      setTracking("done");
    } catch (e) {
      setTracking(e instanceof ApiError && e.detail ? e.detail : "Couldn't add it. Try again.");
    }
  };

  return (
    <Card style={[styles.slip, s.status === "won" && { borderColor: colors.accent }, s.status === "lost" && { borderColor: colors.danger }]}>
      <View style={styles.head}>
        <View>
          <Eyebrow>{s.target}x slip · {s.games} games · {settled}/{s.picks.length} settled</Eyebrow>
          <Text style={[styles.big, font.mono]}>{odds(s.total_odds)}<Text style={styles.bigX}>x</Text></Text>
        </View>
        <View style={{ alignItems: "flex-end", gap: 4 }}>
          <StatusPill status={s.status} />
          <Muted style={{ fontSize: 11 }}>Chance it lands</Muted>
          <Text style={[styles.h, font.mono]}>{pctFine(s.win_chance)}</Text>
        </View>
      </View>

      <View style={styles.code}>
        <Eyebrow>SportyBet booking code</Eyebrow>
        {b?.code ? (
          <>
            <Text selectable style={styles.codeText}>{b.code}</Text>
            <View style={styles.actions}>
              <View style={{ flex: 1 }}>
                <Button title={copied ? "Copied" : "Copy code"} icon={copied ? "checkmark" : "copy-outline"} onPress={() => copy(b.code!)} />
              </View>
              {b.share_url ? (
                <View style={{ flex: 1 }}>
                  <Button title="Open" kind="secondary" icon="open-outline" onPress={() => Linking.openURL(b.share_url!)} />
                </View>
              ) : null}
            </View>
            {b.booked < s.picks.length ? (
              <Text style={styles.warn}>The code holds {b.booked} of the {s.picks.length} picks: SportyBet wasn’t offering the others when we booked it.</Text>
            ) : null}
            {isToday && started > 0 && s.status === "pending" ? (
              <Muted style={{ fontSize: 12 }}>{started} match{started > 1 ? "es have" : " has"} kicked off: SportyBet leaves {started > 1 ? "those" : "it"} out if you load the code now.</Muted>
            ) : null}
            {signedIn && s.status === "pending" ? (
              tracking === "done" ? (
                <Text style={{ color: colors.accent, fontSize: 13 }}>
                  <Ionicons name="checkmark" size={13} /> In your tickets: we’ll grade it as the results come in.
                </Text>
              ) : (
                <View style={{ gap: space.xs }}>
                  <Button title="Track in my tickets" kind="secondary" icon="ticket-outline" busy={tracking === "busy"} onPress={track} />
                  {tracking !== "idle" && tracking !== "busy" ? <Text style={{ color: colors.danger, fontSize: 12 }}>{tracking}</Text> : null}
                </View>
              )
            ) : null}
          </>
        ) : isToday && toPlay > 0 ? (
          <Muted>The code is on its way: SportyBet didn’t take the slip yet, so we try again every {retry} minutes.</Muted>
        ) : (
          <Muted>No booking code: SportyBet didn’t take this slip before its matches started.</Muted>
        )}
      </View>

      {s.within_target === false || (s.days ?? 1) > 1 || s.bookable === false ? (
        <Muted style={{ fontSize: 11, marginBottom: space.sm }}>
          {[s.within_target === false && `Closest to ${s.target}x the picks allowed`,
            (s.days ?? 1) > 1 && "includes the next day's matches",
            s.bookable === false && "some picks weren't on SportyBet yet"].filter(Boolean).join(" · ")}
        </Muted>
      ) : null}

      {s.picks.map((p, i) => (
        <LegRow key={i} leg={{ ...p, pick: `${p.market_name ? `${p.market_name}: ` : ""}${p.label || p.code}` }} />
      ))}
    </Card>
  );
}

const styles = StyleSheet.create({
  days: { paddingHorizontal: space.md, paddingVertical: space.sm, gap: space.sm },
  chip: { paddingHorizontal: space.md, paddingVertical: 7, borderRadius: radius.pill, backgroundColor: colors.surface,
    borderWidth: 1, borderColor: colors.border },
  chipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  chipText: { color: colors.text, fontSize: 13, fontWeight: "700" },
  tabs: { paddingHorizontal: space.md, gap: space.sm, paddingBottom: space.md },
  tab: { width: 112, padding: space.md, borderRadius: radius.md, backgroundColor: colors.surface, borderWidth: 1,
    borderColor: colors.border, gap: 3 },
  tabActive: { borderColor: colors.accent, backgroundColor: "rgba(184,245,58,0.06)" },
  tabOdds: { color: colors.text, fontSize: 22, fontWeight: "800" },
  slip: { marginHorizontal: space.md, paddingBottom: space.sm },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", gap: space.md, marginBottom: space.md },
  big: { color: colors.text, fontSize: 40, fontWeight: "800", marginTop: 2 },
  bigX: { color: colors.muted, fontSize: 22 },
  h: { color: colors.text, fontSize: 16, fontWeight: "700" },
  code: { backgroundColor: colors.sunken, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    padding: space.md, gap: space.sm, marginBottom: space.md },
  codeText: { color: colors.text, fontSize: 30, fontWeight: "800", letterSpacing: 4, fontFamily: "monospace" },
  actions: { flexDirection: "row", gap: space.sm },
  warn: { color: colors.warn, fontSize: 12 },
  footer: { textAlign: "center", padding: space.xl, fontSize: 11 },
});
