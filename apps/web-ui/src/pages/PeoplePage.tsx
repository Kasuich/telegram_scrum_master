import { useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { Link } from "react-router-dom";

import { api, type UiRole } from "../lib/api";

const ROLE_LABEL: Record<UiRole, string> = {
  developer: "разработчик",
  teamlead: "тимлид",
  user: "пользователь",
};

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function PeoplePage() {
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });

  return (
    <div className="page-grid">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Люди</h2>
            <p>{users.data?.length ?? 0} сотрудников</p>
          </div>
          <Users className="h-5 w-5 text-muted" />
        </div>
        {users.isLoading ? (
          <div className="empty">Загрузка</div>
        ) : (
          <div className="people-grid">
            {(users.data ?? []).map((person) => (
              <Link className="person-card" key={person.user_id} to={`/users/${person.user_id}`}>
                {person.avatar_url ? (
                  <img alt={person.display_name} className="avatar-img" src={api.avatarSrc(person.avatar_url)} style={{ width: 44, height: 44 }} />
                ) : (
                  <div className="avatar-placeholder" style={{ width: 44, height: 44, fontSize: 16 }}>
                    {initials(person.display_name)}
                  </div>
                )}
                <div className="min-w-0">
                  <div className="truncate font-medium">{person.display_name}</div>
                  <div className="truncate text-xs text-muted">
                    {ROLE_LABEL[person.ui_role]}
                    {person.tracker_login ? ` · ${person.tracker_login}` : ""}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
