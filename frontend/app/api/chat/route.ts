import { GoogleGenerativeAI } from "@google/generative-ai";
import { NextRequest, NextResponse } from "next/server";

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");

const SYSTEM = `You are BetIQ's AI betting assistant. Help users build football accumulators from AI-predicted matches and generate SportyBet booking codes.

Each prediction has: home, away, date, tip_code ("1"=home win, "X"=draw, "2"=away win, "1X"/"X2"/"12"=double chance, "?"/"Skip"=no tip), tip_1x2, goals_type ("Banker"/"Asian"/"Skip"), goals_confidence (0–1), league, flag.

RULES:
1. Only pick games where tip_code is "1", "X", or "2" — only these can be booked on SportyBet.
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

    // Slim each prediction to only the fields the model needs
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

    const model = genAI.getGenerativeModel({
      model: "gemini-2.0-flash-exp",
      systemInstruction: SYSTEM,
    });

    // Build Gemini history from prior messages.
    // Gemini requires history to start with a "user" turn, so we:
    // 1. Prepend a user turn containing today's predictions as context
    // 2. Add a short model acknowledgement
    // 3. Then append the real conversation (skipping any leading assistant turns)
    const contextTurn = {
      role: "user" as const,
      parts: [{ text: `Here are today's available predictions:\n${JSON.stringify(slim)}` }],
    };
    const ackTurn = {
      role: "model" as const,
      parts: [{ text: "Got it — I have today's predictions loaded. What kind of accumulator would you like?" }],
    };

    const prior = messages.slice(0, -1);
    const firstUserIdx = prior.findIndex((m: { role: string }) => m.role === "user");
    const realHistory = firstUserIdx === -1 ? [] : prior.slice(firstUserIdx).map(
      (m: { role: string; content: string }) => ({
        role: m.role === "assistant" ? ("model" as const) : ("user" as const),
        parts: [{ text: m.content }],
      })
    );

    const history = [contextTurn, ackTurn, ...realHistory];
    const lastMessage = messages[messages.length - 1];

    const chat = model.startChat({ history });
    const result = await chat.sendMessage(lastMessage.content);
    const text = result.response.text();

    return NextResponse.json({ message: text });
  } catch (err: unknown) {
    console.error("[chat/gemini]", err);
    return NextResponse.json({ error: "assistant_unavailable" }, { status: 500 });
  }
}
