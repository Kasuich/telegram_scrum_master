import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, LayoutGrid } from "lucide-react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, type BoardColumn, type Stats } from "../lib/api";

const PIE_COLORS = ["#0d9488", "#6366f1", "#f59e0b", "#ef4444", "#64748b", "#0ea5e9"];

function shortDay(iso: string): string {
  return iso.slice(5); // YYYY-MM-DD -> MM-DD
}

export function BoardPage() {
  const board = useQuery({ queryKey: ["my-board"], queryFn: api.myBoard });
  const stats = useQuery({ queryKey: ["my-stats", 14], queryFn: () => api.myStats(14) });

  const unavailable = board.data && !board.data.available ? board.data.note : null;

  return (
    <div className="board-page">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Моя статистика</h2>
            <p>{stats.data?.available ? `за ${stats.data.window_days} дней` : "нет данных"}</p>
          </div>
          <LayoutGrid className="h-5 w-5 text-muted" />
        </div>
        {stats.data?.available ? <StatsView stats={stats.data} /> : (
          <div className="empty">{stats.data?.note ?? "Загрузка"}</div>
        )}
      </section>

      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Моя доска</h2>
            <p>
              {board.data?.available
                ? `${board.data.total} задач${board.data.queue ? ` · ${board.data.queue}` : ""}`
                : "нет данных"}
            </p>
          </div>
        </div>
        {unavailable ? (
          <div className="empty">{unavailable}</div>
        ) : board.data?.available ? (
          <BoardColumns columns={board.data.columns} />
        ) : (
          <div className="empty">Загрузка</div>
        )}
      </section>
    </div>
  );
}

function StatsView({ stats }: { stats: Stats }) {
  const cards = [
    { label: "Назначено", value: stats.counts.assigned ?? 0 },
    { label: "В работе", value: stats.counts.in_progress ?? 0 },
    { label: "Закрыто", value: stats.counts.resolved ?? 0 },
    { label: "Просрочено", value: stats.counts.overdue ?? 0, danger: true },
  ];

  return (
    <div className="grid gap-4">
      <div className="stat-cards">
        {cards.map((card) => (
          <div className={`stat-card ${card.danger && card.value > 0 ? "danger" : ""}`} key={card.label}>
            <span className="stat-value">{card.value}</span>
            <span className="stat-label">{card.label}</span>
          </div>
        ))}
      </div>

      <div className="chart-grid">
        <div className="chart-box">
          <div className="label">Закрытия по дням</div>
          <ResponsiveContainer height={200} width="100%">
            <BarChart data={stats.throughput.map((row) => ({ ...row, day: shortDay(row.date) }))}>
              <XAxis dataKey="day" fontSize={11} stroke="#94a3b8" />
              <YAxis allowDecimals={false} fontSize={11} stroke="#94a3b8" width={24} />
              <Tooltip />
              <Bar dataKey="closed" fill="#0d9488" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <div className="label">Открытые задачи по статусам</div>
          {stats.status_distribution.length ? (
            <ResponsiveContainer height={200} width="100%">
              <PieChart>
                <Pie
                  data={stats.status_distribution}
                  dataKey="count"
                  nameKey="status"
                  outerRadius={80}
                  label={({ name, value }: { name?: string; value?: number }) => `${name}: ${value}`}
                >
                  {stats.status_distribution.map((_, index) => (
                    <Cell fill={PIE_COLORS[index % PIE_COLORS.length]} key={index} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty">Нет открытых задач</div>
          )}
        </div>
      </div>

      <div className="lead-time">
        Время выполнения (lead time):{" "}
        <strong>{stats.lead_time.median_days ?? "—"}</strong> дн. медиана ·{" "}
        <strong>{stats.lead_time.avg_days ?? "—"}</strong> дн. среднее
        {stats.lead_time.count ? ` (по ${stats.lead_time.count} задачам)` : ""}
      </div>
    </div>
  );
}

function BoardColumns({ columns }: { columns: BoardColumn[] }) {
  if (!columns.length) return <div className="empty">Нет открытых задач</div>;
  return (
    <div className="board-columns">
      {columns.map((column) => (
        <div className="board-column" key={column.status}>
          <div className="board-column-head">
            <span>{column.status}</span>
            <span className="mono-chip">{column.issues.length}</span>
          </div>
          <div className="board-column-body">
            {column.issues.map((issue) => (
              <div className={`board-card ${issue.overdue ? "overdue" : ""}`} key={issue.key}>
                <div className="flex items-center gap-2">
                  <span className="mono-chip">{issue.key}</span>
                  {issue.overdue ? <AlertTriangle className="h-3.5 w-3.5 text-rose" /> : null}
                </div>
                <div className="board-card-summary">{issue.summary}</div>
                {issue.deadline ? (
                  <div className={`board-card-deadline ${issue.overdue ? "text-rose" : "text-muted"}`}>
                    дедлайн: {issue.deadline}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
