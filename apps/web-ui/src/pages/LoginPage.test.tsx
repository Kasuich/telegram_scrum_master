import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("renders the console sign-in form", () => {
    render(
      <LoginPage
        onPasswordLogin={vi.fn()}
        onRequestCode={vi.fn()}
        onVerifyCode={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Консоль PM-агента" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /получить код/i })).toBeInTheDocument();
  });
});
