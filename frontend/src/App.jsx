import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import RouteFallback from "./components/RouteFallback.jsx";

// Eager: the entry points. Login and the 404 are small, and Home is the first
// paint for most visitors -- lazy-loading them would add a network round-trip
// to the very screens where perceived speed matters most.
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import Placeholder from "./pages/Placeholder.jsx";

/**
 * Route-level code splitting.
 *
 * Everything used to be a static import, which produced one ~909KB bundle that
 * every visitor downloaded in full. A candidate on exam-day bandwidth was
 * pulling down the admin dashboard, the examiner exam-builder, the whole
 * marketing site and a charting library before their exam could render -- none
 * of which they will ever open, and some of which they are not authorised to.
 *
 * Splitting by route means each of those becomes a chunk fetched only when its
 * path is actually visited. The grouping below is by audience rather than by
 * file, because that matches how the app is really used: nobody navigates from
 * an admin drill-down into the exam runner.
 */

// --- marketing -------------------------------------------------------------
const ForgotPassword = lazy(() => import("./pages/ForgotPassword.jsx"));
const Register = lazy(() => import("./pages/Register.jsx"));
const RequestAccess = lazy(() => import("./pages/RequestAccess.jsx"));
const Privacy = lazy(() => import("./pages/Privacy.jsx"));
const Features = lazy(() => import("./pages/Features.jsx"));
const Pricing = lazy(() => import("./pages/Pricing.jsx"));
const About = lazy(() => import("./pages/About.jsx"));
const FAQ = lazy(() => import("./pages/FAQ.jsx"));
const Contact = lazy(() => import("./pages/Contact.jsx"));

// --- signed-in, all roles --------------------------------------------------
const Dashboard = lazy(() => import("./pages/Dashboard.jsx"));
const Profile = lazy(() => import("./pages/Profile.jsx"));
const Results = lazy(() => import("./pages/Results.jsx"));

// --- the exam runner -------------------------------------------------------
// The single biggest page in the app, and the one whose dependencies (CodeMirror,
// the proctoring stack) are heaviest. Splitting it out matters in both
// directions: a marketing visitor never downloads it, and a candidate who does
// gets a chunk that is not padded with admin code.
const Exam = lazy(() => import("./pages/Exam.jsx"));

// --- admin drill-down ------------------------------------------------------
const AdminExaminers = lazy(() => import("./pages/AdminExaminers.jsx"));
const AdminExaminerDetail = lazy(() => import("./pages/AdminExaminerDetail.jsx"));
const AdminExams = lazy(() => import("./pages/AdminExams.jsx"));
const AdminExamDetail = lazy(() => import("./pages/AdminExamDetail.jsx"));
const AdminCandidates = lazy(() => import("./pages/AdminCandidates.jsx"));
const AdminCandidateDetail = lazy(() => import("./pages/AdminCandidateDetail.jsx"));
const AdminAttemptReport = lazy(() => import("./pages/AdminAttemptReport.jsx"));
const AdminLiveSessions = lazy(() => import("./pages/AdminLiveSessions.jsx"));
const AdminViolations = lazy(() => import("./pages/AdminViolations.jsx"));

export default function App() {
  return (
    // The boundary wraps the router rather than sitting inside it, so it also
    // catches a chunk that fails to load -- a real possibility on the flaky
    // connections this app is explicitly built to tolerate, and one that would
    // otherwise render a blank white page with nothing in the UI to explain it.
    <ErrorBoundary>
      <Suspense fallback={<RouteFallback />}>
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
      </Suspense>
    </ErrorBoundary>
  );
}
