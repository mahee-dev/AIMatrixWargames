"""Build the story cards: one self-contained 1200×675 page per screen, in SCQA order.

1. Question → Answer   2. The background   3. Behind closed doors (when the story has quotes)
4. How it happened     5. How this game was played (counted data only)

Every page carries the "AI SIMULATION" badge, because scenarios name real people and no card may
read as news, and the AI Matrix Wargames footer (#18, D-121). Pages need no network to render.
All story text is HTML-escaped; the narrator's words never become markup.
"""

from __future__ import annotations

from html import escape as E
from importlib import resources

from aar.facts import RunFacts, UsageRow
from aar.model import AfterActionReport
from aar.story import Story

REPO_URL = "github.com/mahee-dev/AIMatrixWargames"
TAGLINE = "AI agents negotiate, scheme and strike deals"
CATEGORICAL_SLOTS = 8
SMALL_CAST = 4
DENSE_CAST = 6
SHORT_LABEL_OVER = 20


def _css() -> str:
    return resources.files("aar").joinpath("templates/card.css").read_text(encoding="utf-8")


class _Deck:
    """Shared page furniture: Actor colours and names, the top bar, the footer."""

    def __init__(self, story: Story, facts: RunFacts, report: AfterActionReport) -> None:
        self.story, self.facts, self.report = story, facts, report
        self.order = [a.id for a in report.actors]
        self.entries = {a.id: a for a in story.actors}
        self.css = _css()

    def colour(self, actor_id: str) -> str:
        slot = self.order.index(actor_id) if actor_id in self.order else CATEGORICAL_SLOTS
        # Colours follow the Actor in fixed order and are never cycled (dataviz rule).
        return f"var(--a{slot})" if slot < CATEGORICAL_SLOTS else "var(--a-none)"

    def short(self, actor_id: str) -> str:
        entry = self.entries.get(actor_id)
        return entry.short if entry else self.report.actor_name(actor_id)

    def role(self, actor_id: str) -> str:
        entry = self.entries.get(actor_id)
        return entry.role if entry else self.report.actor_name(actor_id)

    def tag(self, actor_id: str) -> str:
        return (f'<span class="tag"><i class="dot" style="background:{self.colour(actor_id)}"></i>'
                f"{E(self.short(actor_id))}</span>")

    def page(self, n: int, total: int, kicker: str, body: str, *, credit: str, extra: str = "") -> str:
        return (
            f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=1200, height=675"><title>{E(kicker)}</title>'
            f"<style>{self.css}</style></head><body class=\"{extra}\"><div class=\"card\">"
            f'<div class="top in" style="animation-delay:0s"><span class="badge">AI SIMULATION</span>'
            f'<span class="kicker">{E(kicker)}</span><span class="credit">{E(credit)}</span>'
            f'<span class="step"><b>{n}</b> / {total}</span></div>{body}'
            f'<footer><span class="mark"><i></i>AI MATRIX WARGAMES</span><span>{E(TAGLINE)}</span>'
            f'<span class="url">{E(REPO_URL)}</span></footer></div></body></html>'
        )


def _delay(seconds: float) -> str:
    return f' style="animation-delay:{seconds:.2f}s"'


def _answer(deck: _Deck) -> str:
    story, facts = deck.story, deck.facts
    r = story.result
    tiles = [(f.value, f.label) for f in r.facts[:2]]
    secret = facts.secret_messages
    tiles.append((f"{secret} secret message{'' if secret == 1 else 's'}",
                  f"between the AI agents in {facts.turns or deck.report.turns_played} turns"))
    names = {a.id: a.short for a in story.actors}
    credit = facts.model_credit(names, story.actor_noun)
    fig = r.figure
    return (
        f'<div class="q in"{_delay(.2)}>{E(story.background.question)}</div>'
        f'<h1 class="ans in"{_delay(.45)}>{E(r.headline)}</h1>'
        f'<div class="dek in"{_delay(.8)}>{E(r.dek)}</div>'
        f'<div class="res"><div class="fig in"{_delay(1.2)}><div class="big">{E(fig.value)}'
        f'<small>{E(fig.unit)}</small></div><div class="figlabel">{E(fig.label)}</div></div>'
        f'<div class="facts">'
        + "".join(f'<div class="tile in"{_delay(1.6 + i * .25)}><b>{E(v)}</b><span>{E(l)}</span></div>'
                  for i, (v, l) in enumerate(tiles))
        + "</div></div>"
        + (f'<div class="played in"{_delay(2.6)}>{E(credit)}</div>' if credit else "")
    )


