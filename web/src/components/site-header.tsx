import Link from "next/link";

import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: "/", label: "Leaderboard" },
  { href: "/failures/", label: "Failure modes" },
  { href: "/guide/", label: "Guide" },
] as const;

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-page/95 backdrop-blur">
      <div className="mx-auto flex h-12 w-full max-w-[1280px] items-center gap-3 px-4 sm:px-6">
        <Link href="/" className="group flex min-w-0 items-center gap-2.5">
          <span aria-hidden="true" className="h-[11px] w-[11px] shrink-0 rounded-[2px] bg-accent" />
          <span className="font-display text-[19px] font-medium tracking-[-0.01em] group-hover:text-accent">
            trajectory
          </span>
          <span className="hidden truncate pl-1 text-xs text-dim sm:inline">
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
