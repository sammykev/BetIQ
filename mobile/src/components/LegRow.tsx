import { StyleSheet, Text, View } from "react-native";
import { colors, font, space } from "@/lib/theme";
import { localTime, odds, pct } from "@/lib/format";
import type { LegStatus } from "@/lib/types";
import { Pill } from "./ui";

export interface Leg {
  home: string; away: string; date: string; time?: string;
  pick: string; odds?: number | null; prob?: number | null; status: LegStatus;
  live?: { score: [number, number] | null; minute: string | null; status: string; as_it_stands?: "won" | "lost" | null } | null;
}

/** One pick on a slip or ticket: the match, the pick, its odds, and how it stands. */
export function LegRow({ leg }: { leg: Leg }) {
  const l = leg.live;
  const playing = leg.status === "pending" && l?.status === "live";
  const score = l?.score ? `${l.score[0]}-${l.score[1]}` : null;
  return (
    <View style={styles.row}>
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={styles.match} numberOfLines={1}>{leg.home} v {leg.away}</Text>
        <Text style={styles.pick} numberOfLines={2}>{leg.pick}</Text>
        <Text style={styles.meta}>
          {playing ? <Text style={{ color: colors.danger, fontWeight: "700" }}>{l?.minute ? `${l.minute}'` : "LIVE"} {score}</Text>
            : score && l?.status === "finished" ? `FT ${score}` : `${localTime(leg.date, leg.time)}`}
          {typeof leg.prob === "number" ? `  ·  ${pct(leg.prob)}` : ""}
        </Text>
      </View>
      <View style={{ alignItems: "flex-end", gap: 4 }}>
        <Text style={[styles.odds, font.mono]}>{odds(leg.odds)}</Text>
        <StatusPill status={leg.status} now={playing ? l?.as_it_stands ?? null : null} />
      </View>
    </View>
  );
}

export function StatusPill({ status, now }: { status: string; now?: "won" | "lost" | null }) {
  if (status === "won" || status === "half_won") return <Pill tone="accent">Won</Pill>;
  if (status === "lost" || status === "half_lost") return <Pill tone="danger">Lost</Pill>;
  if (status === "void" || status === "push") return <Pill>Void</Pill>;
  if (status === "none") return <Pill>Not today</Pill>;
  if (now) return <Pill tone={now === "won" ? "accent" : "warn"}>{now === "won" ? "Winning" : "Losing"}</Pill>;
  if (status === "open") return <Pill tone="warn">Open</Pill>;
  return <Pill>Pending</Pill>;
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: space.md, paddingVertical: space.md,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  match: { color: colors.muted, fontSize: 12 },
  pick: { color: colors.text, fontSize: 14, fontWeight: "600" },
  meta: { color: colors.faint, fontSize: 12 },
  odds: { color: colors.text, fontSize: 15, fontWeight: "800" },
});
