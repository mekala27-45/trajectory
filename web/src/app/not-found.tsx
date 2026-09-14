import Link from "next/link";

export const metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <div className="flex flex-col gap-3">
      <h1 className="text-[20px] font-semibold">That page is not in this dataset</h1>
      <p className="max-w-[70ch] text-[13.5px] text-muted">
        The site is a static export of one data bundle. A task or run id that was not in the bundle
        when it was built has no page here. If you followed a link from an older build, the id may
        have been dropped when the bundle was regenerated.
      </p>
      <p className="text-[13.5px]">
        <Link href="/" className="text-accent underline underline-offset-2">
          Back to the leaderboard
        </Link>
      </p>
    </div>
  );
}
