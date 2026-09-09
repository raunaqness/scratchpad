# PRD: House Voice — a knowledge base from their blog

**Product:** Signal (Scratchpad)
**Feature name (working):** House Voice
**Status:** draft for discussion — product only, not an engineering spec
**Audience:** a person who makes content for **one company** (founder, marketer, content lead)

**v1 input:** one public blog URL (`company.com/blog` or similar)
**v1 output:** a knowledge base Signal can write from, after the user confirms it

---

## 1. Why this exists

Signal today waits for the user to type facts. That is honest, and it is also a cold start. The people we want to serve already have a public blog. That writing *is* the brief.

House Voice lets them paste that blog URL once. Signal reads the posts, builds a knowledge base of how they write, what they write about, and who they seem to write for, and only then starts helping them make the next piece. Nothing is treated as company truth until they agree.

---

## 2. Job to be done

When I sit down to write the next post for my company, I want Signal to already know our topics, our cadence, our tone, and our reader, so I am not briefing a stranger every thread.

**Success feels like:** “Yes, that’s us” on a one-screen briefing, then the first draft sounds like the blog without copying a post.

**Failure feels like:** a generic LinkedIn voice, invented product claims, or a dump of scraped text into the scratchpad.

---

## 3. Who it is for (v1)

**In**

- One person writing for one brand
- They can share a public blog URL
- They will spend two minutes confirming what Signal inferred

**Out**

- Agencies juggling many clients in one account
- Private / login-only blogs
- Other websites, social feeds, or files
- Auto-posting anywhere

---

## 4. Product idea: a knowledge base, filled by a Reader

The user should not feel they launched a crawler. They should feel they hired a **researcher who comes back with a briefing**, and that briefing becomes the company’s knowledge base for writing.

| Piece | Role |
| --- | --- |
| **You** | Paste the blog URL. Confirm or correct the briefing. Write the next piece. |
| **Scratchpad** | Where *this* idea lives. Does not get filled with the whole blog. |
| **Knowledge base (House Voice)** | A durable portrait of the brand, sitting beside threads, not inside one chat. Built only from that blog. |
| **Reader (sub-agent)** | Goes to the blog, gathers posts, comes back. Visible, pausable, not the main writer. |

The Reader is a **researcher**. It does not draft posts, schedule anything, or chat with the user as a second personality. When it is done, the main Signal collaborator uses the knowledge base the way an editor uses a style sheet plus a fact file.

### Why a sub-agent (and when not to)

A separate Reader is the right metaphor when gathering takes more than a few seconds: finding the blog index, walking several posts, summarizing. The user should see “Reading 12 of 24 posts…” and be able to stop it. That work should not lock the composer or look like the writer is thinking.

A sub-agent is the **wrong** metaphor if we pretend it is roaming the internet freely. v1 is: **the blog URL the user named, a bounded set of posts on that site, then a briefing.** Not a general research agent.

There is only one door: **paste the blog URL.**

---

## 5. What the knowledge base contains

House Voice is a living portrait with two layers. The UI should make the layers obvious.

### Layer A — Observed (from the blog)

Things we can point at a post and say “we saw this”:

- Recurring topics and how often they show up
- How pieces are usually built (announcement, how-to, story, list, changelog)
- Texture of the prose (plain vs specialist, short vs long, first person vs company “we”, humor, CTA habits)
- Names they actually use for the product and the reader
- A handful of **short excerpts** as style samples, with links back to the source post
- Recurring facts and claims that appear in the writing (still confirmed by the user before they become “true for us”)

### Layer B — Inferred (must be confirmed)

Things we are guessing:

- Who they are trying to win (role, seniority, industry)
- What they are trying to make the reader believe or do
- Stage of the company / category (as it appears in the writing)
- What they seem to avoid (hard sells, jargon, founder memoir, etc.)

Inferred items show as **proposals** with Accept / Edit / Reject. Until accepted, they are assumptions, not facts. Signal must not treat “you sell to CISOs” as knowledge if the user never agreed.

### What we deliberately do *not* put in the knowledge base

- Full copies of every article (we keep excerpts + links, not a shadow CMS)
- Metrics, customer logos, or pricing unless they appear in the writing *and* the user confirms they still stand
- Anything from a different website than the blog they pointed at

---

## 6. End-to-end flow

### 6.1 First-run (empty product, new user)

1. User hits **Start writing**.
2. Empty workspace offers two equally obvious paths:
   - “What would you like to write about today?” (current scratchpad)
   - “Learn from our blog” — secondary, but visible; not buried in settings
