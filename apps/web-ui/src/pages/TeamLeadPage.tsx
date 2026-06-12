import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CalendarClock, ClipboardCheck, Save, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AuditReport } from "../components/AuditReport";
import { api, type HealthBreakdown, type ScheduledJob, type TeamHealth } from "../lib/api";

const WEEKDAYS = [
  { iso: 1, label: "Пн" },
  { iso: 2, label: "Вт" },
  { iso: 3, label: "Ср" },
  { iso: 4, label: "Чт" },
  { iso: 5, label: "Пт" },
  { iso: 6, label: "Сб" },
  { iso: 7, label: "Вс" },
];

type EditablePreset = "hourly" | "daily" | "weekdays" | "weekly";

const PRESET_OPTIONS: { value: EditablePreset; label: string }[] = [
  { value: "hourly", label: "Каждый час" },
  { value: "daily", label: "Ежедневно" },
  { value: "weekdays", label: "Будни" },
  { value: "weekly", label: "По дням" },
];

function healthColor(value: number): string {
  if (value >= 75) return "#0d9488";
  if (value >= 50) return "#f59e0b";
  return "#ef4444";
}

export function TeamLeadPage({ teamId }: { teamId: string | null }) {
  if (!teamId) {
    return (
      <div className="page-grid">
        <section className="surface wide">
          <div className="empty">Команда не определена</div>
        </section>
      </div>
    );
  }
  return (
    <div className="board-page">
      <HealthDashboard teamId={teamId} />
      <CronSection />
      <TeamRoster teamId={teamId} />
    </div>
  );
}

function HealthDashboard({ teamId }: { teamId: string }) {
  const health = useQuery({ queryKey: ["team-health", teamId, 14], queryFn: () => api.teamHealth(teamId, 14) });
  const [auditKey, setAuditKey] = useState<number | null>(null);

  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Здоровье команды</h2>
          <p>{health.data?.available ? `за ${health.data.window_days} дней` : "нет данных"}</p>
        </div>
        <button className="secondary-button" onClick={() => setAuditKey(Date.now())} title="Аудит доски">
          <ClipboardCheck className="h-4 w-4" />
          Аудит
        </button>
      </div>
      {auditKey !== null ? (
        <AuditReport key={auditKey} teamId={teamId} onClose={() => setAuditKey(null)} />
      ) : null}
      {health.data?.available ? <HealthView health={health.data} /> : (
        <div className="empty">{health.data?.note ?? "Загрузка"}</div>
      )}
    </section>
  );
}

