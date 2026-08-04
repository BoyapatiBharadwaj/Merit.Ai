import { Navigate } from "react-router-dom";
import StudentDashboard from "./StudentDashboard.jsx";
import AdminDashboard from "./AdminDashboard.jsx";
import ExaminerDashboard from "./ExaminerDashboard.jsx";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function Dashboard() {
  if (!isLoggedIn()) return <Navigate to="/login" replace />;

  const role = getRole();
  if (role === "student") return <StudentDashboard />;
  if (role === "admin") return <AdminDashboard />;
  if (role === "examiner") return <ExaminerDashboard />;
  return <Navigate to="/" replace />;
}
