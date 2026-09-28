import { Pressable, StyleSheet, Text, View } from "react-native";
import { router } from "expo-router";
import { colors, font, space } from "@/lib/theme";
import { localTime, pct } from "@/lib/format";
import type { MatchdayMatch } from "@/lib/types";
import { Pill } from "./ui";

const WON = new Set(["won", "half_won"]);
const LOST = new Set(["lost", "half_lost"]);

/** One match: kick-off or live minute, the teams and score, our tip and how it did. */
export function MatchRow({ m }: { m: MatchdayMatch }) {
  const live = m.status === "live";
  const done = m.status === "finished";
  const tip = m.grades?.tip;
  const conf = m.pred.tip_confidence;
  const [hg, ag] = m.score ?? [null, null];
  const status = live ? (m.minute ? `${m.minute}'` : "LIVE") : done ? (m.aet ? "AET" : "FT")
    : m.status === "postponed" ? "PST" : localTime(m.date, m.time);
  return (
    <Pressable onPress={() => router.push({ pathname: "/match", params: { home: m.home, away: m.away, date: m.date } })}
      style={({ pressed }) => [styles.row, pressed && { backgroundColor: colors.raised }]}
      accessibilityRole="button" accessibilityLabel={`${m.home} versus ${m.away}`}>
      <Text style={[styles.status, live && { color: colors.danger }]}>{status}</Text>
      <View style={styles.teams}>
        {[{ name: m.home, g: hg, win: hg != null && ag != null && hg > ag },
          { name: m.away, g: ag, win: hg != null && ag != null && ag > hg }].map((t, i) => (
          <View key={i} style={styles.teamLine}>
            <Text style={[styles.team, t.win && { fontWeight: "800" }]} numberOfLines={1}>{t.name}</Text>
            {t.g != null && <Text style={[styles.goals, font.mono, live && { color: colors.danger }]}>{t.g}</Text>}
          </View>
        ))}
      </View>
      <View style={styles.tip}>
        {m.pred.tip_1x2 ? (
          <>
            <Text style={styles.tipText} numberOfLines={1}>{m.pred.tip_1x2}</Text>
            {tip && done ? (
              <Pill tone={WON.has(tip.verdict) ? "accent" : LOST.has(tip.verdict) ? "danger" : "muted"}>
                {WON.has(tip.verdict) ? "Won" : LOST.has(tip.verdict) ? "Lost" : "Void"}
              </Pill>
            ) : typeof conf === "number" ? <Text style={[styles.conf, font.mono]}>{pct(conf)}</Text> : null}
          </>
        ) : m.locked ? <Pill>Locked</Pill> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", paddingVertical: space.md, paddingHorizontal: space.lg, gap: space.md,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  status: { width: 58, color: colors.muted, fontSize: 12, fontWeight: "700" },
  teams: { flex: 1, gap: 4 },
  teamLine: { flexDirection: "row", alignItems: "center", gap: space.sm },
  team: { flex: 1, color: colors.text, fontSize: 14 },
  goals: { color: colors.text, fontSize: 14, fontWeight: "800", minWidth: 14, textAlign: "right" },
  tip: { width: 96, alignItems: "flex-end", gap: 4 },
  tipText: { color: colors.text2, fontSize: 12, fontWeight: "600" },
  conf: { color: colors.accent, fontSize: 13, fontWeight: "800" },
});
