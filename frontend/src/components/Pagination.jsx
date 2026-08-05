/**
 * Pagination footer for the admin list tables.
 *
 * The note that used to sit here said these endpoints return the whole
 * filtered list in one response because the tables are "platform-scale, not
 * internet-scale". That reasoning does not survive contact with a real
 * institution: violations accumulate per candidate per exam and never stop, and
 * a few thousand candidates is an ordinary year. Downloading all of them to
 * display fifteen rows cost the transfer, the parse and the render every time a
 * filter changed.
 *
 * The endpoints are paginated server-side now (see backend
 * app/schemas/pagination.py). This component only reports which page to fetch;
 * `paginate` below remains for the few small lists still held in memory.
 */
export default function Pagination({ page, pageCount, onChange, totalCount, pageSize }) {
  if (pageCount <= 1) return null;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalCount);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-border text-xs text-muted">
      <span>
        {start}–{end} of {totalCount}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          className="px-3 py-1.5 rounded-lg border border-border font-semibold disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
        >
          Previous
        </button>
        <span className="px-2 py-1.5 font-semibold text-ink">
          {page} / {pageCount}
        </span>
        <button
          type="button"
          disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}
          className="px-3 py-1.5 rounded-lg border border-border font-semibold disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
        >
          Next
        </button>
      </div>
    </div>
  );
}

/** `rows.slice(...)` for a list the client genuinely holds in full -- an
 * exam's own sections, an organization's roster. NOT for the admin
 * collections, which are paged by the server; slicing those meant fetching
 * everything to show one page. */
export function paginate(rows, page, pageSize) {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const start = (safePage - 1) * pageSize;
  return { pageRows: rows.slice(start, start + pageSize), pageCount, safePage };
}