def _background(deck: _Deck) -> str:
    b = deck.story.background
    blocks = [("Situation", b.situation, "")]
    if b.complication:
        blocks.append(("Complication", b.complication, ""))
    blocks.append(("The question", b.question, " qq"))
    left = "".join(f'<div class="blk{cls} in"{_delay(.3 + i * .5)}><div class="lbl">{label}</div><p>{E(text)}</p></div>'
                   for i, (label, text, cls) in enumerate(blocks))
    rows = ""
    for i, entry in enumerate(deck.story.actors):
        number = f'<span class="num">{E(entry.number)}</span>' if entry.number else ""
        rows += (f'<div class="who in"{_delay(.6 + i * .12)}><i class="dot" style="background:{deck.colour(entry.id)}"></i>'
                 f'<div><div class="nm">{E(entry.role)}{number}</div><p>{E(entry.wants)}</p></div></div>')
    return (f'<div class="bg"><div class="scq">{left}</div>'
            f'<div class="table"><div class="lbl">At the table</div>{rows}</div></div>')


def _closed_doors(deck: _Deck) -> str:
    doors = deck.story.closed_doors
    assert doors is not None
    messages = {m.id: m for t in deck.report.turns for m in t.messages}
    bubbles, first = "", None
    for i, quote in enumerate(doors.quotes):
        message = messages.get(quote.message_id)
        sender = message.sender_actor_id if message else ""
        first = first or sender
        to = ", ".join(deck.short(r) for r in (message.recipients if message else []))
        turn = f" · turn {message.turn}" if message else ""
        translated = '<span class="tr">translated</span>' if quote.translated else ""
        bubbles += (
            f'<div class="msg{" reply" if sender != first else ""} in"{_delay(.5 + i * .9)}><div class="head">'
            f'<i class="dot" style="background:{deck.colour(sender)}"></i><b>{E(deck.short(sender))} → {E(to)}</b>'
            f'<span class="lock">🔒 private{turn}</span>{translated}</div><q>{E(quote.text)}</q></div>'
        )
    side = ""
    if doors.public:
        side += (f'<div class="public in"{_delay(2.3)}><div class="lbl">Meanwhile, in public</div>'
                 f"<p>{E(doors.public)}</p></div>")
    if doors.payoff:
        side += (f'<div class="payoff in"{_delay(2.9)}><div class="lbl">{E(doors.payoff.when)}</div>'
                 f"<p>{E(doors.payoff.text)}</p></div>")
    return (f'<h1 class="in"{_delay(.15)}>{E(doors.title)}</h1>'
            f'<div class="doors"><div class="thread">{bubbles}</div><aside>{side}</aside></div>')


def _developments(deck: _Deck) -> str:
    d = deck.story.developments
    pins = {"win": ("win", "✓"), "fail": ("fail", "✗")}
    cells = ""
    for i, m in enumerate(d.moments, 1):
        cls, glyph = pins.get(m.tone or "", ("", str(i)))
        cells += (f'<div class="moment {cls} in"{_delay(.4 + (i - 1) * .55)}><div class="pin">{glyph}</div>'
                  f'<div class="when">{E(m.when)}</div><h3>{E(m.title)}</h3><p>{E(m.text)}</p>'
                  f'<div class="tags">{"".join(deck.tag(a) for a in m.actor_ids[:2])}</div></div>')
    return (f'<h1 class="in"{_delay(.15)}>{E(d.title)}</h1>'
            f'<div class="line" style="grid-template-columns:repeat({len(d.moments)},1fr)">{cells}</div>')


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:.2f}" if value >= 0.01 else "<$0.01"


def _tokens(value: int) -> str:
    return f"{value / 1e6:.1f}M" if value >= 1e6 else f"{value / 1e3:.0f}k" if value >= 1e3 else str(value)


