export function SiteFooter() {
  return (
    <footer className="mt-10 border-t border-line">
      <div className="mx-auto w-full max-w-[1280px] px-4 py-6 text-xs text-dim sm:px-6">
        <p className="max-w-[70ch]">
          Every number on this site comes from a recorded run. The run records, the tasks and the
          scoring code are in the repository, so any figure here can be recomputed from the
          trajectory it came from.
        </p>
      </div>
    </footer>
  );
}
