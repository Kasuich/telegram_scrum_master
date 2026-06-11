import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Home,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  LogOut,
  PawPrint,
  TerminalSquare,
  UserRound,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Suspense, lazy } from "react";
import type { ReactNode } from "react";

import { api, type UiRole } from "./lib/api";
import { AdminPage } from "./pages/AdminPage";
import { DevPage } from "./pages/DevPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { PeoplePage } from "./pages/PeoplePage";
import { PetPage } from "./pages/PetPage";
import { PlaygroundPage } from "./pages/PlaygroundPage";
import { ProfilePage } from "./pages/ProfilePage";

// Recharts is heavy — load chart-bearing pages (and their charts) on demand.
const BoardPage = lazy(() => import("./pages/BoardPage").then((m) => ({ default: m.BoardPage })));
const TeamLeadPage = lazy(() =>
  import("./pages/TeamLeadPage").then((m) => ({ default: m.TeamLeadPage })),
);

// Role hierarchy: developer ⊃ teamlead ⊃ user. A route lists the minimum role
// that may access it; higher roles inherit access to everything below.
const ROLE_RANK: Record<UiRole, number> = { user: 0, teamlead: 1, developer: 2 };

function canAccess(role: UiRole, min: UiRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[min];
}

interface RouteCtx {
  role: UiRole;
  name: string;
  selfId: string;
  teamId: string | null;
}

interface RouteDef {
  path: string;
  label: string;
  icon: LucideIcon;
  min: UiRole;
  nav: boolean;
  element: (ctx: RouteCtx) => ReactNode;
}

const ROUTES: RouteDef[] = [
  {
    path: "/",
    label: "Главная",
    icon: Home,
    min: "user",
    nav: true,
    element: ({ role, name }) => <HomePage name={name} role={role} />,
  },
  {
    path: "/board",
    label: "Моя доска",
    icon: LayoutGrid,
    min: "user",
    nav: true,
    element: () => <BoardPage />,
  },
  {
    path: "/team",
    label: "Команда",
    icon: Users,
    min: "teamlead",
    nav: true,
    element: ({ teamId }) => <TeamLeadPage teamId={teamId} />,
  },
  {
    path: "/pet",
    label: "Скрамик",
    icon: PawPrint,
    min: "user",
    nav: true,
    element: () => <PetPage />,
  },
  {
    path: "/profile",
    label: "Профиль",
    icon: UserRound,
    min: "user",
    nav: true,
    element: ({ selfId }) => <ProfilePage selfId={selfId} />,
  },
  {
    path: "/users/:userId",
    label: "Профиль",
    icon: UserRound,
    min: "user",
    nav: false,
    element: ({ selfId }) => <ProfilePage selfId={selfId} />,
  },
  {
    path: "/dev",
    label: "Разработка",
    icon: TerminalSquare,
    min: "developer",
    nav: true,
    element: () => <DevPage />,
  },
  {
    path: "/people",
    label: "Люди",
    icon: Users,
    min: "developer",
    nav: true,
    element: () => <PeoplePage />,
  },
  {
    path: "/admin",
    label: "Админ/PM",
    icon: LayoutDashboard,
    min: "developer",
    nav: true,
    element: () => <AdminPage />,
  },
  {
    path: "/playground",
    label: "Песочница",
    icon: ListChecks,
    min: "developer",
    nav: true,
    element: () => <PlaygroundPage />,
  },
];

function defaultPath(role: UiRole): string {
  return role === "developer" ? "/dev" : "/";
}

export function App() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const login = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => api.login(email, password),
    onSuccess: ({ user }) => {
      queryClient.setQueryData(["me"], user);
    },
  });
  const codeLogin = useMutation({
    mutationFn: ({ challengeId, code }: { challengeId: string; code: string }) =>
      api.verifyLoginCode(challengeId, code),
    onSuccess: ({ user }) => {
      queryClient.setQueryData(["me"], user);
    },
  });
  const requestCode = useMutation({
    mutationFn: api.requestLoginCode,
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
    return (
      <LoginPage
        error={requestCode.error?.message ?? codeLogin.error?.message ?? login.error?.message}
        onPasswordLogin={(email, password) => login.mutate({ email, password })}
        onRequestCode={(identifier) => requestCode.mutateAsync(identifier)}
        onVerifyCode={(challengeId, code) => codeLogin.mutate({ challengeId, code })}
      />
    );
  }

  const role = me.data.ui_role;
  const name = me.data.display_name || me.data.email;
  const home = defaultPath(role);
  const navRoutes = ROUTES.filter((route) => route.nav && canAccess(role, route.min));

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Bot className="h-5 w-5" />
          </span>
          <div>
            <div className="brand-title">PM-агент</div>
            <div className="brand-subtitle">{roleLabel(role)}</div>
          </div>
        </div>
        <nav className="nav">
          {navRoutes.map((route) => (
            <NavLink key={route.path} to={route.path} end={route.path === "/"}>
              <route.icon className="h-4 w-4" />
              {route.label}
            </NavLink>
          ))}
        </nav>
        <button className="logout-button" onClick={() => logout.mutate()}>
          <LogOut className="h-4 w-4" />
          {me.data.email}
        </button>
      </aside>
      <main className="main-panel">
        <Suspense fallback={<div className="loading">Загрузка</div>}>
          <Routes>
            {ROUTES.filter((route) => canAccess(role, route.min)).map((route) => (
              <Route
                element={route.element({
                  role,
                  name,
                  selfId: me.data.id,
                  teamId: me.data.team_id,
                })}
                key={route.path}
                path={route.path}
              />
            ))}
            <Route element={<Navigate replace to={home} />} path="*" />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}

function roleLabel(role: UiRole): string {
  if (role === "developer") return "разработчик";
  if (role === "teamlead") return "тимлид";
  return "пользователь";
}
