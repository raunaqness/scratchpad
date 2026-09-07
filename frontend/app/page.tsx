import Link from "next/link";
import { ArrowUpRight, Check, CircleDot } from "lucide-react";

export default function HomePage() {
  return (
    <main className="landing-page">
      <section className="landing-hero">
        <div className="eyebrow">
          <CircleDot size={12} strokeWidth={3} />
          Grounded content, clearly written
        </div>
        <h1>
          Turn product truth
          <span> into a signal.</span>
        </h1>
        <p className="landing-intro">
          Signal helps you shape the facts you already know into a confident
          LinkedIn post—without inventing a single claim.
        </p>
        <Link href="/app" className="primary-action">
          Start writing
          <ArrowUpRight size={18} />
        </Link>
        <p className="landing-note">Bring a product name and three facts.</p>
      </section>

      <section className="landing-details" aria-label="How Signal works">
        <div className="detail-heading">
          <span className="detail-kicker">The Signal method</span>
          <h2>A smaller brief makes a stronger post.</h2>
        </div>
        <div className="detail-list">
          {[
            "Share the facts you can stand behind.",
            "Choose the tone and audience.",
            "Review a draft that stays on brief.",
          ].map((item) => (
            <div className="detail-item" key={item}>
              <Check size={16} />
              <span>{item}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