3. Choosing that opens a **short sheet**, not a settings maze:
   - Field: “What’s the URL of your blog?”
   - Helper text: e.g. `https://company.com/blog`
   - Optional: “This is for [company name]” if it is not obvious from the site
4. User submits. The sheet becomes a **Reader progress card** in the thread (and a House Voice panel on the side):
   - Found the blog
   - Collecting posts (count)
   - Reading a sample
   - Building the knowledge base
5. User can **keep jotting in the scratchpad** while this runs, or wait. They can **Stop**.
6. Reader finishes. The main collaborator does **not** auto-write a post. It presents the **briefing**.
7. Briefing screen (the most important UI in this feature):
   - One paragraph: “Here’s how you sound.”
   - Topics as chips
   - Reader we think you write for (inferred — confirm)
   - Two or three “this sounds like you / this does not” contrast lines
   - Sources: titles + dates + links of posts we actually read
   - Actions: **That’s us** · **Edit a few things** · **Try a different URL**
8. **That’s us** saves the knowledge base. A line drops into the scratchpad only as a pointer: “House Voice on file for Acme — topics X, reader Y.” The portrait lives in House Voice, not as a wall of notes.
9. Composer prompt changes in spirit to: “What would you like to write about today?” because the *who* is known. Starter ideas can now be specific: “A post in your usual how-to shape about [top topic].”

### 6.2 They skip this and write first

Allowed. House Voice is optional. If they later paste a blog URL in the composer, we offer: “Build a knowledge base from this blog?” rather than silently reading it.

### 6.3 They already have a knowledge base

New thread: a quiet badge, “Writing in Acme’s voice.” They can ignore it, open the portrait, or say “don’t use house voice for this thread” (guest post, personal rant, new product line).

### 6.4 Refresh

The blog changes. House Voice shows **last read** and **Refresh**. Refresh re-runs the Reader on the same URL, then diffs the briefing: “You have started writing more about security; CTAs got softer.” User accepts the update or keeps the old knowledge base.

Do not refresh in the background without asking. A content lead should not find their voice “updated” overnight.

### 6.5 Using the knowledge base when writing

When they brainstorm, expand, or run a skill (blog outline, social post, campaign):

- Match **texture** (how it sounds) from House Voice
- Prefer **topics and names** they actually use
- Still ground **new claims** in this thread’s confirmed facts
- If House Voice and the scratchpad disagree (“blog says we are for startups; this thread says enterprise”), ask once, do not blend silently

A later delight (not required for v1 ship): **Voice check** on a derived draft — “this is more salesy than your last ten posts” — as critique, not a blocker.

### 6.6 Failure and thin-source paths

| What happened | What the user sees |
| --- | --- |
| URL is a homepage with no posts | “We couldn’t find articles. Try the blog index, e.g. company.com/blog.” |
| Site is blocked, login, or empty | Honest stop. Ask them to try another public blog URL. |
| Only 1–2 short posts | Briefing labeled **thin**. Still useful for tone; we say we are guessing more. |
| User stops the Reader | Keep whatever we already drafted; mark incomplete; offer resume or a new URL. |
| Posts are all guest authors / mixed | Ask: “Several voices here — which should we treat as the house?” |

Never invent a confident audience from two paragraphs.

---

## 7. The briefing experience (be opinionated)

The briefing should feel like a **creative director’s one-pager**, not an analytics dashboard.

Suggested sections, in this order:

1. **The room** — one sentence on who seems to be spoken to, and in what situation they would read this.
2. **The stance** — what the writing is trying to do (teach, announce, reassure, pick a fight, document).
3. **The grain** — how sentences behave (length, “we” vs “I”, humor, how hard they sell).
4. **The map** — five to eight topic clusters, not a tag cloud of 80 words.
5. **The tells** — three phrases or moves that show up a lot (so we can reuse them without parody).
6. **The don’ts** — inferred only, always editable: what would sound off-brand.
7. **The shelf** — the posts we read, so trust is inspectable.

Optional creative extra for v1 if it stays light: **two fake headlines** — “a post that would fit” vs “a post that would not.” User taps which world they live in. Faster than a form.

---

## 8. Functional requirements

### Must have (v1)

