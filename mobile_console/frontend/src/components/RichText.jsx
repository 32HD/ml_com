import React from "react";

function inlineSegments(text) {
  const raw = String(text || "");
  if (!raw) return [];
  const parts = raw.split(/(`[^`]+`)/g);
  return parts.filter(Boolean).map((part, index) => ({
    id: `${index}:${part.slice(0, 16)}`,
    code: /^`[^`]+`$/.test(part),
    text: /^`[^`]+`$/.test(part) ? part.slice(1, -1) : part,
  }));
}

function parseBlocks(text) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const blocks = [];
  let mode = "paragraph";
  let paragraph = [];
  let list = null;
  let code = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const value = paragraph.join("\n").trim();
    if (value) blocks.push({ type: "paragraph", text: value });
    paragraph = [];
  };

  const flushList = () => {
    if (!list?.items?.length) return;
    blocks.push(list);
    list = null;
  };

  const flushCode = () => {
    if (!code) return;
    blocks.push(code);
    code = null;
  };

  lines.forEach((rawLine) => {
    const line = String(rawLine || "");
    const fence = line.match(/^```([\w-]+)?\s*$/);
    if (fence) {
      if (mode === "code") {
        flushCode();
        mode = "paragraph";
      } else {
        flushParagraph();
        flushList();
        code = { type: "code", lang: String(fence[1] || "").trim(), text: "" };
        mode = "code";
      }
      return;
    }

    if (mode === "code") {
      code.text = code.text ? `${code.text}\n${line}` : line;
      return;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        type: "heading",
        level: heading[1].length,
        text: heading[2].trim(),
      });
      return;
    }

    const quote = line.match(/^>\s+(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push({ type: "quote", text: quote[1].trim() });
      return;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (!list || list.ordered) {
        flushList();
        list = { type: "list", ordered: false, items: [] };
      }
      list.items.push(bullet[1].trim());
      return;
    }

    const ordered = line.match(/^\s*(\d+)\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      if (!list || !list.ordered) {
        flushList();
        list = { type: "list", ordered: true, items: [] };
      }
      list.items.push(ordered[2].trim());
      return;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      return;
    }

    flushList();
    paragraph.push(line.trimEnd());
  });

  flushParagraph();
  flushList();
  flushCode();
  return blocks;
}

function InlineText({ text }) {
  const segments = inlineSegments(text);
  return (
    <>
      {segments.map((segment) =>
        segment.code ? (
          <code key={segment.id} className="rich-inline-code">{segment.text}</code>
        ) : (
          <React.Fragment key={segment.id}>{segment.text}</React.Fragment>
        ),
      )}
    </>
  );
}

export function RichText({ text, className = "", mono = false }) {
  const value = String(text || "").trim();
  if (!value) return null;
  if (mono) {
    return <pre className={`rich-text rich-text-mono ${className}`.trim()}>{value}</pre>;
  }

  const blocks = parseBlocks(value);
  return (
    <div className={`rich-text ${className}`.trim()}>
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          const HeadingTag = block.level === 1 ? "h3" : block.level === 2 ? "h4" : "h5";
          return <HeadingTag key={`${block.type}:${index}`} className="rich-heading"><InlineText text={block.text} /></HeadingTag>;
        }
        if (block.type === "quote") {
          return <blockquote key={`${block.type}:${index}`} className="rich-quote"><InlineText text={block.text} /></blockquote>;
        }
        if (block.type === "code") {
          return (
            <pre key={`${block.type}:${index}`} className="rich-code">
              {block.lang ? <span className="rich-code-lang">{block.lang}</span> : null}
              <code>{block.text}</code>
            </pre>
          );
        }
        if (block.type === "list") {
          const Tag = block.ordered ? "ol" : "ul";
          return (
            <Tag key={`${block.type}:${index}`} className={`rich-list${block.ordered ? " ordered" : ""}`}>
              {block.items.map((item, itemIndex) => (
                <li key={`${index}:${itemIndex}`}><InlineText text={item} /></li>
              ))}
            </Tag>
          );
        }
        return <p key={`${block.type}:${index}`} className="rich-paragraph"><InlineText text={block.text} /></p>;
      })}
    </div>
  );
}
