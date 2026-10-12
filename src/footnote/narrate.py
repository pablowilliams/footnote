"""Turn verified segments into the final answer text.

The default narrator is a template: it joins the sentences the answer builders
wrote. An optional Claude narrator can rewrite the computed sentences into more
natural prose, but it receives only those sentences and their figures, and its
output goes back through the same number and citation checks. If it introduces a
figure that is not in the evidence, its text is discarded and the template is used.
Quotations from filings are never rewritten.
"""

from __future__ import annotations

import os

from footnote.answers import Draft
from footnote.logs import log
from footnote.schemas import Segment
from footnote.verify import run_checks

_SYSTEM = (
    "You edit short answers about company financial results. Rewrite the draft so it reads as clear, formal "
    "English. Keep every figure exactly as written, character for character, and keep every citation marker "
    "such as [1] directly after the figure it supports. Do not add any figure, comparison or claim that is not "
    "in the draft. Return only the rewritten text."
)


def render(segments: list[Segment]) -> str:
    lines: list[str] = []
    for s in segments:
        if s.bullet:
            marker = "".join(f"[{i}]" for i in s.citation_ids)
            lines.append(f"- “{s.text}” {marker}".rstrip())
        else:
            lines.append(s.text)
    out: list[str] = []
    para: list[str] = []
    for line in lines:
        if line.startswith("- "):
            if para:
                out.append(" ".join(para))
                para = []
            out.append(line)
        else:
            para.append(line)
    if para:
        out.append(" ".join(para))
    return "\n".join(out)


def narrate(draft: Draft) -> tuple[str, str]:
    """Return the answer text and which narrator produced it."""
    template = render(draft.segments)
    if os.environ.get("FOOTNOTE_NARRATOR", "template") != "claude":
        return template, "template"
    computed = [s for s in draft.segments if s.kind == "computed"]
    if not computed:
        return template, "template"
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model="claude-opus-5",
            max_tokens=2000,
            system=_SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-06-01"],
            # Older SDK releases do not name this parameter yet, so it travels in the body.
            extra_body={"fallbacks": [{"model": "claude-opus-4-8"}]},
            messages=[{"role": "user", "content": " ".join(s.text for s in computed)}],
        )
        if response.stop_reason == "refusal":
            return template, "template (model declined)"
        rewritten = "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception as err:  # any API failure falls back to the template answer
        log.warning("narrate.fallback", error=str(err))
        return template, "template (model unavailable)"
    candidate = Segment(text=rewritten, kind="computed", citation_ids=[i for s in computed for i in s.citation_ids])
    rest = [s for s in draft.segments if s.kind != "computed"]
    checks = run_checks(draft, [candidate, *rest])
    if not all(c.passed for c in checks if c.name in ("numbers_sourced", "citations_resolve")):
        log.warning("narrate.rejected", checks=[c.model_dump() for c in checks if not c.passed])
        return template, "template (model output failed verification)"
    leading = [s for s in draft.segments if s.kind == "context" and draft.segments.index(s) < draft.segments.index(computed[0])]
    return render([*leading, candidate, *[s for s in rest if s not in leading]]), "claude-opus-5"
