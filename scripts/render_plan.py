#!/usr/bin/env python3
"""Render a Markdown plan into a self-contained, mobile-friendly HTML page.

Used by serve-plan / the serve_plan MCP tool. Produces ONE .html file that the
serve-image daemon serves as a normal text/html blob — no daemon changes needed
beyond the shared /assets/mermaid.min.js route.

Mermaid code fences become <pre class="mermaid"> and are drawn client-side from a
root-relative <script src="/assets/mermaid.min.js">, so the served page bakes in no
daemon IP and stays tiny.

Renderer preference:
  1. mistune (high-fidelity GFM: tables, strikethrough, task lists) if importable
  2. a built-in minimal Markdown→HTML converter otherwise (never hard-fails)

CLI:  render_plan.py <plan.md> [out.html] [--title "..."]
"""

from __future__ import annotations

import datetime
import html
import os
import re
import sys
from typing import Optional


# ── Markdown → body HTML ──────────────────────────────────────────────────────

def _render_body_mistune(md_text: str) -> Optional[str]:
    """High-fidelity render via mistune, with mermaid fences passed through."""
    try:
        import mistune
    except Exception:
        return None

    class PlanRenderer(mistune.HTMLRenderer):  # type: ignore[name-defined]
        def block_code(self, code: str, info: Optional[str] = None) -> str:
            lang = (info or "").strip().split() [0].lower() if info else ""
            if lang == "mermaid":
                return f'<pre class="mermaid">{html.escape(code)}</pre>\n'
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            return f"<pre><code{cls}>{html.escape(code)}</code></pre>\n"

    try:
        md = mistune.create_markdown(
            renderer=PlanRenderer(escape=False),
            plugins=["table", "strikethrough", "task_lists", "url"],
        )
        return md(md_text)
    except Exception:
        return None


def _inline(text: str) -> str:
    """Minimal inline Markdown → HTML for the built-in fallback."""
    out = html.escape(text)
    # images then links (image first so ![..](..) isn't eaten by link rule)
    out = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img alt="\1" src="\2">', out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", out)
    return out


