import Link from "next/link";

export default function AboutPage() {
  return (
    <main className="simple-page">
      <p className="detail-kicker">About Signal</p>
      <h1>Writing with a clear line back to the source.</h1>
      <p>
        Signal is a constrained LinkedIn-post assistant. It turns product
        information you explicitly provide into a draft, then helps you edit
        and validate it.
      </p>
      <p>
        It does not research products or fill gaps with guesses. Every useful
        post starts with information you can confirm.
      </p>
      <Link href="/app" className="text-action">
        Open the writing room →
      </Link>
    </main>
  );
}
