export type RiskLevel = "low" | "medium" | "high";
export type ActionStatus = "pending" | "completed" | "failed";
export type ConfirmStatus = "pending" | "approved" | "rejected";
export type ConsoleRole = "dev" | "admin" | "user";
export type UiRole = "developer" | "teamlead" | "user";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: ConsoleRole;
  ui_role: UiRole;
  team_id: string | null;
  team_role: string | null;
  tracker_login: string | null;
  default_board_id: string | null;
}

export interface Autonomy {
  auto_risk: RiskLevel[];
  confirm_risk: RiskLevel[];
  always_confirm_tools: string[];
}

export interface AgentListItem {
  name: string;
  description: string;
  enabled: boolean;
  has_spec: boolean;
  model: string;
  updated_at: string | null;
}

export interface AgentConfig {
  name: string;
  description: string;
  enabled: boolean;
  model: string;
  prompt: string;
  autonomy: Autonomy;
  spec_prompt: string;
  overlay: Record<string, unknown>;
  has_spec: boolean;
}

export interface ActionListItem {
  id: string;
  created_at: string;
  agent_name: string | null;
  tool_name: string;
  risk_level: RiskLevel;
  status: ActionStatus;
  trace_id: string | null;
  error: string | null;
}

export interface TraceStep {
  kind: string;
  ts?: string;
  content?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  result?: unknown;
  error?: string;
  confirm_id?: string;
  reason?: string;
}

export interface Trace {
  id: string;
  session_id: string;
  steps: TraceStep[];
  metadata_json: Record<string, unknown> | null;
  created_at: string;
}

export interface Confirm {
  id: string;
  action_id: string;
  prompt: string;
  status: ConfirmStatus;
  answer: string | null;
  created_at: string;
  responded_at: string | null;
}

export interface Feedback {
  id: string;
  action_id: string;
  user_id: string | null;
  rating: number;
  comment: string | null;
  created_at: string;
}

export interface ActionDetail {
  action: ActionListItem;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  trace: Trace | null;
  confirms: Confirm[];
  feedback: Feedback[];
}

export interface Contact {
  type: string;
  value: string;
  label?: string | null;
}

export interface Profile {
  user_id: string;
  display_name: string;
  ui_role: UiRole;
  title: string | null;
  bio: string | null;
  contacts: Contact[];
  avatar_url: string | null;
  is_self: boolean;
  // Owner-only fields (present when is_self).
  email?: string | null;
  team_role?: string | null;
  tracker_login?: string | null;
  private?: Record<string, unknown> | null;
}

export interface PatchProfileBody {
  title?: string | null;
  bio?: string | null;
  contacts?: Contact[];
  private?: Record<string, unknown>;
}

export interface BoardIssue {
  key: string;
  summary: string;
  status: string;
  status_key: string;
  deadline: string | null;
  overdue: boolean;
  updated_at: string | null;
}

export interface BoardColumn {
  status: string;
  issues: BoardIssue[];
}

export interface Board {
  available: boolean;
  queue: string | null;
  tracker_login: string | null;
  total: number;
  columns: BoardColumn[];
  note: string | null;
}

export interface Stats {
  available: boolean;
  window_days: number;
  tracker_login: string | null;
  counts: Record<string, number>;
  throughput: Array<{ date: string; closed: number }>;
  status_distribution: Array<{ status: string; count: number }>;
  lead_time: { count: number; avg_days: number | null; median_days: number | null };
  note: string | null;
}

export type SchedulePreset = "daily" | "weekdays" | "weekly" | "custom";

export interface ScheduleStruct {
  preset: SchedulePreset;
  time?: string;
  days?: number[];
}

export interface ScheduledJob {
  id: string;
  agent_name: string | null;
  name: string;
  cron_expr: string;
  schedule: ScheduleStruct;
  human: string;
  payload_type: string | null;
  enabled: boolean;
  run_count: number;
  max_runs: number | null;
  next_run: string | null;
  created_at: string;
}

export interface PatchScheduledJobBody {
  enabled?: boolean;
  schedule?: { preset: "daily" | "weekdays" | "weekly"; time: string; days?: number[] };
}

