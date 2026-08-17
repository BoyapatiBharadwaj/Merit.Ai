import { useNavigate, useParams } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import { ExamsListView } from "./examiner/ExamsListPage.jsx";
import { ExamBuilderView } from "./examiner/ExamBuilderPage.jsx";

/**
 * The examiner portal shell.
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
