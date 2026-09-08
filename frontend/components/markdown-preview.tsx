"use client";

import { TextMessagePartProvider } from "@assistant-ui/react";

import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { cn } from "@/lib/utils";

/**
 * Render a plain markdown string with the same pipeline the chat uses
 * (`@assistant-ui/react-markdown` + remark-gfm): headings, lists, tables,
 * fenced code with copy buttons. `streaming` keeps partial markdown stable
 * while tokens are still arriving.
 */
export function MarkdownPreview({
  text,
  streaming = false,
  className,
}: {
  text: string;
  streaming?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("markdown-preview", className)}>
      <TextMessagePartProvider text={text} isRunning={streaming}>
        <MarkdownText />
      </TextMessagePartProvider>
    </div>
  );
}
