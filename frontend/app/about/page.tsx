import Link from "next/link";

export default function AboutPage() {
  return (
    <main className="simple-page">
      <p className="detail-kicker">About Scratchpad</p>
      <h1>Think it through with a clear line back to the source.</h1>
      <p>
        Scratchpad is a freeform thinking surface. You jot down raw, half-formed
        ideas about anything and rework them with an AI collaborator until the
        notes feel right. When you&rsquo;re ready, a skill turns the scratchpad
        into a concrete piece — a blog outline, a social post, a campaign.
      </p>
      <p>
        It does not research for you or fill gaps with guesses. Unverified
        specifics are surfaced as open questions, not stated as fact.
      </p>
      <Link href="/app" className="text-action">
        Open the scratchpad →
      </Link>
    </main>
  );
}
