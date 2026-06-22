import Groq from "groq-sdk";
import { NextRequest, NextResponse } from "next/server";

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

const SYSTEM = `You are BetIQ's AI betting assistant. Help users build football accumulators from AI-predicted matches and generate SportyBet booking codes.

Each prediction has: home, away, date, tip_code ("1"=home win, "X"=draw, "2"=away win, "1X"/"X2"/"12"=double chance, "?"/"Skip"=no tip), tip_1x2, goals_type ("Banker"/"Asian"/"Skip"), goals_confidence (0–1), league, flag.

RULES:
1. ONLY pick games where tip_code is exactly "1", "X", or "2". Never pick "1X", "X2", "12", "?", or "Skip" — these cannot be booked on SportyBet. If a game's tip_code is not "1", "X", or "2", skip it entirely.
2. Prefer Banker games. Never pick more than 10 games.
3. When you present picks, ALWAYS include this block at the end of your reply:

<selections>
[{"home":"TeamA","away":"TeamB","date":"YYYY-MM-DD","tip_code":"1","tip_1x2":"Home Win","goals_confidence":0.82,"league":"PL","flag":"🏴󠁧󠁢󠁥󠁮󠁧󠁿"}]
</selections>

4. Be friendly and concise. Use football emojis occasionally.
5. Tell the user to click "Generate SportyBet Code" after showing picks.
6. If asked to swap or remove a game, update the selections block.`;

export async function POST(req: NextRequest) {
  try {
    const { messages, predictions } = await req.json();

    // Slim predictions to only bookable tips and essential fields
    const slim = ((predictions ?? []) as Record<string, unknown>[])
      .slice(0, 100)
      .filter((p) => p.tip_code && !["?", "Skip"].includes(p.tip_code as string))
      .map((p) => ({
        home: p.home,
        away: p.away,
        date: p.date,
        tip_code: p.tip_code,
        tip_1x2: p.tip_1x2,
        goals_type: p.goals_type,
        conf: typeof p.goals_confidence === "number"
          ? Math.round((p.goals_confidence as number) * 100) + "%"
          : "—",
        league: p.league,
        flag: p.flag,
      }));

    // Build message list: system → context injection → conversation history
    const groqMessages: Groq.Chat.ChatCompletionMessageParam[] = [
      { role: "system", content: SYSTEM },
      {
        role: "user",
        content: `Here are today's available predictions:\n${JSON.stringify(slim)}`,
      },
      {
        role: "assistant",
        content: "Got it — predictions loaded. What kind of accumulator would you like?",
      },
      // Real conversation (skip any leading assistant greeting)
      ...(() => {
        const firstUserIdx = messages.findIndex((m: { role: string }) => m.role === "user");
        return firstUserIdx === -1 ? [] : messages.slice(firstUserIdx);
      })().map((m: { role: string; content: string }) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
    ];

    const completion = await groq.chat.completions.create({
      model: "llama-3.3-70b-versatile",
      messages: groqMessages,
      max_tokens: 1500,
      temperature: 0.4,
    });

    const text = completion.choices[0]?.message?.content ?? "";
    return NextResponse.json({ message: text });
  } catch (err: unknown) {
    console.error("[chat/groq]", err);
    return NextResponse.json({ error: "assistant_unavailable" }, { status: 500 });
  }
}