export interface TeamMember {
  user_id: string;
  display_name: string;
  tracker_login: string | null;
  role: string;
  avatar_url: string | null;
}

export interface HealthBreakdown {
  key: string;
  label: string;
  score: number;
  weight: number;
}

export interface TeamHealth {
  available: boolean;
  window_days: number;
  health_index: number | null;
  breakdown: HealthBreakdown[];
  drags: string[];
  totals: Record<string, number>;
  throughput: Array<{ date: string; closed: number }>;
  members: Array<{
    user_id: string | null;
    display_name: string | null;
    tracker_login: string | null;
    assigned: number;
    in_progress: number;
    resolved: number;
    overdue: number;
  }>;
  note: string | null;
}

export interface PetSpecies {
  id: string;
  name: string;
  rarity: "common" | "uncommon" | "rare" | "epic" | "legendary";
  rarity_rank: number;
  desc: string;
}

export interface Pet {
  available: boolean;
  level: number;
  xp: number;
  xp_into_level: number;
  xp_for_next: number;
  progress: number;
  mood: number;
  tier: number;
  tier_name: string;
  species: PetSpecies | null;
  stats: Record<string, number>;
  stat_labels: Record<string, string>;
  coins: number;
  equipped: Record<string, string>;
  owner_name: string | null;
  note: string | null;
}

export interface ShopItem {
  id: string;
  name: string;
  slot: string;
  rarity: "common" | "uncommon" | "rare" | "epic" | "legendary";
  price: number;
  owned: boolean;
  equipped: boolean;
  affordable: boolean;
}

export interface Shop {
  coins: number;
  earned: number;
  spent: number;
  equipped: Record<string, string>;
  items: ShopItem[];
}

export interface AgentTool {
  name: string;
  description: string;
  risk: RiskLevel;
  enabled: boolean;
  confirm: boolean | null;
}

export interface UserSummary {
  user_id: string;
  display_name: string;
  email: string;
  role: ConsoleRole;
  ui_role: UiRole;
  team_role: string | null;
  tracker_login: string | null;
  avatar_url: string | null;
}

export interface PendingConfirm {
  confirm_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk: RiskLevel;
  prompt: string;
}

export interface ChatResponse {
  reply: string | null;
  pending_confirm: PendingConfirm | null;
  session_id: string;
  steps: TraceStep[];
}

export interface BattleCombatant {
  rank: number | null;
  user_id: string | null;
  name: string;
  species_id: string | null;
  species_name: string;
  level: number;
  power: number;
  equipped: Record<string, string>;
}

export interface BattleRoyale {
  team_name: string;
  ranked: BattleCombatant[];
  winner: BattleCombatant | null;
  status_frames: string[];
  image_base64: string;
}

export interface Duel {
  winner: BattleCombatant;
  loser: BattleCombatant;
  log: string[];
  status_frames: string[];
  image_base64: string;
}

export interface DuelRow {
  user_id: string;
  name: string;
  wins: number;
  losses: number;
  battles: number;
}

