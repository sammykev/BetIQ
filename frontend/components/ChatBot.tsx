"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { X, Send, MessageCircle, Loader2, Check, Ticket, ChevronDown } from "lucide-react";
import type { Prediction } from "@/lib/api";
import { useBetSlip } from "@/lib/useBetSlip";
import { selectionFromPrediction, type SlipSelection } from "@/lib/slip";

interface Props {
  predictions: Prediction[];
}

interface Message {
  role: "user" | "assistant";
  content: string;
}


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

function AssistantBubble({ content, predictions }: { content: string; predictions: Prediction[] }) {
  const selections = parseSelections(content);
  const displayText = stripSelections(content);
  const slip = useBetSlip();
  const [addedToSlip, setAddedToSlip] = useState(false);

  // The assistant's picks as slip selections, with the model's probability when we have it
  const addToSlip = () => {
    if (!selections) return;
    const list = selections.flatMap((sel): SlipSelection[] => {
      const pred = predictions.find(p => p.home === sel.home && p.away === sel.away && (!sel.date || p.date === sel.date));
      if (pred) { const own = selectionFromPrediction(pred); return own ? [own] : []; }
      if (!["1", "X", "2"].includes(sel.tip_code) || !sel.date) return [];
      return [{ home: sel.home, away: sel.away, date: sel.date, league: sel.league, market: "1x2",
                marketName: "Match Result", code: sel.tip_code, label: sel.tip_1x2 || sel.tip_code }];
    });
    slip.addMany(list);
    setAddedToSlip(true);
    slip.setOpen(true);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="bg-n-800 rounded-2xl rounded-tl-sm px-4 py-3 text-n-100 text-sm whitespace-pre-wrap leading-relaxed max-w-[85%]">
        {displayText}
      </div>

      {selections && (
        <button onClick={addToSlip} className="btn-primary self-start !px-4 !py-2 !text-xs">
          {addedToSlip ? <Check size={13} /> : <Ticket size={13} />}
          {addedToSlip ? "Added — open slip to book" : "Add to slip & book on SportyBet"}
        </button>
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
            "👋 Hey! I'm BetIQ's betting assistant.\n\nTell me what you want — e.g. *\"5 banker games this weekend\"* or *\"best 3-fold for today\"* — and I'll pick the games. Add them to your slip to get a SportyBet booking code.",
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
          messages: newMessages.slice(-20),
          predictions: predictions.slice(0, 100),
        }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        setMessages([...newMessages, {
          role: "assistant",
          content: res.status === 429 ? "You're sending messages quickly — give me a few seconds and try again."
            : res.status === 401 ? "Please sign in to use the assistant."
            : res.status === 402 ? "The assistant is part of Premium."
            : "Sorry, I'm having trouble right now. Please try again in a moment.",
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
      {/* Floating button — sits above the mobile bottom nav */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-20 lg:bottom-6 right-4 lg:right-6 z-50 w-14 h-14 bg-brand-400 hover:bg-brand-300 text-ink rounded-full shadow-pop flex items-center justify-center transition-all hover:scale-105 active:scale-95"
          title="BetIQ Assistant"
        >
          <MessageCircle size={24} />
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="fixed bottom-20 lg:bottom-6 right-4 lg:right-6 z-50 w-[360px] max-w-[calc(100vw-2rem)] flex flex-col card !rounded-2xl shadow-pop overflow-hidden animate-slide-up"
          style={{ height: minimised ? "auto" : "560px" }}
        >
          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-3 bg-surface-sunken border-b border-n-800 shrink-0">
            <img src="/logo.svg" alt="" className="w-7 h-7 rounded-lg" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-n-0 dark:text-white leading-none">BetIQ Assistant</p>
              <p className="text-[10px] text-brand-600 dark:text-brand-400 mt-0.5 font-medium">AI · SportyBet booking</p>
            </div>
            <button
              onClick={() => setMinimised(!minimised)}
              className="p-1 hover:bg-n-700/60 rounded-lg text-n-500 transition-colors"
            >
              <ChevronDown size={14} className={clsx("transition-transform", minimised && "rotate-180")} />
            </button>
            <button
              onClick={() => { setOpen(false); setMinimised(false); }}
              className="p-1 hover:bg-n-700/60 rounded-lg text-n-500 transition-colors"
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
                      <div className="bg-brand-400 text-ink font-medium rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm max-w-[80%] whitespace-pre-wrap">
                        {m.content}
                      </div>
                    </div>
                  ) : (
                    <AssistantBubble key={i} content={m.content} predictions={predictions} />
                  )
                )}

                {loading && (
                  <div className="flex gap-2 items-center text-n-500 text-sm">
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
                        className="chip chip-idle !text-xs"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}

                <div ref={bottomRef} />
              </div>

              {/* Input */}
              <div className="p-3 border-t border-n-800 bg-surface-sunken shrink-0">
                <div className="flex gap-2">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKey}
                    placeholder="Ask for picks…"
                    disabled={loading}
                    className="flex-1 bg-surface border border-n-800 text-n-0 placeholder:text-n-500 text-base sm:text-sm rounded-xl px-3 py-2 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-[border-color,box-shadow] duration-150 disabled:opacity-50"
                  />
                  <button
                    onClick={() => send()}
                    disabled={!input.trim() || loading}
                    className="bg-brand-400 hover:bg-brand-300 disabled:opacity-40 text-ink rounded-xl px-3 py-2 transition-colors"
                  >
                    <Send size={15} />
                  </button>
                </div>
                <p className="text-center text-[10px] text-n-500 mt-2">
                  Powered by BetIQ
                </p>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
