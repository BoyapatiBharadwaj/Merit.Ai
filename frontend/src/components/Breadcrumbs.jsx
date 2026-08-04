import { Link } from "react-router-dom";
import Icon from "./Icon.jsx";

/**
 * Trail of `{ label, to? }` segments, e.g.
 *   [{ label: "Dashboard", to: "/dashboard" },
 *    { label: "Examiners", to: "/admin/examiners" },
 *    { label: "Dr. John Smith" }]
 *
 * The last segment (and any segment with no `to`) renders as plain text --
 * "you are here" -- everything before it is a link back up the tree. Used
 * across every admin drill-down page so the admin always has the
 * "Dashboard > Examiners > Dr. John Smith > Python Assessment > Jane Doe"
 * style trail the spec calls for.
 */
export default function Breadcrumbs({ trail }) {
  if (!trail || trail.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className="flex items-center flex-wrap gap-1.5 text-sm mb-5">
      {trail.map((crumb, i) => {
        const isLast = i === trail.length - 1;
        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <Icon name="chevron-left" width={13} height={13} className="rotate-180 text-muted shrink-0" />}
            {isLast || !crumb.to ? (
              <span className={isLast ? "font-semibold text-ink" : "text-muted"}>{crumb.label}</span>
            ) : (
              <Link to={crumb.to} className="text-muted hover:text-primary transition-colors font-medium">
                {crumb.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}