- User can start House Voice by pasting **one public blog URL**.
- Gathering is visible, stoppable, and does not freeze writing.
- Signal only reads pages on that blog’s site, and only a **bounded recent sample** (enough to hear a voice and fill a knowledge base, not the entire archive).
- User sees which posts were read (title, date if present, link).
- Briefing separates **observed** vs **inferred**; inferred items need accept / edit / reject.
- Saving House Voice does not paste the blog into the scratchpad.
- Later writing in that workspace can use the accepted knowledge base for tone, topics, and confirmed facts from the blog.
- User can ignore House Voice, write without it, or turn it off for a thread.
- User can refresh later and review a change summary before replacing the knowledge base.
- Clear empty/error states when there is no public writing to read.
- User can discard House Voice entirely.

### Should have (v1 or immediately after)

- Thin-source honesty when the sample is small.
- Mixed-author / guest-post handling with a simple choice.
- Starter prompts on the empty composer that reflect accepted topics (“Write this week’s note on [topic]”).
- Voice-aware critique: one pass that says whether a draft drifted from the house.

### Nice to have (later)

- Multiple House Voices per account (personal vs company, or two products).
- Compare two drafts against the portrait.
- A “use recent posts only” choice when the archive is huge.

### Must not

- Follow the whole internet from one URL.
- Auto-publish, schedule, or email anyone.
- Present guesses as confirmed company facts.
- Hide sources. If we cannot show where a claim came from, it is not observed.

---

## 9. Non-functional requirements (in product language)

**Trust.** The user can always see what we read and what we guessed. No black-box “we trained on your site.”

**Pace.** First briefing should feel like a coffee wait, not a research project. If gathering will take longer, say so up front and let them work in the scratchpad.

**Gentleness on their blog.** We are a guest: few pages, no hammering, no pretending to be a logged-in visitor.

**Quiet by default.** No surprise rereads. No knowledge-base changes applied while they are mid-draft.

**Stay a writing tool.** House Voice makes the collaborator *informed*. It does not become a second chatbot, a SEO auditor, or a site clone.

**Reversible.** Wrong URL, wrong brand, wrong era of the blog — throw it away and start again without wrecking threads.

**Respect mixed content.** Old acquisition-era posts should not overpower the last six months if the user says “use recent only.”

**Privacy of the obvious kind.** We store a knowledge base of links/excerpts and a portrait the user asked us to learn from, not a hidden full-text archive for unrelated purposes. User can delete it.

**Honesty when thin.** Better a modest briefing than a confident fiction.

**Fits the existing promise.** Signal still does not invent metrics or customers. House Voice is *how we sound and what the blog already said*, not *proof we grew 40%.*

---

## 10. Scope for the first ship

**In**

- One House Voice per user/workspace
- One public blog URL
- Reader gathers a bounded set of posts on that site
- Knowledge base + briefing + confirm
- Use in brainstorm / write / skills as tone, topic, and confirmed-blog-fact guidance
- Refresh on request

**Out**

- Any source that is not that blog
- Multi-brand agencies
- Ranking, SEO, or market research beyond the blog
- Scheduling and publishing
- Continuous background reading of the internet

---

## 11. How we will know it worked

Qualitative (early)

- Users who complete a briefing say “that’s us” more often than they rewrite the whole thing
- First post after briefing needs less “make it sound like us”
- Users can name which source posts we used without digging

Directional (when there is usage)

- Share of new workspaces that finish a briefing (not just paste a URL)
- Share that accept at least the audience inference or replace it with their own words (both are wins; abandon is the loss)
- Drop-off at gathering vs at briefing (tells us if the Reader or the portrait is the problem)

Anti-goals: number of pages crawled, length of the portrait, “AI detected their brand colors.”

---

## 12. Open questions

- Is House Voice **account-level** (always on) or **workspace-level** (this company, this month)? v1 can be one per logged-in user; agencies will force a change later.
- Should confirming the briefing **also** seed today’s small memory (`audience` / `tone`), or is House Voice the only place that lives?
- How recent is “recent”? Last 12 posts vs last 12 months — let the user pick if the archive is huge.
- If the blog mixes product announcements and thought leadership, is that one knowledge base or does the user need to say which posts count?

---

## 13. Recommendation

Ship **one door**: paste a blog URL. A **Reader sub-agent** gathers posts on that site and fills a **knowledge base**. The user confirms a briefing. The scratchpad stays for the piece they are writing now.

Do not put a second conversational agent in the product. Do not dump the blog into the scratchpad.

The creative bet: the briefing screen *is* the product. If that page makes a content lead nod, everything downstream has a real house to write as. If it feels like a scrape report, we failed even if the gathering worked.
