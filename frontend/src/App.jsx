import { Routes, Route } from "react-router-dom";
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import ForgotPassword from "./pages/ForgotPassword.jsx";
import Register from "./pages/Register.jsx";
import RequestAccess from "./pages/RequestAccess.jsx";
import Privacy from "./pages/Privacy.jsx";
import Features from "./pages/Features.jsx";
import Pricing from "./pages/Pricing.jsx";
import About from "./pages/About.jsx";
import FAQ from "./pages/FAQ.jsx";
import Contact from "./pages/Contact.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Profile from "./pages/Profile.jsx";
import Exam from "./pages/Exam.jsx";
import Results from "./pages/Results.jsx";
import Placeholder from "./pages/Placeholder.jsx";
import AdminExaminers from "./pages/AdminExaminers.jsx";
import AdminExaminerDetail from "./pages/AdminExaminerDetail.jsx";
import AdminExams from "./pages/AdminExams.jsx";
import AdminExamDetail from "./pages/AdminExamDetail.jsx";
import AdminCandidates from "./pages/AdminCandidates.jsx";
import AdminCandidateDetail from "./pages/AdminCandidateDetail.jsx";
import AdminAttemptReport from "./pages/AdminAttemptReport.jsx";
import AdminLiveSessions from "./pages/AdminLiveSessions.jsx";
import AdminViolations from "./pages/AdminViolations.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/register" element={<Register />} />
      {/* Examiners can't self-register -- this collects a request an admin
          reviews, and is where the marketing site's examiner CTA points. */}
      <Route path="/request-access" element={<RequestAccess />} />
      <Route path="/privacy" element={<Privacy />} />

      {/* Marketing pages linked from the nav -- each a full page rather than
          an anchor scroll, so they're directly linkable/bookmarkable. */}
      <Route path="/features" element={<Features />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/about" element={<About />} />
      <Route path="/faq" element={<FAQ />} />
      <Route path="/contact" element={<Contact />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/profile" element={<Profile />} />
      <Route path="/exam/:examId" element={<Exam />} />
      <Route path="/results/:attemptId" element={<Results />} />

      {/* Admin dashboard drill-down pages -- each guarded admin-only inline
          (mirroring the isLoggedIn/getRole guard idiom used elsewhere, e.g.
          Profile.jsx), rather than a wrapping layout route, since that's the
          existing pattern for role-gated pages in this app. */}
      <Route path="/admin/examiners" element={<AdminExaminers />} />
      <Route path="/admin/examiners/:examinerId" element={<AdminExaminerDetail />} />
      <Route path="/admin/exams" element={<AdminExams />} />
      <Route path="/admin/exams/:examId" element={<AdminExamDetail />} />
      <Route path="/admin/candidates" element={<AdminCandidates />} />
      <Route path="/admin/candidates/:studentId" element={<AdminCandidateDetail />} />
      <Route path="/admin/attempts/:attemptId" element={<AdminAttemptReport />} />
      <Route path="/admin/live-sessions" element={<AdminLiveSessions />} />
      <Route path="/admin/violations" element={<AdminViolations />} />

      <Route path="*" element={<Placeholder title="Page not found" description="That page doesn't exist yet." />} />
    </Routes>
  );
}
