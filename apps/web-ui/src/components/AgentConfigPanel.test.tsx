import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentConfig, Autonomy } from "../lib/api";
import { AgentConfigPanel } from "./AgentConfigPanel";

const baseAutonomy: Autonomy = {
  auto_risk: ["low"],
  confirm_risk: ["medium", "high"],
  always_confirm_tools: [],
};

function makeConfig(autonomy: Autonomy = baseAutonomy): AgentConfig {
  return {
    name: "pm_agent",
    description: "runtime-агент",
    enabled: true,
    model: "gpt-oss-120b",
    prompt: "Ты PM-агент.",
    autonomy,
    spec_prompt: "Ты PM-агент.",
    overlay: {},
    has_spec: true,
  };
}

describe("AgentConfigPanel", () => {
  it("removes a risk from confirmation when it is selected for auto execution", () => {
    const onSaveOverlay = vi.fn();
    render(<AgentConfigPanel config={makeConfig()} onSaveSpec={vi.fn()} onSaveOverlay={onSaveOverlay} />);

    fireEvent.click(
      within(screen.getByRole("group", { name: "Автовыполнение" })).getByRole("button", {
        name: "Средний",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить оверлей" }));

    expect(onSaveOverlay).toHaveBeenCalledWith({
      enabled: true,
      autonomy: {
        auto_risk: ["low", "medium"],
        confirm_risk: ["high"],
        always_confirm_tools: [],
      },
    });
  });

  it("removes a risk from auto execution when it is selected for confirmation", () => {
    const onSaveOverlay = vi.fn();
    const config = makeConfig({
      auto_risk: ["low", "medium"],
      confirm_risk: ["high"],
      always_confirm_tools: [],
    });
    render(<AgentConfigPanel config={config} onSaveSpec={vi.fn()} onSaveOverlay={onSaveOverlay} />);

    fireEvent.click(
      within(screen.getByRole("group", { name: "Подтверждение" })).getByRole("button", {
        name: "Средний",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить оверлей" }));

    expect(onSaveOverlay).toHaveBeenCalledWith({
      enabled: true,
      autonomy: {
        auto_risk: ["low"],
        confirm_risk: ["high", "medium"],
        always_confirm_tools: [],
      },
    });
  });
});
