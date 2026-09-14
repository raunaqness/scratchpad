import { NextResponse, type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** Free-text feedback on one turn — attached server-side to that turn's own
 * Langfuse trace (see backend/tracing.py's `record_feedback`). Behind the
 * always-open feedback box under each reply in /app. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  const body = (await req.json().catch(() => ({}))) as {
    conversation_id?: string;
    turn_index?: number;
    comment?: string;
  };
  if (
    !body.conversation_id ||
    typeof body.turn_index !== "number" ||
    !body.comment?.trim()
  ) {
    return NextResponse.json(
      { error: "conversation_id, turn_index, and comment are required" },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${backendUrl()}/api/feedback`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-signal-proxy-secret": proxySecret(),
      },
      body: JSON.stringify({
        conversation_id: body.conversation_id,
        turn_index: body.turn_index,
        comment: body.comment.trim(),
      }),
    });
    return NextResponse.json(await res.json().catch(() => ({})), {
      status: res.status,
    });
  } catch {
    return NextResponse.json({ error: "backend_unreachable" }, { status: 502 });
  }
}