const API_BASE = import.meta.env.VITE_CONSOLE_API_URL ?? "/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(localizeError(response.status, text || response.statusText));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  requestLoginCode: (identifier: string) =>
    request<{ challenge_id: string; expires_in_seconds: number }>("/auth/code/request", {
      method: "POST",
      body: JSON.stringify({ identifier }),
    }),
  verifyLoginCode: (challengeId: string, code: string) =>
    request<{ user: User }>("/auth/code/verify", {
      method: "POST",
      body: JSON.stringify({ challenge_id: challengeId, code }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),
  myProfile: () => request<Profile>("/me/profile"),
  patchMyProfile: (body: PatchProfileBody) =>
    request<Profile>("/me/profile", { method: "PATCH", body: JSON.stringify(body) }),
  userProfile: (userId: string) => request<Profile>(`/users/${userId}/profile`),
  myBoard: () => request<Board>("/me/board"),
  myStats: (window = 14) => request<Stats>(`/me/stats?window=${window}`),
  myPet: () => request<Pet>("/me/pet"),
  userPet: (userId: string) => request<Pet>(`/users/${userId}/pet`),
  petGrantXp: (body: { amount?: number; level?: number }) =>
    request<Pet>("/me/pet/grant-xp", { method: "POST", body: JSON.stringify(body) }),
  petSetSpecies: (id: string) =>
    request<Pet>("/me/pet/set-species", {
      method: "POST",
      body: JSON.stringify({ id, name: "", rarity: "common" }),
    }),
  petReset: () => request<Pet>("/me/pet/reset", { method: "POST" }),
  petShop: () => request<Shop>("/me/pet/shop"),
  petBuy: (itemId: string) =>
    request<Pet>("/me/pet/buy", { method: "POST", body: JSON.stringify({ item_id: itemId }) }),
  petEquip: (slot: string, itemId: string | null) =>
    request<Pet>("/me/pet/equip", { method: "PUT", body: JSON.stringify({ slot, item_id: itemId }) }),
  scheduledJobs: () => request<ScheduledJob[]>("/scheduled-jobs"),
  patchScheduledJob: (id: string, body: PatchScheduledJobBody) =>
    request<ScheduledJob>(`/scheduled-jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  teamMembers: (teamId: string) => request<TeamMember[]>(`/teams/${teamId}/members`),
  teamHealth: (teamId: string, window = 14) =>
    request<TeamHealth>(`/teams/${teamId}/health?window=${window}`),
  teamAudit: (teamId: string, window = 14) =>
    request<ChatResponse>(`/teams/${teamId}/audit?window=${window}`, { method: "POST" }),
  uploadAvatar: async (file: File): Promise<Profile> => {
    const response = await fetch(`${API_BASE}/me/avatar`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": file.type },
      body: file,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(localizeError(response.status, text || response.statusText));
    }
    return (await response.json()) as Profile;
  },
  avatarSrc: (path: string) => `${API_BASE}${path}`,
  agents: () => request<AgentListItem[]>("/agents"),
  agentConfig: (name: string) => request<AgentConfig>(`/agents/${name}/config`),
  patchAgentSpec: (name: string, body: { prompt?: string; model?: string }) =>
    request<AgentConfig>(`/agents/${name}/spec`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  patchAgentOverlay: (name: string, body: { enabled?: boolean; autonomy?: Autonomy }) =>
    request<AgentConfig>(`/agents/${name}/overlay`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  agentTools: (name: string) => request<AgentTool[]>(`/agents/${name}/tools`),
  patchAgentTools: (name: string, tools: Array<{ name: string; enabled: boolean; confirm: boolean | null }>) =>
    request<AgentTool[]>(`/agents/${name}/tools`, {
      method: "PATCH",
      body: JSON.stringify({ tools }),
    }),
  users: () => request<UserSummary[]>("/users"),
  actions: (params: URLSearchParams) => request<ActionListItem[]>(`/actions?${params}`),
  actionDetail: (id: string) => request<ActionDetail>(`/actions/${id}`),
  feedback: (id: string, body: { rating: number; comment?: string }) =>
    request<Feedback>(`/actions/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  confirms: (status = "pending") => request<Confirm[]>(`/confirms?status=${status}`),
  decideConfirm: (id: string, approved: boolean) =>
    request<ChatResponse>(`/confirms/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  playgroundChat: (agent: string, message: string, session_id: string) =>
    request<ChatResponse>(`/playground/${agent}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, session_id }),
    }),
  // Telegram Mini App
  authTelegramWebApp: (initData: string) =>
    request<{ user: User }>("/auth/telegram/webapp", {
      method: "POST",
      body: JSON.stringify({ init_data: initData }),
    }),
  battleTeam: () => request<BattleRoyale>("/me/battle/team", { method: "POST" }),
  battleLeaderboard: () => request<BattleCombatant[]>("/me/battle/leaderboard"),
  battleDuel: (opponentId: string) =>
    request<Duel>(`/me/battle/duel/${opponentId}`, { method: "POST" }),
  duelLeaderboard: () => request<DuelRow[]>("/me/battle/duels"),
};

function localizeError(status: number, raw: string): string {
  if (status === 401) return "Неверные данные или код подтверждения";
  if (status === 403) return "Недостаточно прав";
  if (status === 404) return "Не найдено";
  if (status >= 500) return "Внутренняя ошибка сервера";

  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail ?? raw;
  } catch {
    return raw;
  }
}
