// Thin wrapper over the Telegram WebApp SDK (loaded via telegram-web-app.js).
// Degrades gracefully outside Telegram so the page can be developed in a browser.

interface TelegramWebApp {
  initData: string;
  ready: () => void;
  expand: () => void;
  colorScheme?: "light" | "dark";
  themeParams?: Record<string, string>;
  HapticFeedback?: {
    impactOccurred: (style: "light" | "medium" | "heavy") => void;
    notificationOccurred: (type: "error" | "success" | "warning") => void;
  };
  openLink?: (url: string) => void;
  MainButton?: { hide: () => void };
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function webApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp;
}

export function isTelegram(): boolean {
  return Boolean(webApp()?.initData);
}

/** initData for backend auth. In dev (outside TG) read a mock from VITE_TG_DEV_INITDATA. */
export function initData(): string {
  const real = webApp()?.initData;
  if (real) return real;
  return (import.meta.env.VITE_TG_DEV_INITDATA as string | undefined) ?? "";
}

export function tgReady(): void {
  const wa = webApp();
  if (!wa) return;
  try {
    wa.ready();
    wa.expand();
  } catch {
    /* no-op */
  }
}

export function haptic(type: "success" | "error" | "warning" = "success"): void {
  try {
    webApp()?.HapticFeedback?.notificationOccurred(type);
  } catch {
    /* no-op */
  }
}

export function impact(style: "light" | "medium" | "heavy" = "medium"): void {
  try {
    webApp()?.HapticFeedback?.impactOccurred(style);
  } catch {
    /* no-op */
  }
}
