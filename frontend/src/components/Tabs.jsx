/**
 * Simple underline tab strip -- used by the Examiner Detail page's
 * All/Active/Upcoming/Completed exam tabs.
 */
export default function Tabs({ tabs, active, onChange }) {
  return (
    <div className="flex gap-1 border-b border-border mb-4 overflow-x-auto">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`px-4 py-2.5 text-sm font-semibold whitespace-nowrap border-b-2 transition-colors ${
            active === t.id ? "border-primary text-primary" : "border-transparent text-muted hover:text-ink"
          }`}
        >
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 text-xs text-muted">({t.count})</span>}
        </button>
      ))}
    </div>
  );
}
