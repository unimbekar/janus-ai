import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarkdownBody } from "@/components/MarkdownBody";

function html(content: string): string {
  return renderToStaticMarkup(createElement(MarkdownBody, { content }));
}

describe("MarkdownBody", () => {
  it("renders headings, bold, lists, and blockquotes instead of raw markers", () => {
    const markup = html(`### Dijkstra's Algorithm

**What it does**: finds the **shortest path**.

> It fails if weights can be negative.

- Greedy approach
- Why it works`);

    expect(markup).toContain("<h3>");
    expect(markup).toContain("Dijkstra");
    expect(markup).toContain("<strong>");
    expect(markup).toContain("<blockquote>");
    expect(markup).toContain("<ul>");
    expect(markup).not.toContain("### ");
    expect(markup).not.toContain("**What it does**");
  });

  it("renders nothing for empty content", () => {
    expect(html("")).toBe("");
  });
});
