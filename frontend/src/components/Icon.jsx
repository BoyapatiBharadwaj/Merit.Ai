/**
 * Single shared icon set (stroke-based, 24x24 viewBox) used across the marketing page and the
 * auth forms, so icon weight/style never drifts between sections.
 */
export default function Icon({ name, ...props }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    // Every call site in this app pairs the icon with adjacent visible text
    // (a button label, a heading, a bullet's own copy), so the icon itself
    // carries no independent meaning for assistive tech by default.
    "aria-hidden": true,
    ...props,
  };
  switch (name) {
    case "eye":
      return <svg {...common}><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z" /><circle cx="12" cy="12" r="3" /></svg>;
    case "eye-off":
      return (
        <svg {...common}>
          <path d="M3 3l18 18" />
          <path d="M10.6 5.1A11 11 0 0 1 12 5c7 0 11 7 11 7a13.2 13.2 0 0 1-3.4 4M6.5 6.6C3.7 8.4 1 12 1 12s4 7 11 7c1.4 0 2.7-.3 3.9-.7" />
          <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
        </svg>
      );
    case "bell":
      return <svg {...common}><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></svg>;
    case "code":
      return <svg {...common}><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>;
    case "shield":
      return <svg {...common}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></svg>;
    case "chart":
      return <svg {...common}><path d="M3 3v18h18" /><path d="M18 17V9M13 17V5M8 17v-4" /></svg>;
    case "doc":
      return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><polyline points="14 2 14 8 20 8" /><path d="M9 15h6M9 11h6" /></svg>;
    case "briefcase":
      return <svg {...common}><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" /></svg>;
    case "user":
      return <svg {...common}><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></svg>;
    case "shield-check":
      return <svg {...common}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></svg>;
    case "check":
      return <svg {...common} strokeWidth={2.8}><polyline points="20 6 9 17 4 12" /></svg>;
    case "arrow":
      return <svg {...common} strokeWidth={2.4}><path d="M5 12h14M13 5l7 7-7 7" /></svg>;
    case "mail":
      return <svg {...common}><rect x="2" y="4" width="20" height="16" rx="2.5" /><path d="m3 6.5 9 6 9-6" /></svg>;
    case "lock":
      return <svg {...common}><rect x="4" y="10.5" width="16" height="10" rx="2.5" /><path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5" /></svg>;
    case "hash":
      return <svg {...common}><path d="M5 9h14M5 15h14M10 3 8 21M16 3l-2 18" /></svg>;
    case "alert":
      return <svg {...common}><path d="M12 9v4.5" /><path d="M10.3 3.9 1.8 18.5A1.5 1.5 0 0 0 3.1 21h17.8a1.5 1.5 0 0 0 1.3-2.5L13.7 3.9a1.7 1.7 0 0 0-3.4 0Z" /><circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none" /></svg>;
    case "spinner":
      return (
        <svg {...common} strokeWidth={3}>
          <circle cx="12" cy="12" r="9" opacity="0.25" />
          <path d="M21 12a9 9 0 0 0-9-9" />
        </svg>
      );
    case "settings":
      return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>;
    case "users":
      return <svg {...common}><path d="M17 21v-2a4 4 0 0 0-3-3.87" /><path d="M9 21v-2a4 4 0 0 1 3-3.87" /><circle cx="9" cy="7" r="4" /><path d="M20 21v-2a4 4 0 0 0-2.5-3.7" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>;
    case "layout":
      return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 21V9" /></svg>;
    case "grid":
      return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>;
    case "chevron-left":
      return <svg {...common} strokeWidth={2.4}><polyline points="15 18 9 12 15 6" /></svg>;
    case "grip":
      return <svg {...common} fill="currentColor" stroke="none"><circle cx="9" cy="6" r="1.4" /><circle cx="9" cy="12" r="1.4" /><circle cx="9" cy="18" r="1.4" /><circle cx="15" cy="6" r="1.4" /><circle cx="15" cy="12" r="1.4" /><circle cx="15" cy="18" r="1.4" /></svg>;
    case "camera":
      return <svg {...common}><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2Z" /><circle cx="12" cy="13" r="4" /></svg>;
    case "mic":
      return <svg {...common}><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><path d="M12 19v3M8 22h8" /></svg>;
    case "monitor":
      return <svg {...common}><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></svg>;
    case "flag":
      return <svg {...common}><path d="M4 22V4" /><path d="M4 4h13l-2.5 4L17 12H4" /></svg>;
    case "maximize":
      return <svg {...common}><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" /></svg>;
    case "x":
      return <svg {...common} strokeWidth={2.4}><path d="M18 6 6 18M6 6l12 12" /></svg>;
    case "trash":
      return <svg {...common}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></svg>;
    case "clock":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>;
    case "battery":
      return <svg {...common}><rect x="2" y="7" width="18" height="10" rx="2" /><line x1="22" y1="11" x2="22" y2="13" /></svg>;
    case "zap":
      return <svg {...common}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" /></svg>;
    case "wifi":
      return (
        <svg {...common}>
          <path d="M5 12.55a11 11 0 0 1 14.08 0" />
          <path d="M1.42 9a16 16 0 0 1 21.16 0" />
          <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
          <circle cx="12" cy="20" r="0.6" fill="currentColor" stroke="none" />
        </svg>
      );
    case "chevron-down":
      return <svg {...common} strokeWidth={2.4}><polyline points="6 9 12 15 18 9" /></svg>;
    case "plus":
      return <svg {...common} strokeWidth={2.4}><path d="M12 5v14M5 12h14" /></svg>;
    case "send":
      return <svg {...common}><path d="m22 2-11 11" /><path d="M22 2 15 22l-4-9-9-4Z" /></svg>;
    case "phone":
      return <svg {...common}><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3.1-8.6A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .3 2 .6 2.9a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.9.3 1.9.5 2.9.6a2 2 0 0 1 1.8 2.1Z" /></svg>;
    case "map-pin":
      return <svg {...common}><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" /><circle cx="12" cy="10" r="3" /></svg>;
    case "heart":
      return <svg {...common}><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8Z" /></svg>;
    case "compass":
      return <svg {...common}><circle cx="12" cy="12" r="10" /><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" /></svg>;
    case "target":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></svg>;
    default:
      return null;
  }
}

export function IconBadge({ name, tone = "primary" }) {
  const tones = {
    primary: "bg-primary/10 text-primary",
    surface: "bg-page text-primary border border-border",
  };
  return (
    <div className={`inline-flex items-center justify-center w-11 h-11 rounded-xl shrink-0 ${tones[tone]}`}>
      <Icon name={name} width={20} height={20} />
    </div>
  );
}
