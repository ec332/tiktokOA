import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute, createRoute, createRouter, Link, Outlet, redirect, RouterProvider,
} from "@tanstack/react-router";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArtifactPage, ArtifactsPage } from "./pages/Artifacts";
import { ReviewsPage } from "./pages/Reviews";
import { RunPage } from "./pages/Run";
import { RunsPage } from "./pages/Runs";
import { SutPage } from "./pages/Sut";
import "./styles.css";

type Theme = "system" | "light" | "dark";

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem("tp.theme") as Theme) || "system"; } catch { return "system"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("tp.theme", theme); } catch { /* storage may be blocked */ }
  }, [theme]);
  return (
    <select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
      <option value="system">system theme</option>
      <option value="light">light</option>
      <option value="dark">dark</option>
    </select>
  );
}

function Shell() {
  return (
    <div className="shell">
      <nav className="sidebar">
        <span className="brand">Test Platform</span>
        <Link to="/runs" activeProps={{ className: "active" }}>Runs</Link>
        <Link to="/reviews" activeProps={{ className: "active" }}>Review queue</Link>
        <Link to="/artifacts" activeProps={{ className: "active" }}>Artifacts</Link>
        <Link to="/sut" activeProps={{ className: "active" }}>SUT</Link>
        <span className="spacer" />
        <ThemeToggle />
      </nav>
      <main><Outlet /></main>
    </div>
  );
}

const root = createRootRoute({ component: Shell });
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", beforeLoad: () => { throw redirect({ to: "/runs" }); } }),
  createRoute({ getParentRoute: () => root, path: "/runs", component: RunsPage }),
  createRoute({ getParentRoute: () => root, path: "/runs/$runId", component: RunPage }),
  createRoute({ getParentRoute: () => root, path: "/reviews", component: ReviewsPage }),
  createRoute({ getParentRoute: () => root, path: "/artifacts", component: ArtifactsPage }),
  createRoute({ getParentRoute: () => root, path: "/artifacts/$id", component: ArtifactPage }),
  createRoute({ getParentRoute: () => root, path: "/sut", component: SutPage }),
] as const;
const router = createRouter({ routeTree: root.addChildren(routes) });

declare module "@tanstack/react-router" {
  interface Register { router: typeof router }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
