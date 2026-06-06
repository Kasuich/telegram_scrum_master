import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import type { ColumnDef } from "@tanstack/react-table";
import { Check, RotateCw, ShieldAlert, Star, X } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { RiskBadge, StatusBadge } from "../components/Badge";
import { api, type ActionListItem, type ActionStatus, type RiskLevel } from "../lib/api";
import { formatDate, shortId } from "../lib/format";

export function AdminPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ActionStatus | "">("");
  const [risk, setRisk] = useState<RiskLevel | "">("");
  const [agent, setAgent] = useState("");
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const params = useMemo(() => {
    const query = new URLSearchParams({ limit: "80" });
    if (status) query.set("status", status);
    if (risk) query.set("risk", risk);
    if (agent) query.set("agent", agent);
    return query;
  }, [agent, risk, status]);

  const actions = useQuery({ queryKey: ["actions", params.toString()], queryFn: () => api.actions(params) });
  const confirms = useQuery({ queryKey: ["confirms", "pending"], queryFn: () => api.confirms("pending") });
  const detail = useQuery({
    queryKey: ["action-detail", selectedAction],
    queryFn: () => api.actionDetail(selectedAction as string),
    enabled: Boolean(selectedAction),
  });
  const decide = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) => api.decideConfirm(id, approved),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["confirms"] });
      void queryClient.invalidateQueries({ queryKey: ["actions"] });
    },
  });
  const feedback = useMutation({
    mutationFn: ({ id, rating, comment }: { id: string; rating: number; comment?: string }) =>
      api.feedback(id, { rating, comment }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["action-detail", selectedAction] });
    },
  });

  const columns = useMemo<ColumnDef<ActionListItem>[]>(
    () => [
      {
        header: "Действие",
        accessorKey: "tool_name",
        cell: ({ row }) => (
          <button className="table-link" onClick={() => setSelectedAction(row.original.id)}>
            {row.original.tool_name}
          </button>
        ),
      },
      { header: "Агент", accessorKey: "agent_name", cell: ({ row }) => row.original.agent_name ?? "-" },
      { header: "Риск", accessorKey: "risk_level", cell: ({ row }) => <RiskBadge risk={row.original.risk_level} /> },
      { header: "Статус", accessorKey: "status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { header: "Создано", accessorKey: "created_at", cell: ({ row }) => formatDate(row.original.created_at) },
    ],
    [],
  );
  const table = useReactTable({
    data: actions.data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="page-grid admin-grid">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Действия</h2>
            <p>{actions.data?.length ?? 0} строк</p>
          </div>
          <button className="icon-button" title="Обновить" aria-label="Обновить" onClick={() => void actions.refetch()}>
            <RotateCw className="h-4 w-4" />
          </button>
        </div>
        <div className="filters">
          <select value={status} onChange={(event) => setStatus(event.target.value as ActionStatus | "")}>
            <option value="">Любой статус</option>
            <option value="pending">Ожидает</option>
            <option value="completed">Готово</option>
            <option value="failed">Ошибка</option>
          </select>
          <select value={risk} onChange={(event) => setRisk(event.target.value as RiskLevel | "")}>
            <option value="">Любой риск</option>
            <option value="low">Низкий</option>
            <option value="medium">Средний</option>
            <option value="high">Высокий</option>
          </select>
          <input placeholder="агент" value={agent} onChange={(event) => setAgent(event.target.value)} />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Подтверждения</h2>
            <p>{confirms.data?.length ?? 0} ожидает</p>
          </div>
          <ShieldAlert className="h-5 w-5 text-amber" />
        </div>
        <div className="list">
          {(confirms.data ?? []).map((confirm) => (
            <div className="list-row" key={confirm.id}>
              <div className="flex items-center justify-between gap-2">
                <span className="mono-chip">{shortId(confirm.id)}</span>
                <span className="text-xs text-muted">{formatDate(confirm.created_at)}</span>
              </div>
              <p className="mt-2 text-sm text-ink">{confirm.prompt}</p>
              <div className="mt-3 flex gap-2">
                <button className="icon-button" title="Одобрить" aria-label="Одобрить" onClick={() => decide.mutate({ id: confirm.id, approved: true })}>
                  <Check className="h-4 w-4 text-teal" />
                </button>
                <button className="icon-button" title="Отклонить" aria-label="Отклонить" onClick={() => decide.mutate({ id: confirm.id, approved: false })}>
                  <X className="h-4 w-4 text-rose" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Обратная связь</h2>
            <p>{selectedAction ? shortId(selectedAction) : "не выбрано"}</p>
          </div>
          <Star className="h-5 w-5 text-amber" />
        </div>
        {selectedAction && detail.data ? (
          <FeedbackForm
            existing={detail.data.feedback.length}
            onSubmit={(rating, comment) => feedback.mutate({ id: selectedAction, rating, comment })}
          />
        ) : (
          <div className="empty">Выберите действие</div>
        )}
      </section>
    </div>
  );
}

function FeedbackForm({
  existing,
  onSubmit,
}: {
  existing: number;
  onSubmit: (rating: number, comment?: string) => void;
}) {
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(rating, comment || undefined);
  }

  return (
    <form className="grid gap-4 md:grid-cols-[160px_1fr_auto]" onSubmit={submit}>
      <label className="field">
        <span>Оценка</span>
        <input min={1} max={5} type="number" value={rating} onChange={(event) => setRating(Number(event.target.value))} />
      </label>
      <label className="field">
        <span>Комментарий</span>
        <input value={comment} onChange={(event) => setComment(event.target.value)} />
      </label>
      <button className="primary-button self-end" type="submit">
        <Star className="h-4 w-4" />
        Сохранить
      </button>
      <div className="text-xs text-muted md:col-span-3">Сохранено: {existing}</div>
    </form>
  );
}
