import Link from "next/link";
import {
  ArrowUpRight,
  Compass,
  GitBranch,
  MessagesSquare,
  PenLine,
  ShieldCheck,
  Wand2,
} from "lucide-react";

const CAPABILITIES = [
  {
    icon: PenLine,
    title: "Get past the blank page",
    body: "Start with a fragment — a sentence, a hunch, three bullet points. Scratchpad takes it from there.",
  },
  {
    icon: MessagesSquare,
    title: "Think with a partner that pushes back",
    body: "It asks the questions you'd forget to ask, surfaces the gaps, and keeps the thread moving forward.",
  },
  {
    icon: Compass,
    title: "Find the sharper angle",
    body: "Ask for directions and see three or four distinct takes. Pick the one that lands, or mix two.",
  },
  {
    icon: Wand2,
    title: "Turn notes into a real draft",
    body: "One click runs a skill — blog outline, social post, marketing campaign — built straight from your scratchpad.",
  },
  {
    icon: GitBranch,
    title: "Keep every version",
    body: "Step back to any earlier take, branch from it, or start again. Nothing you wrote is lost.",
  },
  {
    icon: ShieldCheck,
    title: "Stay honest",
    body: "Any claim you haven't backed up gets flagged as an open question — never quietly invented.",
  },
];

const STEPS = [
  {
    n: "01",
    title: "Jot",
    body: "Dump the raw idea in your own words. No format, no headline, no word count — just the thinking.",
  },
  {
    n: "02",
    title: "Work it",
    body: "Develop a line, tighten a paragraph, poke holes, pull angles. The scratchpad grows with you.",
  },
  {
    n: "03",
    title: "Build",
    body: "When the notes feel right, run a skill. Get a finished piece you can take away and publish yourself.",
  },
];

const AUDIENCE = [
  "Founders & marketers shaping a launch story",
  "Writers & creators who think by writing",
  "Product & engineering turning a feature into words people care about",
  "Anyone with more ideas than finished pieces",
];

export default function HomePage() {
  return (
    <main className="landing-page">
      <section className="landing-hero">
        <div className="eyebrow">Your brainstorm buddy</div>
        <h1>
          Bring the messy idea.
          <span>Leave with the draft.</span>
        </h1>
        <p className="landing-intro">
          Scratchpad is where you think a piece through before you write it. Dump
          a raw idea, kick it around with a partner that asks the right
          questions, then turn your notes into a blog outline, a social post, or
          a full campaign — grounded only in what you actually know.
        </p>
        <div className="landing-cta-row">
          <Link href="/app" className="primary-action">
            Start a scratchpad
            <ArrowUpRight size={18} />
          </Link>
          <a href="#how" className="text-action">
            See how it works
          </a>
        </div>
        <p className="landing-note">
          Free-form. No template. It won&rsquo;t invent a fact you didn&rsquo;t
          give it.
        </p>
      </section>

      <section className="landing-block" aria-labelledby="do-heading">
        <div className="landing-block-head">
          <span className="detail-kicker">What you can do with it</span>
          <h2 id="do-heading">
            It&rsquo;s not a writing tool. It&rsquo;s a thinking one.
          </h2>
        </div>
        <div className="capability-grid">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <article className="capability-card" key={title}>
              <Icon size={18} strokeWidth={1.75} />
              <h3>{title}</h3>
              <p>{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-block" id="how" aria-labelledby="how-heading">
        <div className="landing-block-head">
          <span className="detail-kicker">How it works</span>
          <h2 id="how-heading">Three moves, in any order.</h2>
        </div>
        <ol className="step-list">
          {STEPS.map(({ n, title, body }) => (
            <li className="step" key={n}>
              <span className="step-num">{n}</span>
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-block" aria-labelledby="who-heading">
        <div className="landing-block-head">
          <span className="detail-kicker">Who it&rsquo;s for</span>
          <h2 id="who-heading">Anyone who writes to figure things out.</h2>
        </div>
        <ul className="audience-list">
          {AUDIENCE.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="landing-closer">
        <h2>Your next piece is somewhere in your head. Start pulling it out.</h2>
        <Link href="/app" className="primary-action">
          Open Scratchpad
          <ArrowUpRight size={18} />
        </Link>
      </section>
    </main>
  );
}
