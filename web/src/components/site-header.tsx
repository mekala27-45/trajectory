import Link from "next/link";

import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: "/", label: "Leaderboard" },
  { href: "/failures/", label: "Failure modes" },
] as const;

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-page/95 backdrop-blur">
      <div className="mx-auto flex h-12 w-full max-w-[1280px] items-center gap-3 px-4 sm:px-6">
        <Link href="/" className="group flex min-w-0 items-baseline gap-2">
          <span className="font-mono text-[15px] font-semibold tracking-tight group-hover:text-accent">
            trajectory
          </span>
          <span className="hidden truncate text-xs text-dim sm:inline">
            evaluation harness for coding agents
          </span>
        </Link>
        <nav aria-label="Sections" className="ml-auto flex items-center gap-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-md px-2 py-1 text-[13px] text-muted hover:bg-hover hover:text-fg"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <ThemeToggle />
      </div>
    </header>
  );
}
