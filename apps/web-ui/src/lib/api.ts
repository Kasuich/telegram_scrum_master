export type RiskLevel = "low" | "medium" | "high";
export type ActionStatus = "pending" | "completed" | "failed";
export type ConfirmStatus = "pending" | "approved" | "rejected";
export type ConsoleRole = "dev" | "admin" | "user";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: ConsoleRole;
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
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),
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
};

function localizeError(status: number, raw: string): string {
  if (status === 401) return "Неверная почта или пароль";
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
