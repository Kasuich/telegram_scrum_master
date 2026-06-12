import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { TgApp } from "./tg/TgApp";
import "./styles/index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
    },
  },
});

// The Telegram Mini App is served from /tg — a separate, mobile-first shell with
// its own initData auth, distinct from the desktop console under "/".
const isMiniApp = window.location.pathname.startsWith("/tg");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {isMiniApp ? (
        <TgApp />
      ) : (
        <BrowserRouter>
          <App />
        </BrowserRouter>
      )}
    </QueryClientProvider>
  </React.StrictMode>,
);
