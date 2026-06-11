import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Plus, Save, Trash2, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { api, type Contact, type Profile, type UiRole } from "../lib/api";

const ROLE_LABEL: Record<UiRole, string> = {
  developer: "разработчик",
  teamlead: "тимлид",
  user: "пользователь",
};

const CONTACT_TYPES = ["telegram", "email", "phone", "other"];

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function Avatar({ profile, size = 96 }: { profile: Profile; size?: number }) {
  const style = { width: size, height: size };
  if (profile.avatar_url) {
    return <img alt={profile.display_name} className="avatar-img" src={api.avatarSrc(profile.avatar_url)} style={style} />;
  }
  return (
    <div className="avatar-placeholder" style={style}>
      {initials(profile.display_name) || <UserRound className="h-8 w-8" />}
    </div>
  );
}

export function ProfilePage({ selfId }: { selfId: string }) {
  const { userId } = useParams();
  const isOwn = !userId || userId === selfId;
  const profile = useQuery({
    queryKey: ["profile", isOwn ? "me" : userId],
    queryFn: () => (isOwn ? api.myProfile() : api.userProfile(userId as string)),
  });

  if (profile.isLoading) {
    return <div className="page-grid"><section className="surface wide"><div className="empty">Загрузка</div></section></div>;
  }
  if (!profile.data) {
    return <div className="page-grid"><section className="surface wide"><div className="empty">Профиль не найден</div></section></div>;
  }

  return (
    <div className="page-grid">
      {profile.data.is_self ? <OwnProfile profile={profile.data} /> : <PublicProfile profile={profile.data} />}
    </div>
  );
}

function PublicProfile({ profile }: { profile: Profile }) {
  return (
    <section className="surface wide">
      <div className="profile-head">
        <Avatar profile={profile} />
        <div>
          <h2>{profile.display_name}</h2>
          <p className="text-muted">{profile.title || ROLE_LABEL[profile.ui_role]}</p>
        </div>
      </div>
      {profile.bio ? <p className="profile-bio">{profile.bio}</p> : null}
      <ContactList contacts={profile.contacts} />
    </section>
  );
}

function ContactList({ contacts }: { contacts: Contact[] }) {
  if (!contacts.length) return <div className="empty">Контакты не указаны</div>;
  return (
    <div className="list mt-4">
      {contacts.map((contact, index) => (
        <div className="list-row" key={`${contact.type}-${index}`}>
          <span className="mono-chip">{contact.type}</span>
          <span className="ml-2 font-medium">{contact.value}</span>
        </div>
      ))}
    </div>
  );
}

function OwnProfile({ profile }: { profile: Profile }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState(profile.title ?? "");
  const [bio, setBio] = useState(profile.bio ?? "");
  const [contacts, setContacts] = useState<Contact[]>(profile.contacts);
  const [note, setNote] = useState(String(profile.private?.note ?? ""));

  useEffect(() => {
    setTitle(profile.title ?? "");
    setBio(profile.bio ?? "");
    setContacts(profile.contacts);
    setNote(String(profile.private?.note ?? ""));
  }, [profile]);

  const onSaved = (updated: Profile) => {
    queryClient.setQueryData(["profile", "me"], updated);
  };

  const save = useMutation({
    mutationFn: () =>
      api.patchMyProfile({
        title: title || null,
        bio: bio || null,
        contacts: contacts.filter((c) => c.type.trim() && c.value.trim()),
        private: { ...(profile.private ?? {}), note },
      }),
    onSuccess: onSaved,
  });

  const avatar = useMutation({
    mutationFn: (file: File) => api.uploadAvatar(file),
    onSuccess: onSaved,
  });

  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Мой профиль</h2>
          <p>{ROLE_LABEL[profile.ui_role]}{profile.tracker_login ? ` · ${profile.tracker_login}` : ""}</p>
        </div>
        <button className="primary-button" disabled={save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" />
          Сохранить
        </button>
      </div>

      <div className="profile-head">
        <Avatar profile={profile} />
        <div className="grid gap-2">
          <button className="secondary-button" onClick={() => fileRef.current?.click()} disabled={avatar.isPending}>
            <Camera className="h-4 w-4" />
            {avatar.isPending ? "Загрузка" : "Сменить фото"}
          </button>
          <span className="text-xs text-muted">{profile.email}</span>
          <input
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            ref={fileRef}
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) avatar.mutate(file);
              event.target.value = "";
            }}
          />
        </div>
      </div>
      {avatar.error ? <div className="error-line">{(avatar.error as Error).message}</div> : null}

      <label className="field mt-4">
        <span>Должность</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Например, Project Manager" />
      </label>
      <label className="field mt-4">
        <span>О себе</span>
        <textarea className="profile-textarea" value={bio} onChange={(event) => setBio(event.target.value)} />
      </label>

      <div className="label mt-5">Контакты (видны другим)</div>
      <div className="list mt-2">
        {contacts.map((contact, index) => (
          <div className="contact-row" key={index}>
            <input
              list="contact-types"
              placeholder="тип"
              value={contact.type}
              onChange={(event) => setContacts((items) => items.map((c, i) => (i === index ? { ...c, type: event.target.value } : c)))}
            />
            <input
              placeholder="значение"
              value={contact.value}
              onChange={(event) => setContacts((items) => items.map((c, i) => (i === index ? { ...c, value: event.target.value } : c)))}
            />
            <button className="icon-button" aria-label="Удалить" title="Удалить" onClick={() => setContacts((items) => items.filter((_, i) => i !== index))}>
              <Trash2 className="h-4 w-4 text-rose" />
            </button>
          </div>
        ))}
        <datalist id="contact-types">
          {CONTACT_TYPES.map((type) => (
            <option key={type} value={type} />
          ))}
        </datalist>
        <button className="secondary-button self-start" onClick={() => setContacts((items) => [...items, { type: "", value: "" }])}>
          <Plus className="h-4 w-4" />
          Добавить контакт
        </button>
      </div>

      <label className="field mt-5">
        <span>Личная заметка (видна только вам)</span>
        <textarea className="profile-textarea" value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
    </section>
  );
}
