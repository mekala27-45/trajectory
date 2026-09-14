"use client";

import { useCallback, useEffect, useState } from "react";

type Theme = "dark" | "light";

const STORAGE_KEY = "trajectory-theme";

/** Switch between the dark default and the light palette, remembering the choice. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const current = document.documentElement.dataset.theme;
    setTheme(current === "light" ? "light" : "dark");
  }, []);

  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // A browser with storage blocked still gets the switch for this page view.
    }
  }, [theme]);

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}
      title={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-muted hover:bg-hover hover:text-fg"
    >
      <span aria-hidden="true" className="text-[13px] leading-none">
        {theme === "dark" ? "L" : "D"}
      </span>
    </button>
  );
}
