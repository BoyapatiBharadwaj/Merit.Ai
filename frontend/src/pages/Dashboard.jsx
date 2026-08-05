import { Navigate } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import StudentDashboard from "./StudentDashboard.jsx";
import AdminDashboard from "./AdminDashboard.jsx";
import ExaminerDashboard from "./ExaminerDashboard.jsx";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function Dashboard() {
  if (!isLoggedIn()) return <RedirectToLogin />;

  const role = getRole();
  if (role === "student") return <StudentDashboard />;
  if (role === "admin") return <AdminDashboard />;
  // Examiners get their own routed section rather than being rendered inline
  // here, so the exam they are working on lives in the URL -- see
  // ExaminerDashboard for what that fixes.
  if (role === "examiner") return <Navigate to="/examiner" replace />;
  return <Navigate to="/" replace />;
}
