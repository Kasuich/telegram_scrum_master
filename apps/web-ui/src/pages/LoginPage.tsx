import { LogIn } from "lucide-react";
import { FormEvent, useState } from "react";

export function LoginPage({
  onLogin,
  error,
}: {
  onLogin: (email: string, password: string) => void;
  error?: string;
}) {
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("admin");

  function submit(event: FormEvent) {
    event.preventDefault();
    onLogin(email, password);
  }

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <div>
          <h1>Консоль PM-агента</h1>
          <p>Панель управления</p>
        </div>
        <label className="field">
          <span>Почта</span>
          <input autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? <div className="error-line">{error}</div> : null}
        <button className="primary-button justify-center" type="submit">
          <LogIn className="h-4 w-4" />
          Войти
        </button>
      </form>
    </main>
  );
}
