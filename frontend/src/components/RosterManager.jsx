import { useCallback, useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import EmptyState from "./EmptyState.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary, btnGhost, fieldLabelCompact } from "../lib/ui.js";

/**
 * Organization student roster.
 *
 * This is the control that decides who can see this organization's exams at
 * all. Before organizations existed, every published exam was visible to
 * every student on the platform; enrolment here is what replaced that.
 *
 * The roster keys on email rather than on existing accounts, because
 * examiners enrol a cohort before those people have signed up. An entry with
 * no linked account is an outstanding invitation, which is a normal state and
 * is labelled as such rather than looking like an error.
 */
export default function RosterManager() {
  const [members, setMembers] = useState(null);
  const [organization, setOrganization] = useState(null);
  const [emails, setEmails] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [removingIds, setRemovingIds] = useState(() => new Set());
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const [org, roster] = await Promise.all([
        Api.get("/organizations/me"),
        Api.get("/organizations/me/roster"),
      ]);
      setOrganization(org);
      setMembers(roster);
    } catch (err) {
      setMembers([]);
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your student roster.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleEnrol(event) {
    event.preventDefault();
    if (!emails.trim()) return;
    setSaving(true);
    setFeedback(null);
    try {
      // The textarea is sent as one string on purpose -- the backend splits on
      // commas, semicolons, whitespace and newlines, so a column pasted
      // straight out of a spreadsheet works without reformatting.
      const result = await Api.post("/organizations/me/roster", { emails });
      const parts = [];
      if (result.added.length) parts.push(`${result.added.length} added`);
      if (result.linked.length) parts.push(`${result.linked.length} matched an existing account`);
      if (result.already_present.length) parts.push(`${result.already_present.length} already enrolled`);
      const invalid = result.invalid || [];
      const elsewhere = result.in_another_organization || [];
      if (invalid.length) {
        parts.push(`${invalid.length} skipped (not an email): ${invalid.slice(0, 3).join(", ")}${
          invalid.length > 3 ? "…" : ""}`);
      }
      if (elsewhere.length) {
        parts.push(`${elsewhere.length} already belong to another organization and will NOT get access: ${
          elsewhere.slice(0, 3).join(", ")}${elsewhere.length > 3 ? "…" : ""}`);
      }
      setFeedback({
        // Anything skipped or blocked is worth a warning colour even when the
        // rest succeeded -- a silently dropped address becomes a student who
        // cannot sit the exam, discovered on the day.
        tone: invalid.length || elsewhere.length ? "warn"
          : result.added.length ? "success" : "muted",
        message: parts.join(" · ") || "Nothing to add.",
      });
      // Keep the box populated when something was rejected, so the examiner
      // can see and correct what they pasted instead of losing it.
      if (!invalid.length) setEmails("");
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Something went wrong." });
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(member) {
    // A Set rather than a single id: with one id, removing two students in
    // quick succession let the second request's cleanup clear the flag while
    // the first was still in flight, re-enabling a button whose row was about
    // to disappear.
    setRemovingIds((prev) => new Set(prev).add(member.id));
    setFeedback(null);
    try {
      await Api.del(`/organizations/me/roster/${member.id}`);
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't remove that student." });
    } finally {
      setRemovingIds((prev) => {
        const next = new Set(prev);
        next.delete(member.id);
        return next;
      });
    }
  }

  const visible = (members || []).filter((m) =>
    !filter.trim() ||
    m.email.toLowerCase().includes(filter.toLowerCase()) ||
    (m.student_name || "").toLowerCase().includes(filter.toLowerCase()),
  );
  const registered = (members || []).filter((m) => m.student_id).length;
  const invited = (members || []).length - registered;

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <h2 className="font-bold text-lg text-ink flex items-center gap-2">
            <Icon name="users" width={18} height={18} className="text-primary" />
            Student roster
          </h2>
          <p className="text-sm text-muted mt-1">
            {organization ? (
              <>Only students enrolled in <strong className="text-ink">{organization.name}</strong> can see your exams.</>
            ) : (
              "Only enrolled students can see your exams."
            )}
          </p>
        </div>
        {members && members.length > 0 && (
          <div className="text-xs text-muted text-right shrink-0">
            <div><strong className="text-ink">{registered}</strong> registered</div>
            {invited > 0 && <div>{invited} invited, not signed up yet</div>}
          </div>
        )}
      </div>

      {loadError && (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{loadError}</span>
        </div>
      )}

      <form onSubmit={handleEnrol} className="mt-5">
        <label className={fieldLabelCompact} htmlFor="roster-emails">
          Add students by email
        </label>
        <textarea
          id="roster-emails"
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={3}
          placeholder={"anna@college.edu, ben@college.edu\nOr paste a column straight from a spreadsheet"}
          className="w-full rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink placeholder:text-muted/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 transition"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="submit" disabled={saving || !emails.trim()}
                  className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
            {saving ? "Enrolling…" : "Enrol students"}
          </button>
          <span className="text-xs text-muted">
            Students who haven't signed up yet are linked automatically when they register.
          </span>
        </div>
        {feedback && (
          <p className={`mt-3 text-sm ${
            feedback.tone === "error" ? "text-danger"
              : feedback.tone === "warn" ? "text-amber-600 dark:text-amber-400"
              : feedback.tone === "success" ? "text-success" : "text-muted"
          }`}>
            {feedback.message}
          </p>
        )}
      </form>

      <div className="mt-6 border-t border-border pt-5">
        {members === null ? (
          <div className="flex flex-col gap-2">
            <div className="skeleton skeleton-text" />
            <div className="skeleton skeleton-text" />
          </div>
        ) : members.length === 0 ? (
          <EmptyState
            icon="users"
            title="No students enrolled yet"
            description="Until you add someone here, no student can see or start your exams."
          />
        ) : (
          <>
            {members.length > 8 && (
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by name or email"
                className="mb-3 w-full rounded-xl border border-border bg-page pl-4 pr-4 py-2 text-sm text-ink placeholder:text-muted/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 transition"
              />
            )}
            <ul className="flex flex-col divide-y divide-border">
              {visible.map((member) => (
                <li key={member.id} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink truncate">
                      {member.student_name || member.email}
                    </div>
                    {member.student_name && (
                      <div className="text-xs text-muted truncate">{member.email}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                      member.student_id
                        ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                    }`}>
                      {member.student_id ? "Registered" : "Invited"}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleRemove(member)}
                      disabled={removingIds.has(member.id)}
                      title="Remove from roster"
                      className="text-muted hover:text-danger transition-colors disabled:opacity-50"
                    >
                      <Icon name="x" width={15} height={15} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            {visible.length === 0 && (
              <p className="text-sm text-muted py-3">No one matches "{filter}".</p>
            )}
            <p className="mt-4 text-xs text-muted">
              Removing a student revokes access to future exams. Results they have already
              submitted are kept.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
