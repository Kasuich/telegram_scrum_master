import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("renders the console sign-in form", () => {
    render(<LoginPage onLogin={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Консоль PM-агента" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /войти/i })).toBeInTheDocument();
  });
});
