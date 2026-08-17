import Icon from "./Icon.jsx";

/**
 * Shared "nothing here yet" pattern for dashboard lists/tables.
 */
export default function EmptyState({ icon = "layout", title, description, action, className = "" }) {
  return (
    <div
      className={`flex flex-col items-center justify-center text-center rounded-2xl border border-dashed border-border bg-page/60 px-6 py-10 ${className}`}
    >
      <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-surface border border-border text-muted mb-3 shrink-0">
        <Icon name={icon} width={20} height={20} />
      </span>
      <p className="text-sm font-semibold text-ink mb-1">{title}</p>
      {description && <p className="text-xs text-muted max-w-xs leading-relaxed">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
