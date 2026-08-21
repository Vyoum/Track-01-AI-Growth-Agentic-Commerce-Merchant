import { useState } from "react";

const STARTER = [
  {
    role: "assistant",
    text: "Hi — I'm the checkout agent shell. Backend chat endpoint isn't wired yet. Try typing a message to see the UI flow.",
  },
];

export default function ChatWindow({ onProposalHint, backendMessage }) {
  const [messages, setMessages] = useState(STARTER);
  const [input, setInput] = useState("");

  function send() {
    const text = input.trim();
    if (!text) return;

    const next = [...messages, { role: "user", text }];
    next.push({
      role: "assistant",
      text:
        backendMessage ||
        "Scaffold only — agent endpoint arrives in a later pointer. Your message was received locally.",
    });
    setMessages(next);
    setInput("");
    onProposalHint?.({
      status: "scaffold",
      note: "No real proposal yet",
      lastUserMessage: text,
    });
  }

  return (
    <section className="panel chat">
      <h2>Chat</h2>
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.text}
          </div>
        ))}
      </div>
      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder='e.g. "Order my usual, under ₹800"'
        />
        <button type="button" onClick={send}>
          Send
        </button>
      </div>
    </section>
  );
}
