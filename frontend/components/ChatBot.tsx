"use client";

import { useEffect, useRef, useState } from "react";
import { useUser } from "@clerk/nextjs";
import clsx from "clsx";
import { X, Send, MessageCircle, Loader2, Copy, Check, Ticket, ChevronDown, ClipboardList } from "lucide-react";
import type { Prediction } from "@/lib/api";

interface Props {
  predictions: Prediction[];
}

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface BookingResult {
  code: string | null;
  bookie: string | null;
  matched: { game: string; tip: string; odds: string }[];
  unmatched: string[];
  total_odds: number | null;
  error: string | null;
  picks?: { home: string; away: string; tip: string; tip_code: string; date: string; league: string }[];
}

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

function parseSelections(text: string): Prediction[] | null {
  const match = text.match(/<selections>([\s\S]*?)<\/selections>/);
  if (!match) return null;
  try {
    return JSON.parse(match[1].trim());
  } catch {
    return null;
  }
}

function stripSelections(text: string): string {
  return text.replace(/<selections>[\s\S]*?<\/selections>/g, "").trim();
}

function BookingCard({ result, onDismiss }: { result: BookingResult; onDismiss: () => void }) {
  const [copiedCode, setCopiedCode] = useState(false);
  const [copiedPicks, setCopiedPicks] = useState(false);

  const copyCode = () => {
    if (result.code) {
      navigator.clipboard.writeText(result.code);
      setCopiedCode(true);
      setTimeout(() => setCopiedCode(false), 2000);
    }
  };

  const copyPicks = () => {
    const picks = result.picks || [];
    const text = picks.map(p =>
      `${p.home} vs ${p.away} · ${p.date}\nTip: ${p.tip} (${p.tip_code})\n`
    ).join("\n");
    navigator.clipboard.writeText(text.trim());
    setCopiedPicks(true);
    setTimeout(() => setCopiedPicks(false), 2000);
  };

  const bookerLabel: Record<string, string> = { sportybet: "SportyBet", "1xbet": "1xBet" };

  return (
    <div className="rounded-xl border border-slate-600 bg-slate-800/80 p-4 space-y-3 text-sm">

      {/* ── Booking code (if generated) ── */}
      {result.code && (
        <div className="space-y-2">
          <p className="text-green-400 font-semibold text-xs uppercase tracking-wide">
            ✅ {bookerLabel[result.bookie || ""] || "Booking"} Code
          </p>
          <div className="flex items-center gap-2 bg-black/30 rounded-lg px-3 py-2">
            <span className="text-xl font-black text-white tracking-widest flex-1 font-mono">
              {result.code}
            </span>
            <button
              onClick={copyCode}
              className="flex items-center gap-1 bg-green-500 hover:bg-green-400 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-colors shrink-0"
            >
              {copiedCode ? <Check size={12} /> : <Copy size={12} />}
              {copiedCode ? "Copied!" : "Copy"}
            </button>
          </div>
          {result.total_odds && (
            <p className="text-yellow-400 font-semibold text-xs">
              Combined odds: ~{result.total_odds}x
            </p>
          )}
          {result.matched.length > 0 && (
            <div className="space-y-1 pt-1 border-t border-slate-700">
              {result.matched.map((m, i) => (
                <div key={i} className="flex justify-between text-xs text-slate-300">
                  <span className="truncate">{m.game}</span>
                  <span className="text-green-400 font-semibold ml-2 shrink-0">{m.tip} @ {m.odds}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Copy card (always shown when picks available) ── */}
      {!result.code && result.picks && result.picks.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-blue-400">
              <ClipboardList size={13} />
              <p className="font-semibold text-xs uppercase tracking-wide">Your Picks</p>
            </div>
            <button
              onClick={copyPicks}
              className="flex items-center gap-1 bg-blue-500 hover:bg-blue-400 text-white text-xs font-bold px-2.5 py-1 rounded-lg transition-colors"
            >
              {copiedPicks ? <Check size={11} /> : <Copy size={11} />}
              {copiedPicks ? "Copied!" : "Copy all"}
            </button>
          </div>

          <div className="space-y-1.5">
            {result.picks.map((p, i) => (
              <div key={i} className="bg-black/30 rounded-lg px-3 py-2 flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-white text-xs font-semibold truncate">{p.home} vs {p.away}</p>
                  <p className="text-slate-400 text-[10px]">{p.league} · {p.date}</p>
                </div>
                <span className="shrink-0 text-xs font-bold bg-green-500/20 text-green-300 border border-green-500/30 px-2 py-0.5 rounded-full">
                  {p.tip}
                </span>
              </div>
            ))}
          </div>

          <p className="text-slate-500 text-[10px] pt-1">
            Copy these picks and add them manually on any betting site.
          </p>

          <div className="flex gap-2 flex-wrap">
            {[
              { name: "SportyBet", url: "https://www.sportybet.com/ng/" },
              { name: "Bet9ja",    url: "https://web.bet9ja.com/"        },
              { name: "1xBet",     url: "https://1xbet.ng/en/"           },
            ].map(b => (
              <a key={b.name} href={b.url} target="_blank" rel="noopener noreferrer"
                className="text-[10px] font-semibold px-2.5 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors">
                {b.name} ↗
              </a>
            ))}
          </div>

          {result.error && (
            <p className="text-slate-500 text-[10px] italic">{result.error}</p>
          )}
        </div>
      )}

      {/* ── Error only (no code, no picks) ── */}
      {!result.code && (!result.picks || result.picks.length === 0) && (
        <p className="text-red-400 text-xs">⚠️ {result.error || "Could not generate booking code."}</p>
      )}

      <button onClick={onDismiss} className="text-slate-500 text-xs hover:text-slate-300 transition-colors pt-1">
        Dismiss
      </button>
    </div>
  );
}

function AssistantBubble({
  content,
  predictions,
  userId,
}: {
  content: string;
  predictions: Prediction[];
  userId?: string;
}) {
  const selections = parseSelections(content);
  const displayText = stripSelections(content);
  const [booking, setBooking] = useState<BookingResult | null>(null);
  const [loading, setLoading] = useState(false);

  const generateCode = async () => {
    if (!selections) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/booking`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ predictions: selections }),
      });
      const data = await res.json();
      const sanitised = {
        code: data.code ?? null,
        bookie: data.bookie ?? null,
        matched: Array.isArray(data.matched) ? data.matched : [],
        unmatched: Array.isArray(data.unmatched) ? data.unmatched : [],
        total_odds: typeof data.total_odds === "number" ? data.total_odds : null,
        picks: Array.isArray(data.picks) ? data.picks : [],
        error: data.code ? null : (data.error || "Booking code unavailable — use the copy card to add picks manually."),
      };
      setBooking(sanitised);
      // Auto-save to user's accumulator history
      if (sanitised.code && userId) {
        fetch(`${API_URL}/api/user/codes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            uid: userId,
            entry: { code: sanitised.code, games: sanitised.matched, total_odds: sanitised.total_odds, date: new Date().toISOString().slice(0, 10) },
          }),
        }).catch(() => {});
      }
    } catch {
      setBooking({
        code: null,
        bookie: null,
        matched: [],
        unmatched: [],
        total_odds: null,
        picks: [],
        error: "Could not generate booking code. Please try again.",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl rounded-tl-sm px-4 py-3 text-slate-100 text-sm whitespace-pre-wrap leading-relaxed max-w-[85%]">
        {displayText}
      </div>

      {selections && !booking && (
        <button
          onClick={generateCode}
          disabled={loading}
          className="self-start flex items-center gap-2 bg-green-500 hover:bg-green-400 disabled:opacity-60 text-black font-bold text-xs px-4 py-2 rounded-xl transition-colors"
        >
          {loading ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Ticket size={13} />
          )}
          {loading ? "Generating code…" : "Generate SportyBet Code"}
        </button>
      )}

      {booking && (
        <BookingCard result={booking} onDismiss={() => setBooking(null)} />
      )}
    </div>
  );
}

const SUGGESTIONS = [
  "Give me 5 banker games this week",
  "Best 3-game accumulator today",
  "High confidence home wins only",
  "Safe doubles for this weekend",
];

export function ChatBot({ predictions }: Props) {
  const { user } = useUser();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [minimised, setMinimised] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && messages.length === 0) {
      setMessages([
        {
          role: "assistant",
          content:
            "👋 Hey! I'm BetIQ's betting assistant.\n\nTell me what you want — e.g. *\"5 banker games this weekend\"* or *\"best 3-fold for today\"* — and I'll pick the games and generate a SportyBet booking code for you.",
        },
      ]);
    }
  }, [open, messages.length]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async (text?: string) => {
    const userText = (text ?? input).trim();
    if (!userText || loading) return;

    const userMsg: Message = { role: "user", content: userText };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: newMessages,
          predictions: predictions.slice(0, 120),
        }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        setMessages([...newMessages, {
          role: "assistant",
          content: "Sorry, I'm having trouble right now. Please try again in a moment.",
        }]);
        return;
      }
      setMessages([...newMessages, { role: "assistant", content: data.message }]);
    } catch {
      setMessages([
        ...newMessages,
        {
          role: "assistant",
          content: "Couldn't reach the assistant. Check your connection and try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <>
      {/* Floating button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-50 w-14 h-14 bg-green-500 hover:bg-green-400 text-black rounded-full shadow-lg flex items-center justify-center transition-all hover:scale-105 active:scale-95"
          title="BetIQ Assistant"
        >
          <MessageCircle size={24} />
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="fixed bottom-6 right-6 z-50 w-[360px] max-w-[calc(100vw-2rem)] flex flex-col bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl overflow-hidden"
          style={{ height: minimised ? "auto" : "560px" }}
        >
          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-3 bg-slate-800 border-b border-slate-700 shrink-0">
            <img src="/logo.svg" alt="" className="w-7 h-7 rounded-full" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-white leading-none">BetIQ Assistant</p>
              <p className="text-[10px] text-green-400 mt-0.5">AI · SportyBet booking</p>
            </div>
            <button
              onClick={() => setMinimised(!minimised)}
              className="p-1 hover:bg-slate-700 rounded-lg text-slate-400"
            >
              <ChevronDown size={14} className={clsx("transition-transform", minimised && "rotate-180")} />
            </button>
            <button
              onClick={() => { setOpen(false); setMinimised(false); }}
              className="p-1 hover:bg-slate-700 rounded-lg text-slate-400"
            >
              <X size={14} />
            </button>
          </div>

          {!minimised && (
            <>
              {/* Messages */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {messages.map((m, i) =>
                  m.role === "user" ? (
                    <div key={i} className="flex justify-end">
                      <div className="bg-green-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm max-w-[80%] whitespace-pre-wrap">
                        {m.content}
                      </div>
                    </div>
                  ) : (
                    <AssistantBubble key={i} content={m.content} predictions={predictions} userId={user?.id} />
                  )
                )}

                {loading && (
                  <div className="flex gap-2 items-center text-slate-500 text-sm">
                    <Loader2 size={14} className="animate-spin" />
                    Picking games…
                  </div>
                )}

                {/* Quick suggestions — show only at start */}
                {messages.length === 1 && !loading && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => send(s)}
                        className="text-xs px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-300 rounded-full transition-colors"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}

                <div ref={bottomRef} />
              </div>

              {/* Input */}
              <div className="p-3 border-t border-slate-700 bg-slate-800 shrink-0">
                <div className="flex gap-2">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKey}
                    placeholder="Ask for picks…"
                    disabled={loading}
                    className="flex-1 bg-slate-700 border border-slate-600 text-slate-100 placeholder-slate-500 text-sm rounded-xl px-3 py-2 outline-none focus:border-green-500 transition-colors disabled:opacity-50"
                  />
                  <button
                    onClick={() => send()}
                    disabled={!input.trim() || loading}
                    className="bg-green-500 hover:bg-green-400 disabled:opacity-40 text-black rounded-xl px-3 py-2 transition-colors"
                  >
                    <Send size={15} />
                  </button>
                </div>
                <p className="text-center text-[10px] text-slate-600 mt-2">
                  Powered by Claude AI · SportyBet Nigeria
                </p>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
