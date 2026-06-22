import Anthropic from "@anthropic-ai/sdk";
import { NextRequest, NextResponse } from "next/server";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export async function POST(req: NextRequest) {
  try {
    const { messages, predictions } = await req.json();

    // Send up to 120 predictions as context (haiku has large context)
    const ctx = (predictions ?? []).slice(0, 120);

    const system = `You are BetIQ's AI betting assistant. Your job is to help users build football accumulators from our AI-predicted matches and generate SportyBet booking codes.

CURRENT PREDICTIONS (JSON):
${JSON.stringify(ctx, null, 2)}

Each prediction has:
- home / away: team names
- date / time: match schedule
- league / league_name / flag: competition
- tip_1x2: e.g. "Home Win", "Draw", "Away Win", "Skip"
- tip_code: "1" (home win), "X" (draw), "2" (away win), "1X", "X2", "12", "?" or "Skip"
- goals_type: "Banker", "Asian", or "Skip"
- goals_confidence: 0.0 – 1.0 (higher = more confident)
- p_home / p_draw / p_away: model win probabilities

RULES:
1. Only recommend games where tip_code is "1", "X", or "2" — these can be booked on SportyBet.
2. Prefer Banker games for safe slips; include Asian only if asked.
3. Never suggest more than 10 games per slip.
4. When presenting picks, ALWAYS end your reply with a JSON block in exactly this format so the UI can parse it:

<selections>
[{"home":"TeamA","away":"TeamB","date":"2025-07-05","tip_code":"1","tip_1x2":"Home Win","goals_confidence":0.82,"league":"PL","flag":"🏴󠁧󠁢󠁥󠁮󠁧󠁿"},...]
</selections>

5. Be friendly, concise, and use football emojis occasionally.
6. After showing picks, tell the user to click "Generate SportyBet Code" to get their booking code.
7. If the user asks to remove or swap a game, update the selections JSON accordingly.
8. If no suitable games are found for their criteria, say so clearly.`;

    const response = await client.messages.create({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 1500,
      system,
      messages,
    });

    const text =
      response.content[0].type === "text" ? response.content[0].text : "";

    return NextResponse.json({ message: text });
  } catch (err: unknown) {
    console.error("[chat]", err);
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