def _render_body_fallback(md_text: str) -> str:
    """Dependency-free converter. Covers what plans actually use."""
    lines = md_text.replace("\r\n", "\n").split("\n")
    html_parts: list[str] = []
    i = 0
    n = len(lines)
    para: list[str] = []

    def flush_para() -> None:
        if para:
            html_parts.append("<p>" + _inline(" ".join(para)).strip() + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code (``` or ~~~), optionally with a language/mermaid info string
        m = re.match(r"^(```+|~~~+)\s*([\w+-]*)", stripped)
        if m:
            fence, lang = m.group(1), m.group(2).lower()
            flush_para()
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith(fence[0] * 3):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code = "\n".join(buf)
            if lang == "mermaid":
                html_parts.append(f'<pre class="mermaid">{html.escape(code)}</pre>')
            else:
                cls = f' class="language-{lang}"' if lang else ""
                html_parts.append(f"<pre><code{cls}>{html.escape(code)}</code></pre>")
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        # ATX heading
        hm = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if hm:
            flush_para()
            level = len(hm.group(1))
            html_parts.append(f"<h{level}>{_inline(hm.group(2).strip())}</h{level}>")
            i += 1
            continue

        # horizontal rule
        if re.match(r"^([-*_])\1{2,}$", stripped):
            flush_para()
            html_parts.append("<hr>")
            i += 1
            continue

        # blockquote (collapse consecutive lines)
        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            html_parts.append("<blockquote>" + _inline(" ".join(quote)) + "</blockquote>")
            continue

        # GFM pipe table: header row + delimiter row
        if "|" in stripped and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            flush_para()

            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            header = cells(lines[i])
            i += 2  # skip header + delimiter
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            html_parts.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>")
            continue

        # lists (unordered / ordered / task), one level
        lm = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if lm:
            flush_para()
            ordered = bool(re.match(r"\d+\.", lm.group(2)))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < n:
                im = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", lines[i])
                if not im:
                    break
                content = im.group(3)
                task = re.match(r"^\[([ xX])\]\s+(.*)$", content)
                if task:
                    checked = "checked" if task.group(1).lower() == "x" else ""
                    items.append(
                        f'<li class="task"><input type="checkbox" disabled {checked}> '
                        f"{_inline(task.group(2))}</li>"
                    )
                else:
                    items.append(f"<li>{_inline(content)}</li>")
                i += 1
            html_parts.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(html_parts)


def render_body(md_text: str) -> str:
    return _render_body_mistune(md_text) or _render_body_fallback(md_text)


# ── Page assembly ─────────────────────────────────────────────────────────────

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1c1e21; background: #ffffff;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 820px; margin: 0 auto; padding: 1rem 1.15rem 4rem; }
.provenance {
  position: sticky; top: 0; z-index: 5;
  margin: 0 -1.15rem 1.25rem; padding: .55rem 1.15rem;
  font-size: .78rem; color: #65676b; background: rgba(255,255,255,.85);
  backdrop-filter: saturate(180%) blur(8px);
  border-bottom: 1px solid #e3e5e8; word-break: break-word;
}
h1,h2,h3,h4,h5,h6 { line-height: 1.25; margin: 1.6em 0 .55em; font-weight: 650; }
h1 { font-size: 1.85rem; margin-top: .4em; }
h2 { font-size: 1.45rem; padding-bottom: .25em; border-bottom: 1px solid #e3e5e8; }
h3 { font-size: 1.2rem; }
p, ul, ol, blockquote, table, pre { margin: 0 0 1rem; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { padding-left: 1.5rem; }
li { margin: .25em 0; }
li.task { list-style: none; margin-left: -1.2rem; }
li.task input { margin-right: .5rem; }
blockquote {
  margin-left: 0; padding: .2rem 0 .2rem 1rem;
  border-left: 3px solid #cfd2d6; color: #4b4f56;
}
code {
  font: .88em/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #f0f1f3; padding: .15em .4em; border-radius: 5px;
}
pre {
  background: #f6f7f9; border: 1px solid #e3e5e8; border-radius: 8px;
  padding: .9rem 1rem; overflow-x: auto;
}
pre code { background: none; padding: 0; }
/* Render diagrams at natural size (not shrunk-to-fit) inside a horizontal
   scroller, so text stays legible on mobile. Tap opens a zoomable overlay. */
pre.mermaid {
  background: transparent; border: 1px solid #e3e5e8; border-radius: 8px;
  padding: .6rem; margin: 0 0 1rem; overflow-x: auto; -webkit-overflow-scrolling: touch;
  text-align: center;
}
pre.mermaid svg { height: auto; max-width: none; cursor: zoom-in; }
.mm-hint {
  display: block; margin: -.6rem 0 1rem; font-size: .72rem; color: #8a8d91;
  text-align: center;
}
.mm-overlay {
  position: fixed; inset: 0; z-index: 50; overflow: auto;
  background: rgba(255,255,255,.98); -webkit-overflow-scrolling: touch;
  display: flex; align-items: flex-start; justify-content: center; padding: 1rem;
  touch-action: pinch-zoom;
}
.mm-overlay svg { width: 200%; max-width: none; height: auto; }
.mm-close {
  position: fixed; top: .6rem; right: .8rem; z-index: 51;
  font-size: 1.4rem; line-height: 1; padding: .3rem .6rem; border-radius: 8px;
  background: #1c1e21; color: #fff; border: none; cursor: pointer; opacity: .85;
}
table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }
th, td { border: 1px solid #d8dade; padding: .5rem .7rem; text-align: left; }
th { background: #f0f1f3; font-weight: 650; }
tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; height: auto; border-radius: 6px; }
hr { border: none; border-top: 1px solid #e3e5e8; margin: 2rem 0; }
@media (prefers-color-scheme: dark) {
  body { color: #e4e6eb; background: #18191a; }
  .provenance { color: #b0b3b8; background: rgba(24,25,26,.85); border-color: #303234; }
  h2 { border-color: #303234; }
  a { color: #6ea8fe; }
  blockquote { border-color: #3a3d42; color: #b0b3b8; }
  code { background: #2a2c2e; }
  pre { background: #1f2123; border-color: #303234; }
  pre.mermaid { background: transparent; }
  .mm-overlay { background: rgba(24,25,26,.985); }
  .mm-close { background: #e4e6eb; color: #18191a; }
  th, td { border-color: #3a3d42; }
  th { background: #242628; }
  tr:nth-child(even) td { background: #1d1f21; }
  hr { border-color: #303234; }
}
"""

_MERMAID_BOOT = """
<script src="/assets/mermaid.min.js"></script>
<script>
  (function () {
    if (!window.mermaid) return;
    var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    // useMaxWidth:false → diagrams render at natural size (legible on mobile) and
    // scroll inside their container instead of shrinking to the phone's width.
    var noShrink = { useMaxWidth: false };
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: dark ? 'dark' : 'default',
        securityLevel: 'loose',
        fontFamily: 'inherit',
        flowchart: { useMaxWidth: false, htmlLabels: true },
        sequence: noShrink, class: noShrink, state: noShrink,
        er: noShrink, gantt: noShrink, journey: noShrink, pie: noShrink
      });
      var run = window.mermaid.run({ querySelector: '.mermaid' });
      Promise.resolve(run).then(addZoom).catch(function (e) {
        console.error('mermaid render failed', e);
      });
    } catch (e) { console.error('mermaid render failed', e); }

    // Tap any diagram to open a fullscreen, pinch-zoomable overlay.
    function addZoom() {
      document.querySelectorAll('pre.mermaid').forEach(function (pre) {
        var svg = pre.querySelector('svg');
        if (!svg) return;
        var hint = document.createElement('span');
        hint.className = 'mm-hint';
        hint.textContent = 'tap diagram to zoom';
        pre.insertAdjacentElement('afterend', hint);
        svg.addEventListener('click', function () { openOverlay(svg); });
      });
    }
    function openOverlay(svg) {
      var o = document.createElement('div');
      o.className = 'mm-overlay';
      var clone = svg.cloneNode(true);
      clone.removeAttribute('width');
      clone.removeAttribute('height');
      o.appendChild(clone);
      var close = document.createElement('button');
      close.className = 'mm-close';
      close.setAttribute('aria-label', 'Close');
      close.textContent = '\\u2715';
      function dismiss() { o.remove(); close.remove(); }
      close.addEventListener('click', dismiss);
      o.addEventListener('click', function (e) { if (e.target === o) dismiss(); });
      document.body.appendChild(o);
      document.body.appendChild(close);
    }
  })();
</script>
"""


def _derive_title(md_text: str, fallback: str) -> str:
    for line in md_text.split("\n"):
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return fallback


def build_page(body_html: str, *, title: str, source_label: str, rendered_at: str) -> str:
    prov = html.escape(f"Rendered from {source_label}  ·  {rendered_at}")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f'<div class="wrap">\n<div class="provenance">{prov}</div>\n'
        f"{body_html}\n</div>\n"
        f"{_MERMAID_BOOT}\n"
        "</body>\n</html>\n"
    )


def render_file(md_path: str, out_path: Optional[str] = None, *, title: Optional[str] = None) -> str:
    md_path = os.path.abspath(os.path.expanduser(md_path))
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    page_title = title or _derive_title(md_text, os.path.basename(md_path))
    rendered_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = build_page(
        render_body(md_text),
        title=page_title,
        source_label=md_path,
        rendered_at=rendered_at,
    )
    if out_path is None:
        out_path = os.path.splitext(md_path)[0] + ".html"
    out_path = os.path.abspath(os.path.expanduser(out_path))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--title"]
    title = None
    if "--title" in argv:
        idx = argv.index("--title")
        if idx + 1 < len(argv):
            title = argv[idx + 1]
            args = [a for a in args if a != title]
    if not args:
        print("usage: render_plan.py <plan.md> [out.html] [--title TITLE]", file=sys.stderr)
        return 2
    md_path = args[0]
    out_path = args[1] if len(args) > 1 else None
    try:
        out = render_file(md_path, out_path, title=title)
    except FileNotFoundError:
        print(f"file not found: {md_path}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
