import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";

export default function Placeholder({ title, description }) {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <Navbar />
      <main className="flex-1 flex items-center justify-center px-5 py-20">
        <div className="text-center max-w-md">
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3.5 py-1.5 text-xs font-semibold text-primary mb-6">
            Coming soon
          </span>
          <h1 className="text-2xl font-extrabold tracking-tight mb-3">{title}</h1>
          <p className="text-muted mb-8">{description}</p>
          <Link to="/" className="inline-flex items-center gap-2 border border-border hover:bg-surface text-ink font-semibold px-5 py-2.5 rounded-xl transition-colors">
            Back to Home
          </Link>
        </div>
      </main>
      <Footer />
    </div>
  );
}