def _run(deck: _Deck) -> str:
    facts = deck.facts

    def label(actor_id: str) -> str:
        role = deck.role(actor_id)
        return role if len(role) <= SHORT_LABEL_OVER else deck.short(actor_id)

    def row(name: str, colour: str, usage: UsageRow, messages: str, cls: str = "") -> str:
        return (f'<tr class="{cls}"><td><i class="dot" style="background:{colour}"></i>{E(name)}</td>'
                f"<td>{E(usage.model)}</td><td>{messages}</td><td>{usage.calls}</td>"
                f"<td>{_tokens(usage.tokens)}</td><td>{_money(usage.cost)}</td></tr>")

    rows = ""
    for entry in deck.story.actors:
        usage = facts.row(entry.id)
        rows += row(label(entry.id), deck.colour(entry.id), usage,
                    f'{usage.messages} <span class="dim">({usage.secret} secret)</span>')
    for key, name, does in (("umpire", "Umpire", "rules on moves"), ("consequence", "Consequences", "knock-on effects")):
        if key in facts.rows:
            rows += row(name, "var(--brand)", facts.rows[key], f'<span class="dim">{does}</span>', "ump")

    if facts.cost is None:
        cost, note = "—", "not recorded"
    else:
        cost = _money(facts.cost) + ("+" if facts.cost_partial else "")
        note = (f"reported for {facts.costed_calls} of {facts.total_calls} calls" if facts.cost_partial
                else "estimated, all calls")
    runtime = f"{facts.runtime_hours:.1f} h" if facts.runtime_hours is not None else "—"
    tiles = [
        (cost, f"total cost · {note}"),
        (_tokens(facts.tokens), f"tokens · {_tokens(facts.reasoning_tokens)} of them reasoning"),
        (runtime, f"runtime · {facts.model_calls} model calls"),
        (f"{facts.granted} / {facts.adjudicated}", f"Arguments granted · {facts.rolled} decided by dice"),
    ]
    tile_html = "".join(f'<div class="st in"{_delay(.8 + i * .2)}><b>{E(v)}</b><span>{E(t)}</span></div>'
                        for i, (v, t) in enumerate(tiles))
    repro = [f"Scenario {facts.scenario_id}" + (f" v{facts.scenario_version}" if facts.scenario_version else "")]
    if facts.seed is not None:
        repro.append(f"seed {facts.seed}")
    repro += [f"{k} prompt {v}" for k, v in facts.prompt_versions.items()]
    return (
        f'<h1 class="in"{_delay(.15)}>How this game was played</h1><div class="run">'
        f'<table class="in"{_delay(.45)}><colgroup><col style="width:29%"><col style="width:23%">'
        '<col style="width:20%"><col style="width:8%"><col style="width:10%"><col style="width:10%"></colgroup>'
        "<thead><tr><th>Role</th><th>Model</th><th>Messages</th><th>Calls</th><th>Tokens</th><th>Cost</th>"
        f'</tr></thead><tbody>{rows}</tbody></table><div class="tiles">{tile_html}</div></div>'
        f'<div class="repro">{E(" · ".join(repro))}</div>'
    )


def render_screens(story: Story, facts: RunFacts, report: AfterActionReport) -> list[tuple[str, str]]:
    """``(name, html)`` per screen, in order; the closed-doors screen only when there are quotes."""
    deck = _Deck(story, facts, report)
    plan: list[tuple[str, str, str]] = [
        ("answer", story.kicker, _answer(deck)),
        ("background", "The background", _background(deck)),
    ]
    if story.closed_doors is not None:
        plan.append(("closed-doors", "Behind closed doors", _closed_doors(deck)))
    plan += [("developments", "How it happened", _developments(deck)),
             ("run", "The run", _run(deck))]
    written = "Story written by AI from the game record" if story.narrated else "Built from the game record"
    dense = "dense" if len(story.actors) > DENSE_CAST else ""
    long_page = "long-page" if len(story.result.headline) > 34 else ""
    total = len(plan)
    return [
        (name, deck.page(n, total, kicker, body,
                         credit="From the game record" if name == "run" else written,
                         extra=" ".join(c for c in (dense, long_page if name == "answer" else "") if c)))
        for n, (name, kicker, body) in enumerate(plan, 1)
    ]
