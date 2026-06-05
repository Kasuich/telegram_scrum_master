import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Send, X } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { RiskBadge } from "../components/Badge";
import { TraceTimeline } from "../components/TraceTimeline";
import { api, type ChatResponse } from "../lib/api";

export function PlaygroundPage() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const [agent, setAgent] = useState("");
  const [message, setMessage] = useState("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [history, setHistory] = useState<Array<{ role: "user" | "assistant"; text: string }>>([]);
  const [lastResult, setLastResult] = useState<ChatResponse | null>(null);

  const activeAgent = agent || agents.data?.[0]?.name || "";
  const chat = useMutation({
    mutationFn: () => api.playgroundChat(activeAgent, message, sessionId),
    onSuccess: (result) => {
      setHistory((items) => [
        ...items,
        { role: "user", text: message },
        ...(result.reply ? [{ role: "assistant" as const, text: result.reply }] : []),
      ]);
      setLastResult(result);
      setMessage("");
    },
  });
  const decide = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) => api.decideConfirm(id, approved),
    onSuccess: (result) => {
      setLastResult(result);
      if (result.reply) {
        setHistory((items) => [...items, { role: "assistant", text: result.reply ?? "" }]);
      }
    },
  });

  const pending = lastResult?.pending_confirm;
  const steps = useMemo(() => lastResult?.steps ?? [], [lastResult]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (message.trim()) {
      chat.mutate();
    }
  }

  return (
    <div className="playground-grid">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Песочница</h2>
            <p>{activeAgent || "агент"}</p>
          </div>
          <select value={activeAgent} onChange={(event) => setAgent(event.target.value)}>
            {(agents.data ?? []).map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        <div className="chat-log">
          {history.map((item, index) => (
            <div className={`chat-bubble ${item.role}`} key={`${item.role}-${index}`}>
              {item.text}
            </div>
          ))}
        </div>
        {pending ? (
          <div className="confirm-panel">
            <div className="flex items-center gap-2">
              <RiskBadge risk={pending.risk} />
              <span className="font-medium">{pending.tool_name}</span>
            </div>
            <pre className="json-block mt-2">{JSON.stringify(pending.tool_args, null, 2)}</pre>
            <div className="mt-3 flex gap-2">
              <button className="primary-button" onClick={() => decide.mutate({ id: pending.confirm_id, approved: true })}>
                <Check className="h-4 w-4" />
                Одобрить
              </button>
              <button className="secondary-button" onClick={() => decide.mutate({ id: pending.confirm_id, approved: false })}>
                <X className="h-4 w-4" />
                Отклонить
              </button>
            </div>
          </div>
        ) : null}
        <form className="chat-form" onSubmit={submit}>
          <input value={message} onChange={(event) => setMessage(event.target.value)} />
          <button className="primary-button" disabled={!activeAgent || !message.trim()} type="submit">
            <Send className="h-4 w-4" />
            Отправить
          </button>
        </form>
      </section>
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Шаги</h2>
            <p>{steps.length} записей</p>
          </div>
        </div>
        <TraceTimeline steps={steps} />
      </section>
    </div>
  );
}
