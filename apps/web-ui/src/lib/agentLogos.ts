// Maps an agent name to its pixel-art logo (served from /public/agents).
// Source assets: docs/agents/png. Used in the console (DevPage/Playground) and the
// Telegram Mini App so agents have a consistent visual identity.

const LOGOS: Record<string, string> = {
  pm_agent: "/agents/pm_agent.png",
  audit_agent: "/agents/board_audit_agent.png",
  board_audit_agent: "/agents/board_audit_agent.png",
  meeting_summarizer: "/agents/meeting_summarizer.png",
  shturm: "/agents/shturm.png",
};

/** Logo URL for an agent name, or a sensible default. */
export function agentLogo(name: string | null | undefined): string {
  if (!name) return "/agents/pm_agent.png";
  const key = name.toLowerCase();
  return LOGOS[key] ?? "/agents/pm_agent.png";
}

export function hasAgentLogo(name: string | null | undefined): boolean {
  return Boolean(name && name.toLowerCase() in LOGOS);
}