function HealthView({ health }: { health: TeamHealth }) {
  const index = health.health_index ?? 0;
  const totals = [
    { label: "Участники", value: health.totals.members ?? 0 },
    { label: "Открыто", value: health.totals.open ?? 0 },
    { label: "В работе", value: health.totals.in_progress ?? 0 },
    { label: "Закрыто", value: health.totals.resolved ?? 0 },
    { label: "Просрочено", value: health.totals.overdue ?? 0, danger: true },
  ];

  return (
    <div className="grid gap-5">
      <div className="health-top">
        <HealthGauge value={index} />
        <div className="health-breakdown">
          {health.breakdown.map((item) => (
            <BreakdownBar item={item} key={item.key} />
          ))}
          {health.drags.length ? (
            <p className="health-drags">Тянет вниз: {health.drags.join(", ")}</p>
          ) : (
            <p className="health-drags ok">Всё в норме</p>
          )}
        </div>
      </div>

      <div className="stat-cards">
        {totals.map((card) => (
          <div className={`stat-card ${card.danger && card.value > 0 ? "danger" : ""}`} key={card.label}>
            <span className="stat-value">{card.value}</span>
            <span className="stat-label">{card.label}</span>
          </div>
        ))}
      </div>

      <div className="chart-box">
        <div className="label">Закрытия по дням (команда)</div>
        <ResponsiveContainer height={200} width="100%">
          <BarChart data={health.throughput.map((row) => ({ ...row, day: row.date.slice(5) }))}>
            <XAxis dataKey="day" fontSize={11} stroke="#94a3b8" />
            <YAxis allowDecimals={false} fontSize={11} stroke="#94a3b8" width={24} />
            <Tooltip />
            <Bar dataKey="closed" fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Участник</th>
              <th>Открыто</th>
              <th>В работе</th>
              <th>Закрыто</th>
              <th>Просрочено</th>
            </tr>
          </thead>
          <tbody>
            {health.members.map((member) => (
              <tr key={member.user_id ?? member.tracker_login}>
                <td>{member.display_name ?? member.tracker_login}</td>
                <td>{member.assigned}</td>
                <td>{member.in_progress}</td>
                <td>{member.resolved}</td>
                <td className={member.overdue > 0 ? "text-rose" : ""}>{member.overdue}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HealthGauge({ value }: { value: number }) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - value / 100);
  const color = healthColor(value);
  return (
    <div className="health-gauge">
      <svg height="130" viewBox="0 0 130 130" width="130">
        <circle cx="65" cy="65" fill="none" r={radius} stroke="#e5e9f0" strokeWidth="12" />
        <circle
          cx="65"
          cy="65"
          fill="none"
          r={radius}
          stroke={color}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          strokeWidth="12"
          transform="rotate(-90 65 65)"
        />
        <text dominantBaseline="central" fontSize="30" fontWeight="700" textAnchor="middle" x="65" y="62" fill={color}>
          {value}
        </text>
        <text dominantBaseline="central" fontSize="12" textAnchor="middle" x="65" y="84" fill="#64748b">
          из 100
        </text>
      </svg>
      <span className="health-gauge-label">Индекс здоровья</span>
    </div>
  );
}

function BreakdownBar({ item }: { item: HealthBreakdown }) {
  return (
    <div className="breakdown-row">
      <span className="breakdown-label">{item.label}</span>
      <div className="breakdown-track">
        <div className="breakdown-fill" style={{ width: `${item.score}%`, background: healthColor(item.score) }} />
      </div>
      <span className="breakdown-score">{item.score}</span>
    </div>
  );
}

function TeamRoster({ teamId }: { teamId: string }) {
  const members = useQuery({ queryKey: ["team-members", teamId], queryFn: () => api.teamMembers(teamId) });
  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Моя команда</h2>
          <p>{members.data?.length ?? 0} участников</p>
        </div>
        <Users className="h-5 w-5 text-muted" />
      </div>
      <div className="list">
        {(members.data ?? []).map((member) => (
          <Link className="list-row" key={member.user_id} to={`/users/${member.user_id}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{member.display_name}</span>
              <span className="mono-chip">{member.role === "lead" ? "тимлид" : "участник"}</span>
            </div>
            {member.tracker_login ? (
              <span className="text-xs text-muted">{member.tracker_login}</span>
            ) : null}
          </Link>
        ))}
      </div>
    </section>
  );
}

function CronSection() {
  const jobs = useQuery({ queryKey: ["scheduled-jobs"], queryFn: api.scheduledJobs });
  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Регулярные задачи</h2>
          <p>{jobs.data?.length ?? 0} задач</p>
        </div>
        <CalendarClock className="h-5 w-5 text-muted" />
      </div>
      <div className="grid gap-3">
        {(jobs.data ?? []).map((job) => (
          <CronCard job={job} key={job.id} />
        ))}
        {jobs.data && !jobs.data.length ? <div className="empty">Нет регулярных задач</div> : null}
      </div>
    </section>
  );
}

function CronCard({ job }: { job: ScheduledJob }) {
  const queryClient = useQueryClient();
  const isCustom = job.schedule.preset === "custom";
  const [editing, setEditing] = useState(false);
  const [preset, setPreset] = useState<EditablePreset>(
    isCustom ? "daily" : (job.schedule.preset as EditablePreset),
  );
  const [time, setTime] = useState(job.schedule.time ?? "09:00");
  const [days, setDays] = useState<number[]>(job.schedule.days ?? [1]);

  useEffect(() => {
    if (!isCustom) setPreset(job.schedule.preset as EditablePreset);
    setTime(job.schedule.time ?? "09:00");
    setDays(job.schedule.days ?? [1]);
  }, [job, isCustom]);

  // For the hourly preset only the minute-of-hour matters; encode it as "00:MM".
  const minute = Number(time.split(":")[1] ?? "0") || 0;
  const setMinute = (value: number) => {
    const clamped = Math.max(0, Math.min(59, Number.isFinite(value) ? value : 0));
    setTime(`00:${String(clamped).padStart(2, "0")}`);
  };

  const onSuccess = (updated: ScheduledJob) => {
    queryClient.setQueryData<ScheduledJob[]>(["scheduled-jobs"], (prev) =>
      (prev ?? []).map((item) => (item.id === updated.id ? updated : item)),
    );
  };

  const toggle = useMutation({
    mutationFn: () => api.patchScheduledJob(job.id, { enabled: !job.enabled }),
    onSuccess,
  });
  const save = useMutation({
    mutationFn: () =>
      api.patchScheduledJob(job.id, {
        schedule: { preset, time, days: preset === "weekly" ? days : undefined },
      }),
    onSuccess: (updated) => {
      onSuccess(updated);
      setEditing(false);
    },
  });

  return (
    <div className="cron-card">
      <div className="cron-head">
        <div>
          <div className="font-medium">{job.name}</div>
          <div className="text-xs text-muted">
            {job.human}
            {job.agent_name ? ` · ${job.agent_name}` : ""}
          </div>
        </div>
        <label className="switch">
          <input checked={job.enabled} type="checkbox" onChange={() => toggle.mutate()} />
          <span>{job.enabled ? "вкл" : "выкл"}</span>
        </label>
      </div>

      {editing ? (
        <div className="cron-edit">
          {isCustom ? (
            <div className="cron-warning">
              ⚠️ Текущее расписание задано вручную (<code>{job.cron_expr}</code>) и не
              представимо пресетами. Сохранение <strong>заменит</strong> его выбранным ниже —
              отмените, если не хотите менять.
            </div>
          ) : null}
          <div className="segmented">
            {PRESET_OPTIONS.map(({ value, label }) => (
              <button className={preset === value ? "active" : ""} key={value} onClick={() => setPreset(value)}>
                {label}
              </button>
            ))}
          </div>
          {preset === "hourly" ? (
            <label className="cron-minute">
              Минута часа
              <input
                max={59}
                min={0}
                type="number"
                value={minute}
                onChange={(event) => setMinute(Number(event.target.value))}
              />
            </label>
          ) : (
            <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
          )}
          {preset === "weekly" ? (
            <div className="weekday-chips">
              {WEEKDAYS.map((day) => (
                <button
                  className={days.includes(day.iso) ? "active" : ""}
                  key={day.iso}
                  onClick={() =>
                    setDays((current) =>
                      current.includes(day.iso)
                        ? current.filter((d) => d !== day.iso)
                        : [...current, day.iso],
                    )
                  }
                >
                  {day.label}
                </button>
              ))}
            </div>
          ) : null}
          <div className="flex gap-2">
            <button
              className="primary-button"
              disabled={save.isPending || (preset === "weekly" && !days.length)}
              onClick={() => save.mutate()}
            >
              <Save className="h-4 w-4" />
              Сохранить
            </button>
            <button className="secondary-button" onClick={() => setEditing(false)}>
              Отмена
            </button>
          </div>
        </div>
      ) : (
        <button className="link-button" onClick={() => setEditing(true)}>
          <Activity className="h-3.5 w-3.5" />
          Изменить расписание
        </button>
      )}
    </div>
  );
}
