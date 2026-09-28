import { useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, SectionList, StyleSheet, Text, View } from "react-native";
import { colors, radius, space } from "@/lib/theme";
import { dayLabel } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import type { MatchdayMatch, MatchdayResponse, StripResponse } from "@/lib/types";
import { MatchRow } from "@/components/MatchRow";
import { Empty, ErrorState, Loading, Muted } from "@/components/ui";

const LIVE_REFRESH_MS = 60_000;

/** The day's matches by competition, live ones first within each; a strip of days to move between. */
export default function Predictions() {
  const strip = useApi<StripResponse>("/api/matchday/strip");
  const [day, setDay] = useState<string | null>(null);
  const today = strip.data?.today ?? null;
  const date = day ?? today;
  // Poll every minute only while a match is being played
  const md = useApi<MatchdayResponse>(date ? `/api/matchday?date=${date}` : null,
    d => (d.matches.some(m => m.status === "live") ? LIVE_REFRESH_MS : null));

  const sections = useMemo(() => {
    const ms = md.data?.matches ?? [];
    const by = new Map<string, { title: string; flag: string; data: MatchdayMatch[] }>();
    for (const m of ms) {
      const k = m.league_name || m.league;
      if (!by.has(k)) by.set(k, { title: k, flag: m.flag, data: [] });
      by.get(k)!.data.push(m);
    }
    const order = (m: MatchdayMatch) => (m.status === "live" ? 0 : m.status === "scheduled" ? 1 : 2);
    return [...by.values()].map(s => ({ ...s, data: s.data.sort((a, b) => order(a) - order(b) || a.time.localeCompare(b.time)) }))
      .sort((a, b) => Math.min(...a.data.map(order)) - Math.min(...b.data.map(order)));
  }, [md.data]);

  if (strip.error && !strip.data) return <ErrorState error={strip.error} what="Predictions" onRetry={strip.reload} />;
  if (!date) return <Loading label="Loading today's matches…" />;

  const s = md.data?.summary;
  return (
    <View style={{ flex: 1 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.strip} contentContainerStyle={styles.stripInner}>
        {(strip.data?.days ?? []).map(d => {
          const active = d.date === date;
          return (
            <Pressable key={d.date} onPress={() => setDay(d.date)} style={[styles.chip, active && styles.chipActive]}>
              <Text style={[styles.chipText, active && { color: colors.accentInk }]}>{dayLabel(d.date, today ?? d.date)}</Text>
              <Text style={[styles.chipSub, active && { color: colors.accentInk }]}>{d.total} games</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      {md.error && !md.data ? <ErrorState error={md.error} what="Predictions" onRetry={md.reload} /> :
        !md.data ? <Loading /> : (
          <SectionList
            sections={sections}
            keyExtractor={m => m.key}
            renderItem={({ item }) => <MatchRow m={item} />}
            renderSectionHeader={({ section }) => (
              <View style={styles.section}>
                <Text style={styles.sectionText}>{section.flag} {section.title}</Text>
              </View>
            )}
            stickySectionHeadersEnabled
            refreshControl={<RefreshControl refreshing={md.loading && !!md.data} onRefresh={() => md.reload()} tintColor={colors.accent} />}
            ListHeaderComponent={s && s.finished > 0 ? (
              <Muted style={{ paddingHorizontal: space.lg, paddingTop: space.md }}>
                {s.finished} of {s.total} played · our tip came in {s.tip[0]} of {s.tip[0] + s.tip[1]}
              </Muted>
            ) : null}
            ListEmptyComponent={<Empty title="No matches" body="No predictions for this day." />}
            ListFooterComponent={<Muted style={styles.footer}>Predictions are probabilities, not guarantees. 18+ · Bet responsibly.</Muted>}
          />
        )}
    </View>
  );
}

const styles = StyleSheet.create({
  strip: { flexGrow: 0, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  stripInner: { paddingHorizontal: space.md, paddingVertical: space.sm, gap: space.sm },
  chip: { paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.md, backgroundColor: colors.surface,
    borderWidth: 1, borderColor: colors.border, alignItems: "center", minWidth: 76 },
  chipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  chipText: { color: colors.text, fontSize: 13, fontWeight: "700" },
  chipSub: { color: colors.muted, fontSize: 10, marginTop: 1 },
  section: { backgroundColor: colors.sunken, paddingHorizontal: space.lg, paddingVertical: 6 },
  sectionText: { color: colors.text2, fontSize: 12, fontWeight: "700" },
  footer: { textAlign: "center", padding: space.xl, fontSize: 11 },
});
