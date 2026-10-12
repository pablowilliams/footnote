"""The question-answering pipeline as a small, explicit LangGraph state graph.

    parse -> route -> one of {structured, document, structured+document, evidence, clarify, refuse} -> finalise

There is one model-free decision point (the router) and no agent loop. Every path
ends in the same finalise step, which verifies the draft and decides the status.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, TypedDict

from footnote import backend
from footnote.answers import Draft, document, join_names, short_name, structured
from footnote.catalog import COMPANIES, METRICS
from footnote.logs import log
from footnote.narrate import narrate
from footnote.question import ParsedQuestion, parse
from footnote.schemas import Answer, Citation, Route, Segment, Status
from footnote.verify import run_checks, status_from

_SAYS = re.compile(r"\b(say|says|said|describe[sd]?|discuss(es|ed)?|mention(s|ed)?|comment(s|ed)? on)\b", re.I)


class State(TypedDict, total=False):
    question: str
    previous_answer_id: str | None
    parsed: ParsedQuestion
    route: Route
    draft: Draft
    answer: Answer


def decide_route(p: ParsedQuestion) -> Route:
    if p.refusal_reason:
        return Route.refuse
    if p.evidence_request:
        return Route.evidence
    if not p.tickers:
        return Route.refuse if p.unknown_names else Route.clarify
    if p.ambiguity and not p.document:
        return Route.clarify
    if p.metrics:
        if _SAYS.search(p.raw) and not p.years and not p.change:
            return Route.document
        if p.why:
            return Route.sql_and_document
        if len(p.tickers) >= 2:
            return Route.comparison
        if p.change:
            return Route.sql_calculation
        return Route.sql
    if len(p.tickers) >= 2 and p.compare and not p.document:
        return Route.clarify
    return Route.document


# ---------------------------------------------------------------------------- nodes


def node_parse(state: State) -> State:
    return {"parsed": parse(state["question"])}


def node_route(state: State) -> State:
    route = decide_route(state["parsed"])
    log.info("route", route=route.value, tickers=state["parsed"].tickers, metrics=state["parsed"].metrics)
    return {"route": route}


def node_structured(state: State) -> State:
    return {"draft": structured(state["parsed"], state["route"])}


def node_document(state: State) -> State:
    return {"draft": document(state["parsed"], state["question"])}


def node_structured_document(state: State) -> State:
    p = state["parsed"]
    p.change = True
    draft = structured(p, Route.sql_and_document)
    if draft.status is None:
        p_years = sorted(draft.allowed_years)
        p.years = p_years[-1:] if p_years else p.years
        document(p, state["question"], draft=draft)
        draft.allowed_years |= set(p_years)
        if not any(s.kind == "quoted" for s in draft.segments):
            draft.segments.append(
                Segment(
                    text="The annual report does not contain a passage that explains the change closely enough to quote.",
                    kind="context",
                )
            )
            draft.best_similarity = draft.best_similarity or 0.0
    return {"draft": draft}


def node_evidence(state: State) -> State:
    prev = state.get("previous_answer_id")
    draft = Draft(route=Route.evidence)
    if not prev:
        draft.status = Status.ambiguous
        draft.reason = "There is no earlier answer in this conversation to show evidence for. Ask a question first."
        return {"draft": draft}
    row = backend.get().load_answer(prev)
    if not row:
        draft.status = Status.ambiguous
        draft.reason = "The earlier answer could not be found."
        return {"draft": draft}
    earlier = Answer.model_validate(row)
    draft.status = earlier.status
    draft.reason = f"Evidence for the answer to: “{earlier.question}”"
    for c in earlier.citations:
        draft.book.citations.append(c)
        detail = f"[{c.id}] {c.label}"
        if c.snippet:
            detail += f": “{c.snippet}”"
        draft.segments.append(Segment(text=detail, kind="context", bullet=False))
    for calc_ in earlier.calculations:
        inputs = ", ".join(f"{i.label} [{i.citation_id}]" for i in calc_.inputs)
        draft.segments.append(Segment(text=f"Calculation: {calc_.description} = {calc_.formula}, from {inputs}.", kind="context"))
    draft.calculations = earlier.calculations
    draft.__dict__["evidence_of"] = earlier
    return {"draft": draft}


def node_clarify(state: State) -> State:
    p = state["parsed"]
    draft = Draft(route=Route.clarify, status=Status.ambiguous)
    if not p.tickers:
        draft.reason = "The question does not name a company. Which of the covered companies do you mean?"
        draft.clarification_options = [c.name for c in COMPANIES]
    elif p.ambiguity:
        draft.reason = f"{p.ambiguity} Which one do you mean?"
        draft.clarification_options = p.clarification_options
    else:
        draft.reason = f"Which measure should {join_names([short_name(t) for t in p.tickers])} be compared on?"
        draft.clarification_options = [m.label for m in METRICS]
    return {"draft": draft}


def node_refuse(state: State) -> State:
    p = state["parsed"]
    reason = p.refusal_reason
    if not reason and p.unknown_names:
        reason = (
            f"{join_names(p.unknown_names[:3])} {'is' if len(p.unknown_names) == 1 else 'are'} not among the "
            f"{len(COMPANIES)} companies whose filings the system holds, so it has no evidence to answer from."
        )
    return {"draft": Draft(route=Route.refuse, status=Status.refused, reason=reason)}


def node_finalise(state: State) -> State:
    draft = state["draft"]
    route = draft.route if draft.route != Route.sql or state["route"] in (Route.sql, Route.refuse) else state["route"]
    if draft.route == Route.refuse:
        route = Route.refuse
    if draft.status in (Status.refused, Status.ambiguous) or (draft.status and not draft.segments):
        answer_text = draft.reason or ""
        if draft.route == Route.evidence and draft.segments:
            answer_text = (draft.reason or "") + "\n" + "\n".join(s.text for s in draft.segments)
        return {
            "answer": Answer(
                answer_id=str(uuid.uuid4()),
                question=state["question"],
                status=draft.status,
                route=route,
                answer=answer_text,
                segments=draft.segments,
                citations=draft.book.citations,
                calculations=draft.calculations,
                clarification_options=draft.clarification_options,
                reason=draft.reason,
            )
        }
    if draft.route == Route.evidence:
        return {
            "answer": Answer(
                answer_id=str(uuid.uuid4()),
                question=state["question"],
                status=draft.status or Status.verified,
                route=Route.evidence,
                answer=(draft.reason or "") + "\n" + "\n".join(s.text for s in draft.segments),
                segments=draft.segments,
                citations=draft.book.citations,
                calculations=draft.calculations,
                reason=draft.reason,
            )
        }
    checks = run_checks(draft)
    status = draft.status or status_from(checks)
    narrator = "template"
    reason: str | None
    if status == Status.insufficient_evidence and draft.status is None:
        answer_text = "The system drafted an answer it could not fully verify against the filings, so it is not shown."
        reason = "; ".join(c.detail for c in checks if not c.passed)
    else:
        answer_text, narrator = narrate(draft)
        reason = draft.reason
    return {
        "answer": Answer(
            answer_id=str(uuid.uuid4()),
            question=state["question"],
            status=status,
            route=route,
            answer=answer_text if status != Status.insufficient_evidence or draft.status is None else (draft.reason or ""),
            segments=draft.segments,
            citations=draft.book.citations,
            calculations=draft.calculations,
            checks=checks,
            table=draft.table,
            reason=reason,
            narrator=narrator,
            sql=draft.sql,
        )
    }


_TARGET = {
    Route.sql: "structured",
    Route.sql_calculation: "structured",
    Route.comparison: "structured",
    Route.document: "document",
    Route.sql_and_document: "structured_document",
    Route.evidence: "evidence",
    Route.clarify: "clarify",
    Route.refuse: "refuse",
}


def build_graph():  # type: ignore[no-untyped-def]
    from langgraph.graph import END, StateGraph

    g = StateGraph(State)
    g.add_node("parse", node_parse)
    g.add_node("route", node_route)
    g.add_node("structured", node_structured)
    g.add_node("document", node_document)
    g.add_node("structured_document", node_structured_document)
    g.add_node("evidence", node_evidence)
    g.add_node("clarify", node_clarify)
    g.add_node("refuse", node_refuse)
    g.add_node("finalise", node_finalise)
    g.set_entry_point("parse")
    g.add_edge("parse", "route")
    g.add_conditional_edges("route", lambda s: _TARGET[s["route"]], {v: v for v in set(_TARGET.values())})
    for name in set(_TARGET.values()):
        g.add_edge(name, "finalise")
    g.add_edge("finalise", END)
    return g.compile()


_NODES = {
    "structured": node_structured,
    "document": node_document,
    "structured_document": node_structured_document,
    "evidence": node_evidence,
    "clarify": node_clarify,
    "refuse": node_refuse,
}


class _Sequential:
    """The same graph walked by hand, for environments without LangGraph (the browser build).

    The graph has one branch point and no cycles, so walking it is four function
    calls; the node functions are the ones LangGraph runs.
    """

    def invoke(self, state: State) -> State:
        for step in (node_parse, node_route):
            state = {**state, **step(state)}
        state = {**state, **_NODES[_TARGET[state["route"]]](state)}
        return {**state, **node_finalise(state)}


_GRAPH: Any = None


def _graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        try:
            _GRAPH = build_graph()
        except ImportError:
            _GRAPH = _Sequential()
    return _GRAPH


def ask(question: str, previous_answer_id: str | None = None, persist: bool = True) -> Answer:
    started = time.perf_counter()
    result = _graph().invoke({"question": question, "previous_answer_id": previous_answer_id})
    answer: Answer = result["answer"]
    answer.latency_ms = int((time.perf_counter() - started) * 1000)
    if persist:
        backend.get().save_answer(
            answer.answer_id, question, answer.route.value, answer.status.value, answer.latency_ms, answer.model_dump_json()
        )
    log.info("answer", route=answer.route.value, status=answer.status.value, latency_ms=answer.latency_ms)
    return answer


__all__ = ["Citation", "ask", "build_graph", "decide_route"]
