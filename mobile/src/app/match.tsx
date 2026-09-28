import { useMemo } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { colors, font, radius, space } from "@/lib/theme";
import { localTime, pct } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import type { FactMatch, MatchdayMatch, MatchdayResponse, MatchFacts, TeamAverages } from "@/lib/types";
import { Button, Card, Empty, Eyebrow, Loading, Muted, openSite, Pill, Title } from "@/components/ui";

const key = (h: string, a: string) => `${h.trim().toLowerCase()}|${a.trim().toLowerCase()}`;

export default function MatchScreen() {
  const { home = "", away = "", date = "" } = useLocalSearchParams<{ home: string; away: string; date: string }>();
  // Score and stats every minute while it's being played
  const md = useApi<MatchdayResponse>(date ? `/api/matchday?date=${date}` : null,
    d => (d.matches.some(x => x.key === key(home, away) && x.status === "live") ? 60_000 : null));
  const facts = useApi<MatchFacts>(`/api/match/facts?${new URLSearchParams({ home, away, date })}`);
  const m = useMemo(() => md.data?.matches.find(x => x.key === key(home, away)) ?? null, [md.data, home, away]);

  return (
    <>
      <Stack.Screen options={{ title: `${home} v ${away}` }} />
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
        {!md.data && md.loading ? <Loading /> : m ? <Header m={m} /> : (
          <Card><Title>{home} v {away}</Title><Muted>{date}</Muted></Card>
        )}
        {m && <Chances m={m} />}
        {m && <Markets m={m} />}
        {m?.stats && Object.keys(m.stats).length > 0 && <LiveStats m={m} />}
        {facts.data?.averages && (facts.data.averages.home || facts.data.averages.away) && (
          <Averages home={home} away={away} a={facts.data.averages.home} b={facts.data.averages.away} n={facts.data.averages.n} />
        )}
        {facts.data && (facts.data.home.length > 0 || facts.data.away.length > 0) && (
          <Card style={{ gap: space.md }}>
            <Eyebrow>Last 5 matches</Eyebrow>
            <Form name={home} rows={facts.data.home} />
            <Form name={away} rows={facts.data.away} />
          </Card>
        )}
        {!m && !md.loading && !facts.data && <Empty title="Match not found" body="It may have moved to another day." />}
        <Button title="Full analysis on the website" kind="secondary" icon="open-outline"
          onPress={() => openSite(`/match?${new URLSearchParams({ home, away, date })}`)} />
        <Muted style={{ textAlign: "center", fontSize: 11 }}>Probabilities, not guarantees. 18+ · Bet responsibly.</Muted>
      </ScrollView>
    </>
  );
}

function Header({ m }: { m: MatchdayMatch }) {
  const live = m.status === "live";
  return (
    <Card style={{ alignItems: "center", gap: space.sm }}>
      <Eyebrow>{m.flag} {m.league_name}</Eyebrow>
      <View style={styles.headerRow}>
        <Text style={styles.headerTeam} numberOfLines={2}>{m.home}</Text>
        <View style={styles.scoreBox}>
          {m.score ? <Text style={[styles.score, font.mono, live && { color: colors.danger }]}>{m.score[0]} - {m.score[1]}</Text>
            : <Text style={styles.kickoff}>{localTime(m.date, m.time)}</Text>}
          {live ? <Pill tone="live">{m.minute ? `${m.minute}'` : "LIVE"}</Pill>
            : m.status === "finished" ? <Pill>{m.aet ? "AET" : "Full time"}</Pill> : null}
        </View>
        <Text style={[styles.headerTeam, { textAlign: "right" }]} numberOfLines={2}>{m.away}</Text>
      </View>
      {m.pred.tip_1x2 ? <Muted>Our tip: <Text style={{ color: colors.accent, fontWeight: "700" }}>{m.pred.tip_1x2}</Text>
        {typeof m.pred.tip_confidence === "number" ? ` · ${pct(m.pred.tip_confidence)}` : ""}</Muted> : null}
    </Card>
  );
}

