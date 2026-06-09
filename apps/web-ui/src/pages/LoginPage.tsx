import { KeyRound, LogIn } from "lucide-react";
import { FormEvent, useState } from "react";

type Challenge = {
  challenge_id: string;
  expires_in_seconds: number;
};

export function LoginPage({
  onPasswordLogin,
  onRequestCode,
  onVerifyCode,
  error,
}: {
  onPasswordLogin: (email: string, password: string) => void;
  onRequestCode: (identifier: string) => Promise<Challenge>;
  onVerifyCode: (challengeId: string, code: string) => void;
  error?: string;
}) {
  const [identifier, setIdentifier] = useState("");
  const [challengeId, setChallengeId] = useState<string>();
  const [code, setCode] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [adminMode, setAdminMode] = useState(false);
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("admin");

  async function requestCode(event: FormEvent) {
    event.preventDefault();
    setRequesting(true);
    try {
      const challenge = await onRequestCode(identifier);
      setChallengeId(challenge.challenge_id);
    } finally {
      setRequesting(false);
    }
  }

  function verifyCode(event: FormEvent) {
    event.preventDefault();
    if (challengeId) onVerifyCode(challengeId, code);
  }

  function passwordLogin(event: FormEvent) {
    event.preventDefault();
    onPasswordLogin(email, password);
  }

  const submit = adminMode ? passwordLogin : challengeId ? verifyCode : requestCode;

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <div>
          <h1>Консоль PM-агента</h1>
          <p>
            {adminMode
              ? "Резервный вход администратора"
              : challengeId
                ? "Введите код, отправленный ботом в Telegram"
                : "Вход через подтверждение в Telegram"}
          </p>
        </div>

        {adminMode ? (
          <>
            <label className="field">
              <span>Почта</span>
              <input
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
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
          </>
        ) : challengeId ? (
          <label className="field">
            <span>Код подтверждения</span>
            <input
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={6}
              pattern="\d{6}"
              placeholder="000000"
              required
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            />
          </label>
        ) : (
          <label className="field">
            <span>Почта или Telegram username</span>
            <input
              autoComplete="username"
              placeholder="@username"
              required
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
            />
          </label>
        )}

        {error ? <div className="error-line">{error}</div> : null}
        <button className="primary-button justify-center" disabled={requesting} type="submit">
          {challengeId && !adminMode ? (
            <KeyRound className="h-4 w-4" />
          ) : (
            <LogIn className="h-4 w-4" />
          )}
          {adminMode
            ? "Войти"
            : challengeId
              ? "Подтвердить код"
              : requesting
                ? "Отправляем"
                : "Получить код"}
        </button>
        <button
          className="logout-button justify-center"
          type="button"
          onClick={() => {
            setAdminMode(!adminMode);
            setChallengeId(undefined);
          }}
        >
          {adminMode ? "Войти через Telegram" : "Вход для администратора"}
        </button>
      </form>
    </main>
  );
}
