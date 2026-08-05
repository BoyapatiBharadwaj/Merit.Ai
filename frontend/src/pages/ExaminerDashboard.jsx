import { useNavigate, useParams } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import { ExamsListView } from "./examiner/ExamsListPage.jsx";
import { ExamBuilderView } from "./examiner/ExamBuilderPage.jsx";

/**
 * The examiner portal shell.
 *
 * This file used to be 2,080 lines holding every examiner screen -- the exam
 * list, the builder, the schedule form, five panels and the access editor --
 * with the selected exam and the active tab in React state. Three consequences,
 * which the audit reported separately and which were all the same problem:
 *
 *   * Refreshing threw the examiner back to the exam list, mid-edit.
 *   * Browser Back left the application entirely instead of going up one level.
 *   * No view had a URL, so an examiner could not bookmark the exam they were
 *     working on, or send a colleague a link to the violations under discussion.
 *
 * Which exam is open is now the URL and the tab is a query parameter (see
 * ExamBuilderView). Refresh and Back work because the router already knows how
 * to do them, not because this component learned to.
 */
export default function ExaminerDashboard() {
  const { examId } = useParams();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <DashboardHeader title="Examiner Portal" />
      {examId ? (
        <ExamBuilderView examId={Number(examId)} onBack={() => navigate("/examiner")} />
      ) : (
        <ExamsListView onManage={(id) => navigate(`/examiner/exams/${id}`)} />
      )}
    </div>
  );
}