function Chances({ m }: { m: MatchdayMatch }) {
  const { p_home: h, p_draw: d, p_away: a } = m.pred;
  if (typeof h !== "number" || typeof d !== "number" || typeof a !== "number") return null;
  const parts = [{ p: h, c: colors.accent, l: m.home }, { p: d, c: colors.draw, l: "Draw" }, { p: a, c: colors.danger, l: m.away }];
  return (
    <Card style={{ gap: space.md }}>
      <Eyebrow>Result</Eyebrow>
      <View style={styles.bar}>
        {parts.map((x, i) => <View key={i} style={{ flex: Math.max(x.p, 0.01), backgroundColor: x.c }} />)}
      </View>
      <View style={styles.row3}>
        {parts.map((x, i) => (
          <View key={i} style={{ flex: 1, alignItems: i === 0 ? "flex-start" : i === 1 ? "center" : "flex-end" }}>
            <Text style={[styles.big, font.mono]}>{pct(x.p)}</Text>
            <Muted style={{ fontSize: 11 }} >{x.l}</Muted>
          </View>
        ))}
      </View>
    </Card>
  );
}

function Markets({ m }: { m: MatchdayMatch }) {
  const p = m.pred;
  const rows: [string, number | undefined, string?][] = [
    ["Over 1.5 goals", p.p_over15], ["Over 2.5 goals", p.p_over25], ["Over 3.5 goals", p.p_over35],
    ["Both teams score", p.p_btts],
    ["Over 9.5 corners", p.corners_over, typeof p.corners_mean === "number" ? `about ${p.corners_mean.toFixed(1)} expected` : undefined],
    ["Over 4.5 booking points", p.bookings_over, typeof p.bookings_mean === "number" ? `about ${p.bookings_mean.toFixed(1)} expected` : undefined],
  ];
  const shown = rows.filter(r => typeof r[1] === "number");
  if (!shown.length) return null;
  return (
    <Card style={{ gap: space.sm }}>
      <Eyebrow>Markets</Eyebrow>
      {shown.map(([label, prob, note]) => (
        <View key={label} style={styles.market}>
          <View style={{ flex: 1 }}>
            <Text style={styles.marketLabel}>{label}</Text>
            {note ? <Muted style={{ fontSize: 11 }}>{note}</Muted> : null}
          </View>
          <View style={styles.meter}><View style={[styles.meterFill, { width: `${Math.round((prob as number) * 100)}%` }]} /></View>
          <Text style={[styles.marketPct, font.mono]}>{pct(prob)}</Text>
        </View>
      ))}
      {p.referee ? <Muted style={{ fontSize: 11 }}>Referee: {p.referee}</Muted> : null}
    </Card>
  );
}

const STAT_NAMES: Record<string, string> = {
  possession: "Possession %", shots: "Shots", sot: "On target", corners: "Corners", fouls: "Fouls",
  offsides: "Offsides", saves: "Saves", yellow: "Yellow cards", red: "Red cards",
};

function LiveStats({ m }: { m: MatchdayMatch }) {
  return (
    <Card style={{ gap: space.sm }}>
      <Eyebrow>{m.status === "live" ? "Live stats" : "Match stats"}</Eyebrow>
      {Object.entries(m.stats ?? {}).filter(([k]) => STAT_NAMES[k]).map(([k, [h, a]]) => (
        <View key={k} style={styles.row3}>
          <Text style={[styles.statVal, font.mono, h > a && { color: colors.text, fontWeight: "800" }]}>{h}</Text>
          <Muted style={{ flex: 2, textAlign: "center" }}>{STAT_NAMES[k]}</Muted>
          <Text style={[styles.statVal, font.mono, { textAlign: "right" }, a > h && { color: colors.text, fontWeight: "800" }]}>{a}</Text>
        </View>
      ))}
    </Card>
  );
}

type Row = [string, (t: TeamAverages) => number | null | undefined, boolean?];
const AVG_ROWS: [string, Row[]][] = [
  ["Goals", [["Scored", t => t.goals.for], ["Conceded", t => t.goals.against], ["Match goals", t => t.goals.total],
    ["Over 1.5", t => t.over["1.5"], true], ["Over 2.5", t => t.over["2.5"], true], ["Over 3.5", t => t.over["3.5"], true],
    ["Both scored", t => t.btts, true], ["Clean sheets", t => t.clean_sheet, true]]],
  ["Result", [["Won", t => t.results.won, true], ["Drawn", t => t.results.drawn, true], ["Lost", t => t.results.lost, true]]],
  ["Corners", [["Won", t => t.corners?.for], ["Conceded", t => t.corners?.against], ["Match corners", t => t.corners?.total]]],
  ["Cards (booking points)", [["Team", t => t.bookings?.for], ["Match total", t => t.bookings?.total]]],
  ["Shots", [["Shots", t => t.shots?.for], ["On target", t => t.sot?.for], ["On target conceded", t => t.sot?.against]]],
];

