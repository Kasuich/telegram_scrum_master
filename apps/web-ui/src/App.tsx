import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, LayoutDashboard, ListChecks, LogOut, TerminalSquare } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { api } from "./lib/api";
import { AdminPage } from "./pages/AdminPage";
import { DevPage } from "./pages/DevPage";
import { LoginPage } from "./pages/LoginPage";
import { PlaygroundPage } from "./pages/PlaygroundPage";

export function App() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const login = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => api.login(email, password),
    onSuccess: ({ user }) => {
      queryClient.setQueryData(["me"], user);
    },
  });
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      queryClient.clear();
    },
  });

  if (me.isLoading) {
    return <div className="loading">Загрузка</div>;
  }

  if (!me.data) {
    return <LoginPage error={login.error?.message} onLogin={(email, password) => login.mutate({ email, password })} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Bot className="h-5 w-5" />
          </span>
          <div>
            <div className="brand-title">PM-агент</div>
            <div className="brand-subtitle">{roleLabel(me.data.role)}</div>
          </div>
        </div>
        <nav className="nav">
          <NavLink to="/dev">
            <TerminalSquare className="h-4 w-4" />
            Разработка
          </NavLink>
          <NavLink to="/admin">
            <LayoutDashboard className="h-4 w-4" />
            Админ/PM
          </NavLink>
          <NavLink to="/playground">
            <ListChecks className="h-4 w-4" />
            Песочница
          </NavLink>
        </nav>
        <button className="logout-button" onClick={() => logout.mutate()}>
          <LogOut className="h-4 w-4" />
          {me.data.email}
        </button>
      </aside>
      <main className="main-panel">
        <Routes>
          <Route element={<DevPage />} path="/dev" />
          <Route element={<AdminPage />} path="/admin" />
          <Route element={<PlaygroundPage />} path="/playground" />
          <Route element={<Navigate replace to="/dev" />} path="*" />
        </Routes>
      </main>
    </div>
  );
}

function roleLabel(role: string): string {
  if (role === "admin") return "админ";
  if (role === "dev") return "разработчик";
  return "пользователь";
}
