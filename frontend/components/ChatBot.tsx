"use client";

import { useEffect, useRef, useState } from "react";
import { useUser } from "@clerk/nextjs";
import clsx from "clsx";
import { X, Send, MessageCircle, Loader2, Copy, Check, Ticket, ChevronDown } from "lucide-react";
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
  matched: { game: string; tip: string; odds: string }[];
  unmatched: string[];
  total_odds: number | null;
  error: string | null;
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
  const [copied, setCopied] = useState(false);

  const copy = () => {
    if (result.code) {
      navigator.clipboard.writeText(result.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="rounded-xl border border-green-500/40 bg-green-500/10 p-4 space-y-3 text-sm">
      {result.code ? (
        <>
          <p className="text-green-400 font-semibold text-xs uppercase tracking-wide">
            ✅ SportyBet Booking Code
          </p>
          <div className="flex items-center gap-2">
            <span className="text-2xl font-black text-white tracking-widest flex-1">
              {result.code}
            </span>
            <button
              onClick={copy}
              className="flex items-center gap-1 bg-green-500 hover:bg-green-400 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <p className="text-slate-400 text-xs">
            Paste this code on the SportyBet app or website to load your bet slip.
          </p>
          {result.total_odds && (
            <p className="text-yellow-400 font-semibold text-xs">
              Combined odds: ~{result.total_odds}x
            </p>
          )}
        </>
      ) : (
        <p className="text-red-400 text-xs">
          ⚠️ {result.error || "Could not generate booking code."}
        </p>
      )}

      {result.matched.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-slate-700">
          <p className="text-slate-400 text-xs font-medium">Booked games:</p>
          {result.matched.map((m, i) => (
            <div key={i} className="flex justify-between text-xs text-slate-300">
              <span>{m.game}</span>
              <span className="text-green-400 font-semibold">{m.tip} @ {m.odds}</span>
            </div>
          ))}
        </div>
      )}

      {result.unmatched.length > 0 && (
        <div className="pt-1 border-t border-slate-700 space-y-0.5">
          <p className="text-slate-500 text-xs font-medium">Not found on SportyBet:</p>
          {result.unmatched.map((u, i) => (
            <p key={i} className="text-slate-600 text-xs">• {u}</p>
          ))}
        </div>
      )}

      <button
        onClick={onDismiss}
        className="text-slate-500 text-xs hover:text-slate-300 transition-colors"
      >
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
        matched: Array.isArray(data.matched) ? data.matched : [],
        unmatched: Array.isArray(data.unmatched) ? data.unmatched : [],
        total_odds: typeof data.total_odds === "number" ? data.total_odds : null,
        error: data.code ? null : "Booking code unavailable — try again shortly.",
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
        matched: [],
        unmatched: [],
        total_odds: null,
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