function Averages({ home, away, a, b, n }: { home: string; away: string; a: TeamAverages | null; b: TeamAverages | null; n: number }) {
  const show = (t: TeamAverages | null, [, get, p]: Row) => {
    const v = t ? get(t) : null;
    return v == null ? "—" : p ? pct(v) : v.toFixed(1);
  };
  return (
    <Card style={{ gap: space.sm }}>
      <Eyebrow>Team averages · last {n}</Eyebrow>
      <View style={styles.row3}>
        <Text style={styles.avgTeam} numberOfLines={1}>{home}</Text>
        <View style={{ flex: 2 }} />
        <Text style={[styles.avgTeam, { textAlign: "right" }]} numberOfLines={1}>{away}</Text>
      </View>
      {AVG_ROWS.map(([title, rows]) => {
        const keep = rows.filter(r => (a && r[1](a) != null) || (b && r[1](b) != null));
        if (!keep.length) return null;
        return (
          <View key={title} style={{ gap: 2 }}>
            <Eyebrow style={{ textAlign: "center", marginTop: space.sm }}>{title}</Eyebrow>
            {keep.map(r => (
              <View key={r[0]} style={styles.row3}>
                <Text style={[styles.statVal, font.mono]}>{show(a, r)}</Text>
                <Muted style={{ flex: 2, textAlign: "center" }}>{r[0]}</Muted>
                <Text style={[styles.statVal, font.mono, { textAlign: "right" }]}>{show(b, r)}</Text>
              </View>
            ))}
          </View>
        );
      })}
      <Muted style={{ fontSize: 11, marginTop: space.sm }}>Per match, all competitions, including matches just played. Cards: yellow 1, red 2.</Muted>
    </Card>
  );
}

const OUTCOME: Record<FactMatch["outcome"], string> = { W: colors.accent, D: colors.draw, L: colors.danger };

function Form({ name, rows }: { name: string; rows: FactMatch[] }) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={styles.avgTeam}>{name}</Text>
      {rows.length === 0 ? <Muted>No recent results on record.</Muted> : rows.map((r, i) => (
        <View key={i} style={styles.formRow}>
          <View style={[styles.badge, { backgroundColor: OUTCOME[r.outcome] }]}>
            <Text style={styles.badgeText}>{r.outcome}</Text>
          </View>
          <Text style={styles.formOpp} numberOfLines={1}>{r.venue === "H" ? "vs" : "at"} {r.opponent}</Text>
          <Text style={[styles.statVal, font.mono, { flex: 0 }]}>{r.venue === "H" ? `${r.hg}-${r.ag}` : `${r.ag}-${r.hg}`}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", alignItems: "center", gap: space.md, width: "100%" },
  headerTeam: { flex: 1, color: colors.text, fontSize: 16, fontWeight: "800" },
  scoreBox: { alignItems: "center", gap: 4 },
  score: { color: colors.text, fontSize: 28, fontWeight: "900" },
  kickoff: { color: colors.text, fontSize: 20, fontWeight: "800" },
  bar: { flexDirection: "row", height: 10, borderRadius: radius.pill, overflow: "hidden", gap: 2 },
  row3: { flexDirection: "row", alignItems: "center" },
  big: { color: colors.text, fontSize: 18, fontWeight: "800" },
  market: { flexDirection: "row", alignItems: "center", gap: space.md, paddingVertical: 4 },
  marketLabel: { color: colors.text2, fontSize: 14 },
  meter: { width: 70, height: 6, borderRadius: radius.pill, backgroundColor: colors.sunken, overflow: "hidden" },
  meterFill: { height: 6, backgroundColor: colors.accent },
  marketPct: { width: 44, textAlign: "right", color: colors.text, fontWeight: "800" },
  statVal: { flex: 1, color: colors.text2, fontSize: 14 },
  avgTeam: { flex: 1, color: colors.text, fontWeight: "700", fontSize: 13 },
  formRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
  badge: { width: 20, height: 20, borderRadius: 5, alignItems: "center", justifyContent: "center" },
  badgeText: { color: colors.accentInk, fontSize: 11, fontWeight: "900" },
  formOpp: { flex: 1, color: colors.text2, fontSize: 13 },
});
