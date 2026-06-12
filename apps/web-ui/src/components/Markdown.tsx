/**
 * Minimal Markdown renderer — headings, bold, and bullet/numbered lists.
 * The project has no markdown dependency; the audit agent emits simple
 * Markdown, so a tiny inline renderer keeps the bundle lean.
 */

function renderInline(text: string): (string | JSX.Element)[] {
  // Split on **bold** spans, keeping the delimiters out.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: JSX.Element[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item, i) => <li key={i}>{renderInline(item)}</li>);
    blocks.push(
      list.ordered ? <ol key={`l${blocks.length}`}>{items}</ol> : <ul key={`l${blocks.length}`}>{items}</ul>,
    );
    list = null;
  };

  lines.forEach((raw, index) => {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);

    if (heading) {
      flushList();
      const level = heading[1].length;
      const Tag = (`h${Math.min(level + 1, 6)}`) as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={index} className="md-heading">{renderInline(heading[2])}</Tag>);
    } else if (bullet) {
      if (!list || list.ordered) flushList(), (list = { ordered: false, items: [] });
      list.items.push(bullet[1]);
    } else if (numbered) {
      if (!list || !list.ordered) flushList(), (list = { ordered: true, items: [] });
      list.items.push(numbered[1]);
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      blocks.push(<p key={index}>{renderInline(line)}</p>);
    }
  });
  flushList();

  return <div className="markdown">{blocks}</div>;
}
