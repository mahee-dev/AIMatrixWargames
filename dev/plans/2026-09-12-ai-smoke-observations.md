# AI Smoke Observations — Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the real defects behind the 13 observations from the first provider-backed T-044 run, and record which observations were log misreadings so they are not "fixed" twice.

**Architecture:** Every change stays inside the agent layer (`agents/`, `docs/prompts/`, the smoke test) except one decision-gated server change (Umpire hidden-information policy, Task 7). `app/` keeps importing no agent framework; the server keeps owning randomness and visibility. Prompt changes are versioned files (`agents.md §31` — never edit a committed version in place), each recorded in `dev/DECISIONS.md` and committed separately from code.

**Tech Stack:** Python 3.12, PydanticAI 2.42 `FunctionModel` fakes (`tests/agent_fakes.py`), pytest, FastAPI `TestClient`. All new tests run without an OpenRouter key.

**Process fit:** This is Gate-2 remediation on the open T-044 branch, like the I-004…I-014 batch. Task 0 files the real findings as backlog items so `/task-done T-044` can cite them. The plan lives in `dev/plans/` because `docs/` is the read-only spec layer (AGENTS.md §2).

**Revision 2 (2026-09-12):** after external review. Adds I-021 (the cap is not atomic under PydanticAI's concurrent tool execution — the actual cause of the two-Message activation), moves the Umpire hidden-information question to Task 0 so `umpire-v1.2` is issued once, flips the Task 7 recommendation to symmetric facts + a leak test, labels rotation as mitigation, strikes `temperature` as a cause, and makes the I-019 calibration verdict conditional on ≥3 runs.

---

## Part A — Triage (verified against code, 2026-09-12)

| # | Observation | Verdict | Evidence | Fix |
|---|---|---|---|---|
| 1 | Message budget mismatch | **Real — two defects** | (a) The smoke cap was `1` during the run (`b5eeb1a`, 10:47 local; the log's 08:55 is UTC), yet Arkania's *first* turn-2 diplomacy activation sent two Messages. PydanticAI 2.42 runs all tool calls from one model response **concurrently** (`pydantic_ai/_function_schema.py:89` — sync tools via `run_in_executor`); the `send_message` closure at [agents/player.py:462-486](../../agents/player.py) does *check counter → HTTP → increment*, so two calls in one response both pass the check. The cap is not atomic. (b) The packet never states the remaining budget ([lifecycle.py:213-215](../../agents/lifecycle.py) sets it; [player.py:build_packet](../../agents/player.py) omits it). Arkania's *second* activation (ready cleared by an incoming private Message — TEST-PHASE-003) started at budget 0 with no way to know. | Task 2 (atomic cap), Task 3 (visible budget) |
| 2 | Duplicate tool entries | **Misreading** | The `tools=` field in [autonomous.py:194](../../agents/autonomous.py) logs `activation.tool_call_names` — the tool **calls the model made**, in order. `send_message,send_message` = two calls. No tool is ever listed twice as a grant. | Task 1 |
| 3 | Inconsistent tool grants | **Misreading** | Same field. Every player gets the full `_BASELINE_TOOL_NAMES` (9 tools, [player.py:70-80](../../agents/player.py)), asserted by `test_only_player_safe_tools_are_exposed`. "Only the first actor gets observe/read_messages/get_notes" = only that actor *chose to call* them (the packet already contains all three). `send_message` in the argument phase is legal ([context.py:178](../../app/context.py)). | Task 1 |
| 4 | Cestia copies Belvar | **Real (design)** | `actor_order = list(runners)` ([autonomous.py:139](../../agents/autonomous.py)) sweeps in fixed seat order every phase; each later activation's packet includes earlier actors' same-phase Messages. **Confound, not a cause:** the smoke runs at `temperature=0.0`; it is held constant in this batch so any change in copying is attributable to Tasks 4/5. | Task 4 (rotation — mitigation) + Task 5 (prompt: own words) |
| 5 | Private information never used | **Real (prompt)** — injection is fine | `you.private_brief/objectives/private_information` are in every packet ([context.py:92-101](../../app/context.py), `test_private_context_is_not_mixed_across_players`). The prompt lists private information as something you "know", never as something to *use*. Separately, the Umpire never receives hidden facts — `relevant_hidden_information=[]` is hard-coded at [game_service.py:1346](../../app/game_service.py) — so a private fact cannot influence adjudication even when an Actor argues from it. | Task 5 (prompt) + Task 7 (decision-gated) |
| 6 | Resources unread | **Planned, not a bug** | Resource search/read/tools are T-045…T-050 (WBS §4). [player.py:67-69](../../agents/player.py) documents their deliberate absence. | None — continue the WBS |
| 7 | Cons written as impartial critique | **Real (prompt)** | `player-v1.1.md` §Game procedure: "submit a Con only when you have a substantive reason that the requested Effect may fail" — referee framing, nothing about interest. | Task 5 |
| 8 | Probabilities clustered | **Insufficient data** (n=3) | `umpire-v1.1.md` already carries the `agents.md §25` anchors. | Task 6 logs the distribution; verdict after ≥3 runs (Part C) |
| 9 | Nothing forces conflict | **Scenario design, out of scope** | The fixture is deliberately minimal (testing.md §4). A richer scenario is **T-065** (WBS 6.4). | Backlog note only (Task 0) |
| 10 | Determinism claim | **Not a defect, docs unclear** | The claim covers ScriptedPlayers + FakeUmpire (`tests/test_integration_six_turn.py`). `benchmark-methodology §8`: "Seed controls server stochasticity only. It does not guarantee identical LLM outputs." | Task 9 |
| 11 | Supersession inconsistent | **Working as specified** | Adjudicator-chosen per `agents.md §22`; the response schema enum restricts ids to active Effects ([umpire.py:290-301](../../agents/umpire.py)); the server validates unknown/foreign/duplicate/inactive ids ([game_service.py:1397-1416](../../app/game_service.py)). Contradiction *detection* is Umpire quality. | Task 6 (prompt) |
| 12 | Both branches written before roll | **Verified safe** | `AdjudicationContext` has no roll field ([umpire.py:42-57](../../app/umpire.py)); the Umpire is called before the draw; the schema has no roll/branch field. Both-branch Effects are the `agents.md §22` contract. | Task 6 (prompt: failure cost) |
| 13 | Logs truncated | **Tooling** | The audit stream exists (`stream_game_details=True`) but only in pytest's captured log. Nothing writes the export to disk. | Task 8 |

**Not touched by this plan:** anything in `app/` other than the Task 7 context builder; the fixture `scenario.yaml` (golden file); the I-004…I-014 items (separate batch, already on this branch); the smoke `temperature`.

---

## Part B — Tasks

Run order: **0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9**. Task 0 asks the one question Task 7 needs, so Task 6 can issue `umpire-v1.2` once. Each task: red → green → full suite → commit, on `task/T-044-ai-smoke-test`.

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q
```

---

### Task 0: File the findings and settle the Umpire question

**Files:**
- Modify: `dev/BACKLOG.md` (append under `## Open`, after I-014)
- Modify: `dev/tasks/T-044-ai-smoke-test.md` (append a section)

- [x] **Step 1: Umpire hidden-information question — DECIDED 2026-09-12: option 1 (all Actors' facts)**

`agents.md §21` names `relevant_hidden_information` as a server-built Umpire input but defines no policy; the server sends `[]`. Adjudicated Effects are public ([game_service.py:130-132](../../app/game_service.py)), so whatever the Umpire learns can leak through an Effect unless the prompt forbids it and a test checks it. Options:

1. **All Actors' private information and private Effects (recommended)** — symmetric: every Actor's claims are bounded by its own facts on every Argument, and a Con can be weighed against its author's facts too. A per-arguing-Actor policy would handicap whoever is arguing (Arkania's "two turns" bounds Arkania; Belvar's "limited domestic support" never bounds Belvar). Requires the no-leak rule in `umpire-v1.2` **and** the leak test in Task 7.
2. **Arguing Actor's facts only** — narrower exposure, asymmetric as above.
3. **Keep empty** — defer past Gate 2; record in `DECISIONS.md` that the §21 field is unused in the lean MVP.

Decided: **option 1**. Task 6 includes the no-leak paragraph; Task 7 is executed (not skipped).

- [x] **Step 2: Append backlog items**

Append to `dev/BACKLOG.md` after the I-014 entry:

```markdown
- [ ] **I-015** Decision packet does not state the remaining per-phase Message budget — *severity: medium*

  The runner hands `ReferencePlayer` a shrinking `(turn, phase)` Message budget
  (`agents/lifecycle.py`) but `build_packet` never exposes it, so an Actor re-activated after
  its ready state was cleared (TEST-PHASE-003) starts with budget 0 and only discovers it when
  `send_message` is refused. Seen in the first provider-backed T-044 run (Arkania, turn 2). Add a
  `limits` block to the packet and a framing sentence; test that the number tracks the runner.

- [ ] **I-016** Autonomous sweep order is fixed, anchoring later Actors on earlier same-phase text — *severity: medium*

  `AutonomousGameRunner` activates Actors in seat order every phase. In diplomacy the third Actor
  always reads the first two Actors' Messages before writing; the first run produced a public
  statement reproduced near-verbatim from the previous Actor. Rotating the sweep start by turn
  spreads which seat is anchored (mitigation, not elimination — sequential activation is
  inherent to letting Actors respond within a phase); a prompt instruction asks for the Actor's
  own words.

- [ ] **I-017** Player prompt v1.1 frames Cons as impartial critique and never says private information is for use — *severity: medium*

  In the first provider-backed run no Actor used `private_information`, `private_brief` detail or
  a private Effect in any Argument, Message or Note, and one Actor conned an Argument against its
  own interest. Issue `player-v1.2.md` (never edit v1.1 — `agents.md §31`), record the change in
  `DECISIONS.md`, and update the `agents.md §31` file list.

- [ ] **I-018** Umpire never receives hidden information — *severity: medium* · *decision recorded as D-029*

  `_build_umpire_context` hard-codes `relevant_hidden_information=[]`
  (`app/game_service.py`) while `agents.md §21` lists "relevant hidden facts authorised for
  adjudication" as an input. Adjudicated Effects are public, so any policy needs a no-leak rule in
  the Umpire prompt and a test over Effect descriptions and rationales.

- [ ] **I-019** Umpire prompt v1.1 lacks contradiction-supersession and failure-cost guidance; probability distribution is not summarised — *severity: low*

  Adjudicated Effects can accumulate alongside contradicting active Effects because the prompt
  only says to supersede when a new Effect "clearly replaces" one. Failure Effects in the first
  run were softer than success Effects. Only three probabilities were observed (0.35–0.45).
  Issue `umpire-v1.2.md`; have the smoke run log the probability list. **Calibration verdict
  requires ≥3 provider-backed runs on v1.2** — one run is one sample of the prompt.

- [ ] **I-020** The smoke run's export and activation provenance are not written to disk — *severity: low*

  Post-run analysis of the first provider-backed T-044 run worked from a truncated pytest log.
  Write `export.json`, `activations.json` and `failures.json` to a directory named by
  `AMAW_SMOKE_EXPORT_DIR` when set.

- [ ] **I-021** Per-phase Message cap is not atomic under concurrent tool execution — *severity: high*

  PydanticAI executes every tool call in one model response concurrently (sync tools via
  `run_in_executor`). `ReferencePlayer`'s `send_message` closure checks the counter, performs
  the HTTP send, then increments, so two `send_message` calls emitted together both pass a cap
  of 1. Observed in the first provider-backed run (Arkania, turn 2: two Messages at the same
  second under `max_messages_per_phase=1`). Serialise check→send→count under a lock; test with a
  model that emits two `send_message` calls in one response.
```

- [x] **Step 3: Record the triage in the task brief**

Append to `dev/tasks/T-044-ai-smoke-test.md`:

```markdown
### 2026-09-12 first provider-backed run — observation triage

An external log review raised 13 observations; `dev/plans/2026-09-12-ai-smoke-observations.md`
verifies each against the code. Real defects filed as I-015…I-021 and fixed on this branch
before the second provider-backed run. Observations 2, 3 (the `tools=` log field lists tool
*calls*, not grants), 6 (Resources are T-045…T-050), 9 (scenario richness is T-065), 11 and 12
(supersession and pre-roll branch Effects work as specified) require no code change. The
Umpire hidden-information policy (I-018) was decided as D-029: the server passes every Actor's
private information and every active private Effect to the Umpire, tagged by owner.
```

- [x] **Step 4: Commit**

```bash
git add dev/BACKLOG.md dev/tasks/T-044-ai-smoke-test.md dev/plans/2026-09-12-ai-smoke-observations.md
git commit -m "T-044 file I-015-I-021 from first provider-backed run

Triage of the 13 log-review observations: seven real defects filed, six
recorded as misreadings or planned work. Plan in dev/plans/."
```

---

### Task 1: Make the activation log unambiguous (obs 2/3)

**Files:**
- Modify: `agents/autonomous.py:118-124` (after runners are built) and `:189-195` (activation log)
- Test: `tests/test_ai_smoke.py::test_autonomous_coordinator_completes_six_turns_offline`

- [x] **Step 1: Write the failing assertions**

Inside `test_autonomous_coordinator_completes_six_turns_offline`, after `audit = "\n".join(...)`:

```python
    # The per-activation log names tool *calls* (a repeated name is a repeated call) and the tool
    # *grant* is logged once per Actor, so the two can never be confused again.
    assert " tools=" not in audit
    assert "tool_calls=" in audit
    for actor in ACTORS:
        offered = re.search(rf"autonomous actor={actor} tools_offered=(\S+)", audit)
        assert offered is not None, actor
        assert set(offered.group(1).split(",")) == {
            "observe", "read_messages", "send_message", "make_argument", "make_con",
            "pass_con", "get_notes", "save_note", "ready",
        }
```

Add `import re` to the test module imports.

- [x] **Step 2: Run to verify it fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_smoke.py -m 'not llm' -k completes_six_turns_offline
```
Expected: FAIL on `assert " tools=" not in audit`.

- [x] **Step 3: Implement**

In `agents/autonomous.py::run()`, after `runners[actor_id] = self._player_factory(tools, actor_id, actor_name)`:

```python
            logger.info(
                "autonomous actor=%s tools_offered=%s",
                actor_id,
                ",".join(sorted(runners[actor_id].player.tool_names)),
            )
```

Replace the activation log call:

```python
                    logger.info(
                        "autonomous activation actor=%s turn=%s phase=%s tool_calls=%s",
                        actor_id,
                        outcome.context.get("turn"),
                        outcome.phase,
                        ",".join(activation.tool_call_names) or "none",
                    )
```

- [x] **Step 4: Run to verify it passes** — same command. Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add agents/autonomous.py tests/test_ai_smoke.py
git commit -m "T-044 log tool grant once and name tool calls explicitly

The activation log's tools= field listed the model's tool calls in order,
which reads as a duplicated/uneven tool grant. Rename to tool_calls= and log
tools_offered= once per Actor."
```

---

### Task 2: Make the per-phase Message cap atomic (I-021)

**Files:**
- Modify: `agents/player.py` — imports, `ReferencePlayer.__init__`, the `send_message` closure in `_build_agent`
- Test: `tests/test_guardrails.py`

- [x] **Step 0: Confirm names**

```bash
grep -n "_message_budget\|_messages_sent_this_activation\|_successful_message_keys\|def set_message_budget" agents/player.py
```
Expected: definitions in `__init__` (~lines 363-368), use in the `send_message` closure (~462-486), `set_message_budget` (~554).

- [x] **Step 1: Write the failing test**

Add to `tests/test_guardrails.py` after `test_per_phase_message_cap_stops_further_sends_across_activations`:

```python
def test_message_cap_holds_when_one_response_emits_two_sends(db_engine: Engine) -> None:
    """Two send_message calls in ONE model response must not both pass a cap of 1 (I-021).

    PydanticAI executes the tool calls of a single response concurrently, so a non-atomic
    check→send→count lets both through. Pre-fix this fails (2 Messages reach the server) on
    practically every run; post-fix it passes deterministically because the lock serialises
    the whole send.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def two_sends(packet, tool_names, idx):
        if idx == 0:
            return [
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "first"},
                ),
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "second"},
                ),
            ]
        return [TextPart(content="done")]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(two_sends),
        guardrails=GuardrailConfig(max_messages_per_phase=1, max_activations_per_opportunity=50),
    )
    outcome = runner.step()

    msgs = runner.player.tools.read_messages().data["messages"]
    mine = [m for m in msgs if m.get("sender_actor_id") == "arkania"]
    assert len(mine) == 1
    assert outcome.activation is not None and outcome.activation.messages_sent == 1
```

- [x] **Step 2: Run to verify it fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_guardrails.py -k one_response_emits_two_sends
```
Expected: FAIL with `assert 2 == 1`. (If it passes by timing luck, run it 5× with `-p no:randomly --count` unavailable — just re-run; it must fail at least once to count as red. Do not proceed on a green pre-fix run without seeing a red one.)

- [x] **Step 3: Implement**

In `agents/player.py` add `import threading` to the imports. In `ReferencePlayer.__init__`, after `self._successful_message_keys: set[str] = set()`:

```python
        # PydanticAI runs the tool calls of one model response concurrently (sync tools in an
        # executor). The per-phase Message cap is check→send→count, so without serialisation two
        # send_message calls in one response both pass a cap of 1 (I-021). One lock around the
        # whole send keeps the count exact; Messages are rare, so the serialisation is free.
        self._send_lock = threading.Lock()
```

In the `send_message` closure, wrap everything from the cap check to the return in the lock (the guardrail size checks stay outside — they are pure):

```python
        @agent.tool_plain
        def send_message(recipients: list[str], public: bool, text: str) -> dict[str, Any]:
            """Send a public (recipients=[]) or private Message to named recipients."""
            args = {"recipients": recipients, "public": public, "text": text}
            violation = check_message(text, self._guardrails) or check_payload(
                args, self._guardrails
            )
            if violation is not None:
                return violation
            with self._send_lock:
                # Per-phase message cap (agents.md §18): refuse once the runner's budget is spent.
                # Check, send and count happen under one lock (I-021).
                if (
                    self._message_budget is not None
                    and self._messages_sent_this_activation >= self._message_budget
                ):
                    return _guardrail_error(
                        "guardrail_message_cap_reached",
                        "the per-phase message cap has been reached; do not send more this phase",
                        limit=self._guardrails.max_messages_per_phase,
                    )
                key = mutation_key("send_message", args)
                result = self._tools.send_message(
                    recipients=recipients, public=public, text=text, idempotency_key=key
                )
                if result.ok and key not in self._successful_message_keys:
                    self._successful_message_keys.add(key)
                    self._messages_sent_this_activation += 1
                return _payload(result)
```

- [x] **Step 4: Run to verify it passes** — same command, then 3 more times. Expected: PASS every time.

- [x] **Step 5: Run the guardrail/player neighbours**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_guardrails.py tests/test_player.py tests/test_lifecycle.py
```
Expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add agents/player.py tests/test_guardrails.py
git commit -m "T-044 I-021 serialise the Message cap under concurrent tool execution

PydanticAI runs a response's tool calls concurrently; check→send→count was
not atomic, so two send_message calls in one response both passed a cap
of 1 (seen in the first provider-backed run). One lock around the send."
```

---

### Task 3: Expose the remaining Message budget in the packet (I-015)

**Files:**
- Modify: `agents/player.py` — `build_packet`, `build_activation_prompt`, new `_packet_limits`
- Modify: `tests/test_ai_smoke.py:233-244` (smoke guardrails — Step 7)
- Test: `tests/test_player.py`, `tests/test_guardrails.py`

- [x] **Step 0: Confirm names**

```bash
grep -n "def set_message_budget\|self._message_budget\b\|self._guardrails = \|def build_packet\|def build_activation_prompt" agents/player.py
```
Expected: all five present.

- [x] **Step 1: Write the failing packet test**

Add to `tests/test_player.py` after `test_activation_framing_names_server_required_completion_action`. Add `GuardrailConfig` and `ACTIVATION_PACKET_MARKER` to the `from agents.player import (...)` list.

```python
def test_packet_states_remaining_message_budget(db_engine: Engine) -> None:
    """The runner's per-phase Message budget is visible to the model before it acts (I-015)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(default_policy),
        config=PlayerConfig(
            model="fake/deterministic", guardrails=GuardrailConfig(max_messages_per_phase=2)
        ),
    )

    p.set_message_budget(2)
    packet = p.build_packet()
    assert packet["limits"]["max_messages_per_phase"] == 2
    assert packet["limits"]["messages_remaining_this_phase"] == 2
    assert "at most 2 more Messages this phase" in p.build_activation_prompt(packet)

    p.set_message_budget(1)
    packet = p.build_packet()
    assert "at most 1 more Message this phase" in p.build_activation_prompt(packet)

    p.set_message_budget(0)
    packet = p.build_packet()
    assert packet["limits"]["messages_remaining_this_phase"] == 0
    assert "cannot send any more Messages this phase" in p.build_activation_prompt(packet)

    p.set_message_budget(None)  # direct use with no runner: unbounded, so no number is claimed
    packet = p.build_packet()
    assert "messages_remaining_this_phase" not in packet["limits"]
    framing = p.build_activation_prompt(packet).split(ACTIVATION_PACKET_MARKER)[0]
    assert "more Message" not in framing
    assert "cannot send any more Messages" not in framing
```

- [x] **Step 2: Write the failing cross-activation test**

Add to `tests/test_guardrails.py` after the Task 2 test:

```python
def test_remaining_message_budget_in_packet_tracks_runner(db_engine: Engine) -> None:
    """Across activations the packet's remaining budget counts down with the runner (I-015)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen: list[int] = []

    def send_one(packet, tool_names, idx):
        if idx == 0:
            seen.append(packet["limits"]["messages_remaining_this_phase"])
            if "send_message" in packet.get("available_actions", []):
                return [
                    ToolCallPart(
                        tool_name="send_message",
                        args={"recipients": [], "public": True, "text": "hello"},
                    )
                ]
        return [TextPart(content="done")]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(send_one),
        guardrails=GuardrailConfig(max_messages_per_phase=2, max_activations_per_opportunity=50),
    )
    for _ in range(4):
        runner.step()

    assert seen == [2, 1, 0, 0]
```

- [x] **Step 3: Run both to verify they fail**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_player.py tests/test_guardrails.py -k "remaining_message_budget"
```
Expected: 2 FAIL with `KeyError: 'limits'`.

- [x] **Step 4: Implement**

In `agents/player.py`, add to `ReferencePlayer` directly above `build_packet`:

```python
    def _packet_limits(self) -> dict[str, Any]:
        """The bounds the model must plan within (agents.md §18), stated as data in the packet.

        A ceiling the model cannot see is a ceiling it can only hit; in particular the per-phase
        Message budget shrinks across activations (the runner sets it), so it must be restated
        every activation. ``messages_remaining_this_phase`` is omitted when no runner has set a
        budget — an unbounded direct use claims no number.
        """
        g = self._guardrails
        limits: dict[str, Any] = {
            "max_messages_per_phase": g.max_messages_per_phase,
            "max_message_chars": g.max_message_chars,
            "max_argument_action_chars": g.max_argument_action_chars,
            "max_desired_effect_chars": g.max_desired_effect_chars,
            "max_pros": g.max_pros,
            "max_pro_chars": g.max_pro_chars,
            "max_note_chars": g.max_note_chars,
        }
        if self._message_budget is not None:
            limits["messages_remaining_this_phase"] = self._message_budget
        return limits
```

In `build_packet`, add `"limits": self._packet_limits(),` after `"notes": self._current_notes(),`.

In `build_activation_prompt`, after the `if packet.get("requires_input") and completion_action is not None:` block and before `body = json.dumps(...)`:

```python
        remaining = (packet.get("limits") or {}).get("messages_remaining_this_phase")
        if remaining == 0:
            framing += " You cannot send any more Messages this phase."
        elif remaining is not None:
            framing += (
                f" You may send at most {remaining} more Message"
                f"{'' if remaining == 1 else 's'} this phase; `limits` in the context states "
                "every other bound."
            )
```

- [x] **Step 5: Run to verify they pass** — same command. Expected: 2 PASS.

- [x] **Step 6: Run the packet-shape neighbours**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_player.py tests/test_lifecycle.py tests/test_guardrails.py tests/test_ai_smoke.py -m 'not llm'
```
Expected: all PASS (the fakes branch on `available_actions` only).

- [x] **Step 7: Set the smoke cap (user decision — default given)**

After Task 2, `1` really means one Message per Actor per phase. An Actor whose ready state is cleared by an incoming private Message could then *read* but never *answer* it. Recommended `2` (state a position; answer one re-opening Message). `3` would also allow an opening private approach, at the cost of more of the chatter the T-044 brief bounded. In `tests/test_ai_smoke.py` replace the guardrails comment and value:

```python
        # A smoke run proves autonomous completion, not unconstrained diplomacy. Permit two
        # authoritative Messages per Actor per phase: one to state a position and one to answer a
        # Message that re-opened the Actor's ready state (TEST-PHASE-003), while preventing the
        # unbounded chatter seen in the first live attempt (27 Messages before one Argument).
        # The cap is atomic (I-021) and the remaining budget is stated in every packet (I-015).
        guardrails = GuardrailConfig(
            max_llm_iterations=6,
            max_tool_calls=10,
            max_messages_per_phase=2,
            max_activations_per_opportunity=3,
        )
```

- [x] **Step 8: Commit**

```bash
git add agents/player.py tests/test_player.py tests/test_guardrails.py tests/test_ai_smoke.py
git commit -m "T-044 I-015 state the remaining Message budget in the packet

build_packet now carries a limits block including the runner-tracked
messages_remaining_this_phase, and the framing sentence says it in words.
Smoke cap set to 2 so a re-opened Actor can answer."
```

---

### Task 4: Rotate the sweep start per turn (I-016 — mitigation)

Sequential activation is inherent: letting an Actor respond to same-phase Messages is what a diplomacy phase is for, so "draft before reading" or "hold Messages until the phase closes" were rejected — they remove the response, not the anchoring. Rotation spreads which seat reads last; the prompt (Task 5) addresses copying directly. Con order is unaffected in substance: Cons are not in the packet and no Actor endpoint exposes them, so a later Con author sees no earlier Con.

**Files:**
- Modify: `agents/autonomous.py:139-140, 154-163, 176`
- Test: `tests/test_ai_smoke.py::test_autonomous_coordinator_completes_six_turns_offline`

- [x] **Step 1: Write the failing assertion**

The offline `default_policy` sends one public "Opening statement." per Actor per diplomacy phase, so Message creation order reveals the sweep. Add to the offline six-turn test after the Task 1 assertions:

```python
    # Sweep start rotates by turn (I-016): the Actor that speaks first in diplomacy changes every
    # turn, so no seat is always the one reading everyone else first.
    order_by_turn: dict[int, list[str]] = {}
    for message in result.export["messages"]:
        if message["public"]:
            order_by_turn.setdefault(message["turn"], []).append(message["sender_actor_id"])
    assert order_by_turn == {
        1: ["arkania", "belvar", "cestia"],
        2: ["belvar", "cestia", "arkania"],
        3: ["cestia", "arkania", "belvar"],
        4: ["arkania", "belvar", "cestia"],
        5: ["belvar", "cestia", "arkania"],
        6: ["cestia", "arkania", "belvar"],
    }
    assert "sweep=belvar,cestia,arkania" in audit
```

- [x] **Step 2: Run to verify it fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_smoke.py -m 'not llm' -k completes_six_turns_offline
```
Expected: FAIL — turn 2 is `["arkania", "belvar", "cestia"]`.

- [x] **Step 3: Implement**

In `agents/autonomous.py`, add a module-level helper after `PlayerFactory`:

```python
def sweep_order(actor_ids: list[str], turn: Any) -> list[str]:
    """The activation order for one coordinator sweep: seat order, rotated by turn.

    Actors are activated sequentially, so within a phase every later Actor reads what earlier
    Actors wrote before it acts. A fixed order gives the same seat the last word every turn and
    anchors it on the others' text (I-016). Rotating the start by ``turn`` spreads that position
    deterministically with no new randomness — the server still owns the RNG — and the order is
    logged so a run's provenance shows who spoke first. This mitigates anchoring; it does not
    remove it (someone is always last), which is the prompt's job.
    """
    if not actor_ids:
        return []
    offset = ((turn if isinstance(turn, int) else 1) - 1) % len(actor_ids)
    return actor_ids[offset:] + actor_ids[:offset]
```

In `run()`, replace the position log block and the sweep loop header:

```python
            position = (state.get("turn"), state.get("phase"), state.get("active_actor_id"))
            sweep = sweep_order(actor_order, state.get("turn"))
            if position != last_position:
                logger.info(
                    "autonomous game %s position: turn=%s phase=%s active_actor=%s sweep=%s",
                    game_id,
                    *position,
                    ",".join(sweep),
                )
                last_position = position
```

```python
            made_progress = False
            for actor_id in sweep:
```

- [x] **Step 4: Run to verify it passes** — same command. Expected: PASS.

- [x] **Step 5: Run the coordinator neighbours**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_smoke.py tests/test_lifecycle.py -m 'not llm'
```
Expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add agents/autonomous.py tests/test_ai_smoke.py
git commit -m "T-044 I-016 rotate the autonomous sweep start by turn

Sequential activation means later Actors read earlier same-phase Messages
before writing; a fixed order anchored the third seat every turn. The start
now rotates deterministically by turn and is logged with the position."
```

---

### Task 5: Player prompt v1.2 (I-017; obs 4, 5, 7)

Prompt text is spec (`docs/`): decision entry + new versioned file + `agents.md §31` list in a **doc commit**, then the code/test bump in a **code commit**.

**Files:**
- Create: `docs/prompts/player-v1.2.md`
- Modify: `docs/agents.md:879-885` (§31 file list)
- Modify: `dev/DECISIONS.md` (D-027 at the top, below the format block)
- Modify: `agents/player.py:55` (`PLAYER_PROMPT_VERSION`), module docstring
- Test: `tests/test_player.py::test_player_uses_versioned_neutral_prompt`

- [x] **Step 1: Write D-027**

Insert in `dev/DECISIONS.md` directly above `### D-026`:

```markdown
### D-027 — Player prompt v1.2: private information is for use, Cons are strategic, own words
**Date:** 2026-09-12 · **Status:** accepted
**Context:** The first provider-backed T-044 run (I-017) showed three prompt-level gaps in
`player-v1.1`: no Actor used its private information, brief detail or private Effects anywhere;
one Actor conned an Argument whose success served its own objectives; and a later-activated
Actor reproduced an earlier Actor's public statement near-verbatim.
**Decision:** Issue `docs/prompts/player-v1.2.md`. It adds: private information, brief and private
Effects are decision inputs to be used, not merely known; a Con is a strategic instrument read by
the Umpire, raised when an Argument's success would harm the Actor's objectives and withheld when
it would help; write positions in the Actor's own words. `player-v1.1.md` is retained unchanged
(`agents.md §31`). No strategy, stance or scenario tactic is prescribed (`agents.md §17`).
**Consequence:** Prompt version is an experiment-relevant field; runs on v1.1 and v1.2 are not
directly comparable. `PLAYER_PROMPT_VERSION` moves to `player-v1.2`.
```

- [x] **Step 2: Create `docs/prompts/player-v1.2.md`**

Copy `docs/prompts/player-v1.1.md` to `docs/prompts/player-v1.2.md`, change the heading to `# AMAW Player Prompt v1.2`, and make exactly these edits.

Replace the `## Information` section's closing lines (from `Do not assume that unavailable information is true.` to the end of that section) with:

```markdown
Do not assume that unavailable information is true.

If you need more information and the relevant tool is available, search for it.

Your Actor brief, objectives, private information and any Effects visible only to you are
decision inputs, not background. Other Actors do not have them. Use them when you choose what
to argue, what to say, whom to trust and what to write down; do not let them sit unused.
```

Replace the paragraph beginning `When responding to another Actor's Argument, submit a Con` with:

```markdown
When responding to another Actor's Argument, decide first whether its Desired Effect would help
or harm your objectives. A Con is a strategic instrument, not a review: it is read by the Umpire
and lowers the chance that the Effect is established. Raise a Con when the Effect would harm your
position and you have a substantive reason it may fail or be limited. Do not raise a Con against
an Effect that serves you, and do not invent one. After submitting any useful Cons, call
`pass_con()` to close your participation in that Argument. If you have no useful Con, call
`pass_con()` without first inventing one.
```

Append to `## Communication`, after `Remember that commitments, threats and statements may influence how other Actors respond later.`:

```markdown
State your own position in your own words. Do not reproduce another Actor's wording; agreeing
with a position is a decision, not a completion of someone else's text.
```

- [x] **Step 3: Update `docs/agents.md §31`** — file list becomes:

```text
prompts/player-v1.1.md
prompts/player-v1.2.md
prompts/umpire-v1.1.md
prompts/evaluator-v1.1.md
```

- [x] **Step 4: Doc commit**

```bash
git add docs/prompts/player-v1.2.md docs/agents.md dev/DECISIONS.md
git commit -m "D-027 player prompt v1.2: private information is for use, Cons are strategic

Adds player-v1.2.md alongside the retained v1.1 (agents.md §31) and lists it."
```

- [x] **Step 5: Write the failing test**

In `tests/test_player.py::test_player_uses_versioned_neutral_prompt`:

```python
    assert p.prompt_version == PLAYER_PROMPT_VERSION == "player-v1.2"
    # The prompt is the versioned file, actor-substituted — not a second embedded prompt.
    expected = load_player_prompt(p.actor_name)
    assert p.prompt == expected
    assert p.actor_name in p.prompt
    # Neutrality (agents.md §17): no pre-computed strategy or scenario tactics baked in.
    lowered = p.prompt.lower()
    assert "you decide your own strategy" in lowered
    assert "sweden" not in lowered and "coalition" not in lowered
    # D-027: private inputs are for use; Cons are strategic; own words.
    assert "decision inputs, not background" in lowered
    assert "a con is a strategic instrument" in lowered
    assert "in your own words" in lowered
```

- [x] **Step 6: Run to verify it fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_player.py -k versioned_neutral_prompt
```
Expected: FAIL on `"player-v1.1" == "player-v1.2"`.

- [x] **Step 7: Bump the version constant**

`agents/player.py`: `PLAYER_PROMPT_VERSION = "player-v1.2"`; update the module docstring's `player-v1.1.md` and `tests/test_player.py:5`.

- [x] **Step 8: Run the full suite** — expected: all PASS, no key.

- [x] **Step 9: Commit**

```bash
git add agents/player.py tests/test_player.py
git commit -m "T-044 I-017 reference player uses prompt v1.2 (D-027)"
```

---

### Task 6: Umpire prompt v1.2 — issued once, including the D-029 rule (I-019, I-018; obs 8, 11, 12)

**Files:**
- Create: `docs/prompts/umpire-v1.2.md`
- Modify: `docs/agents.md` §31 list, `dev/DECISIONS.md` (D-028, D-029)
- Modify: `agents/umpire.py:48` (`UMPIRE_PROMPT_VERSION`), docstrings at lines 14, 124, 152, `_render_messages`
- Modify: `tests/test_ai_smoke.py` (probability log in the `llm` test)
- Test: `tests/test_ai_umpire.py` — `grep -n "umpire-v1.1" tests/test_ai_umpire.py`

- [x] **Step 1: Write D-028 and D-029**

Insert above D-027 in `dev/DECISIONS.md`:

```markdown
### D-029 — Umpire receives every Actor's hidden information, tagged by owner
**Date:** 2026-09-12 · **Status:** accepted
**Context:** I-018. `agents.md §21` names `relevant_hidden_information` as a server-built Umpire
input but the server sends `[]`, so private facts cannot bound any Actor's claims. Adjudicated
Effects are public, so exposure needs a no-leak rule and a test.
**Decision:** The server passes every Actor's `private_information` and every active private
Effect, each tagged with `kind` and `actor_id` — as a human matrix-game umpire knows everything.
Symmetric: no Actor is bounded by its facts only when it happens to be arguing, and a Con is
weighed against its author's facts too. The Umpire prompt forbids restating hidden facts in
Effects or rationale; the smoke leak check covers Effect descriptions and rationales.
**Consequence:** The Umpire input grows by a few lines per Actor per adjudication;
`_build_umpire_context` is the single place the policy lives. Arguing-Actor-only and keep-empty
were considered and rejected (asymmetric handicap; inert information design).

### D-028 — Umpire prompt v1.2: contradictions supersede, failures cost, anchors kept
**Date:** 2026-09-12 · **Status:** accepted
**Context:** I-019. In the first provider-backed run a failed Argument produced a new Effect that
sat beside the Effect it contradicted, and failure Effects were materially softer than success
Effects. Supersession is adjudicator-chosen (`agents.md §22`) and server-validated; the prompt
only asked for supersession when a new Effect "clearly replaces" one.
**Decision:** Issue `docs/prompts/umpire-v1.2.md` once, carrying the D-029 no-leak rule as well:
an Effect that contradicts an active Effect must supersede it; failure Effects describe the real
cost of the failed action; the `agents.md §25` anchors stay and the whole range is to be used.
v1.1 is retained unchanged. Probability calibration is judged on ≥3 provider-backed runs of
v1.2 (Part C), not on one.
**Consequence:** Umpire prompt version is a fixed-Umpire benchmark field
(`benchmark-methodology §9`); runs on v1.1 and v1.2 are not directly comparable.
```

- [x] **Step 2: Create `docs/prompts/umpire-v1.2.md`**

Copy `docs/prompts/umpire-v1.1.md` to `docs/prompts/umpire-v1.2.md`, heading `# AMAW Umpire Prompt v1.2`, and make exactly these edits.

In `## Probability`, replace `Avoid fake precision.` with:

```markdown
Avoid fake precision, and avoid clustering: a well-supported action against weak Cons belongs
near `0.70`–`0.90`, an overreaching action against strong Cons near `0.10`–`0.30`. Use the whole
range when the evidence warrants it.
```

In `## Effects`, replace `If a new Effect clearly replaces an existing Effect, identify the existing Effect ID in `supersedes_effect_ids`.` with:

```markdown
If a new Effect replaces or contradicts an existing active Effect, you must list that Effect's ID
in `supersedes_effect_ids`; two active Effects must never state incompatible facts.

Failure Effects describe the real consequence of the action failing — lost time, exposed
intentions, hardened opposition, forgone opportunity — proportionate to the action's scale. A
failure Effect must not be a softened version of the success Effect.
```

Append to `## Effects` (D-029):

```markdown
`relevant_hidden_information` is authorised for your judgement only. Effects and your rationale
are visible to Actors: never restate a hidden fact in either; let it shape the probability and
the scale of the Effect, not the text.
```

- [x] **Step 3: Update `docs/agents.md §31`** — add `prompts/umpire-v1.2.md` after the v1.1 line.

- [x] **Step 4: Doc commit**

```bash
git add docs/prompts/umpire-v1.2.md docs/agents.md dev/DECISIONS.md
git commit -m "D-028 D-029 umpire prompt v1.2: contradictions supersede, failures cost, no hidden-fact leaks"
```

- [x] **Step 5: Write the failing tests**

`grep -n "umpire-v1.1" tests/test_ai_umpire.py`; change every version assertion to `"umpire-v1.2"`. Next to the first, add:

```python
    assert "must list that effect's id" in umpire.prompt.lower()
```

(`grep -n "_prompt\b\|def prompt" agents/umpire.py` — add a `prompt` property returning `self._prompt` if none exists.)

In `test_ai_smoke_three_players_six_turns_no_intervention`, after the `roll is not None` assertion:

```python
    probabilities = sorted(
        a["probability"]
        for a in result.export["adjudications"]
        if a["mode"] == "probabilistic" and a["probability"] is not None
    )
    modes = sorted(a["mode"] for a in result.export["adjudications"])
    logging.getLogger("agents.autonomous").info(
        "smoke adjudication summary modes=%s probabilities=%s", modes, probabilities
    )
```

- [x] **Step 6: Run to verify the umpire test fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_umpire.py -m 'not llm'
```
Expected: FAIL on the version string.

- [x] **Step 7: Bump the version constant** — `agents/umpire.py`: `UMPIRE_PROMPT_VERSION = "umpire-v1.2"`; update the docstring mentions.

- [x] **Step 8: Run the full suite** — expected: all PASS.

- [x] **Step 9: Commit**

```bash
git add agents/umpire.py tests/test_ai_umpire.py tests/test_ai_smoke.py
git commit -m "T-044 I-019 AI Umpire uses prompt v1.2 (D-028); smoke logs probability set"
```

---

### Task 7: Umpire hidden-information context + leak test (I-018 / D-029, option 1)

**Files:**
- Modify: `app/game_service.py:1318-1347` (`_build_umpire_context`)
- Modify: `tests/test_ai_smoke.py::_assert_no_private_data_leaks`
- Test: wherever `_build_umpire_context` is already exercised — `grep -ln "_build_umpire_context\|relevant_hidden_information" tests/*.py`

- [x] **Step 0: Confirm names**

```bash
grep -n "^def \|^class " app/game_service.py | grep -i "argument\|umpire_context"
grep -n "class Actor" -A 12 app/models.py | grep -n "primary_key\|game_id"
grep -n "visible_to\|visibility" app/models.py | head
grep -n "^def " tests/api_helpers.py
```
Use the real names below; the Actor's primary key is game-scoped (D-008), so the `session.get` tuple order must match `app/models.py`.

- [x] **Step 1: Write the failing context test**

```python
def test_umpire_context_carries_every_actors_private_facts(db_engine: Engine) -> None:
    """agents.md §21 / D-029: the server passes all Actors' hidden facts, tagged by owner."""
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania is the active Actor in turn 1
    argument = game_service.submit_argument(  # confirm the real name in Step 0
        db_engine, creation.game_id, "arkania",
        action="Begin mobilisation", desired_effect="Reserves deploy", pros=["Plans exist"],
        idempotency_key="k1",
    )
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        context = game_service._build_umpire_context(session, game, argument)

    items = context.relevant_hidden_information
    by_text = {item["text"]: item for item in items}
    assert by_text["Reserve mobilisation requires two turns."]["actor_id"] == "arkania"
    assert by_text["Reserve mobilisation requires two turns."]["kind"] == "private_information"
    assert by_text["Domestic support for a prolonged deployment is limited."]["actor_id"] == "belvar"
    assert by_text["The coalition government is politically fragile."]["actor_id"] == "cestia"
    assert by_text["Arkania's reserve mobilisation plan is incomplete."]["kind"] == "private_effect"
    assert all(set(item) == {"kind", "actor_id", "text"} for item in items)
```

- [x] **Step 2: Write the failing leak assertion**

Extend `_assert_no_private_data_leaks` in `tests/test_ai_smoke.py` — after the per-viewer loop:

```python
    # D-029: the Umpire sees hidden facts but its Effects and rationales are Actor-visible, so a
    # hidden fact must never be restated verbatim in either.
    umpire_text = " ".join(
        [e["description"] for e in export["effects"]]
        + [a.get("rationale") or "" for a in export["adjudications"]]
    )
    for actor in actors.values():
        for fact in actor["private_information"]:
            assert fact not in umpire_text, fact
```

(`grep -n '"rationale"' app/export.py` to confirm the field name.) The offline run uses the FakeUmpire, which writes no hidden facts, so this passes offline and bites on the provider run.

- [x] **Step 3: Run to verify the context test fails** — expected: `KeyError` (list is empty).

- [x] **Step 4: Implement** in `_build_umpire_context`:

```python
    actors = session.exec(select(Actor).where(Actor.game_id == game.id)).all()
    hidden: list[dict[str, Any]] = [
        {"kind": "private_information", "actor_id": actor.id, "text": fact}
        for actor in actors
        for fact in actor.private_information
    ]
    hidden.extend(
        {"kind": "private_effect", "actor_id": viewer, "text": effect.description}
        for effect in active_effects
        if effect.visibility == "private"
        for viewer in (effect.visible_to or [])
    )
```

pass `relevant_hidden_information=hidden`, and rewrite the docstring: policy is D-029; the server, never a player model, chooses what the Umpire sees (`agents.md §21`).

- [x] **Step 5: Run the full suite** — expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add app/game_service.py tests/
git commit -m "T-044 I-018 D-029 Umpire receives tagged hidden facts; smoke checks Effects for leaks"
```

---

### Task 8: Write smoke provenance to disk (I-020; obs 13)

**Files:**
- Modify: `tests/test_ai_smoke.py` (helper + offline test + `llm` test)

- [x] **Step 1: Write the failing test**

Add after the offline six-turn test:

```python
def test_smoke_provenance_is_written_when_export_dir_is_set(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    client = TestClient(app, raise_server_exceptions=False)
    result = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_offline_player_factory,
    ).run()

    monkeypatch.setenv("AMAW_SMOKE_EXPORT_DIR", str(tmp_path))
    written = _write_run_provenance(result)

    assert written == tmp_path / result.game_id
    export = json.loads((written / "export.json").read_text())
    assert export["game"]["status"] == "complete"
    activations = json.loads((written / "activations.json").read_text())
    assert len(activations) == len(result.activations)
    assert activations[0]["tool_call_names"] == result.activations[0].tool_call_names
    assert json.loads((written / "failures.json").read_text()) == []
    # Never write credentials.
    blob = "".join(p.read_text() for p in written.iterdir())
    assert ADMIN_TOKEN not in blob
    assert all(token not in blob for token in result.seat_tokens.values())

    monkeypatch.delenv("AMAW_SMOKE_EXPORT_DIR")
    assert _write_run_provenance(result) is None
```

- [x] **Step 2: Run to verify it fails**

```bash
UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_smoke.py -m 'not llm' -k provenance_is_written
```
Expected: FAIL with `NameError: _write_run_provenance`.

- [x] **Step 3: Implement the helper**

Add after `_assert_no_private_data_leaks`; add `import dataclasses` and `import os`.

```python
def _write_run_provenance(result) -> Path | None:
    """Persist a run's export and ephemeral provenance for post-run analysis (I-020).

    Only when ``AMAW_SMOKE_EXPORT_DIR`` is set. Seat tokens and the admin token are never
    written: the export is the T-043 administrative document (which carries no tokens) and the
    traces are dataclasses with no credential fields.
    """
    target = os.environ.get("AMAW_SMOKE_EXPORT_DIR")
    if not target:
        return None
    run_dir = Path(target) / result.game_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "export.json").write_text(json.dumps(result.export, indent=2, default=str))
    (run_dir / "activations.json").write_text(
        json.dumps([dataclasses.asdict(t) for t in result.activations], indent=2, default=str)
    )
    (run_dir / "failures.json").write_text(
        json.dumps([dataclasses.asdict(f) for f in result.failures], indent=2, default=str)
    )
    return run_dir
```

In `test_ai_smoke_three_players_six_turns_no_intervention`, directly after the `try/except` that produces `result`: `_write_run_provenance(result)`.

- [x] **Step 4: Run to verify it passes** — same command. Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add tests/test_ai_smoke.py
git commit -m "T-044 I-020 write smoke export and provenance to AMAW_SMOKE_EXPORT_DIR"
```

Provider run command (for Part C):

```bash
mkdir -p /private/tmp/amaw-smoke && AMAW_SMOKE_EXPORT_DIR=/private/tmp/amaw-smoke UV_CACHE_DIR=/private/tmp/amaw-uv-cache uv run pytest -q tests/test_ai_smoke.py -m llm -o log_cli=true --log-cli-level=INFO 2>&1 | tee /private/tmp/amaw-smoke/run-$(date +%H%M).log
```

---

### Task 9: Clarify what the fixture's determinism claim covers (obs 10)

**Files:**
- Modify: `tests/fixtures/README.md` (the `scenario.yaml` header is a golden file — do not edit it)

- [x] **Step 1: Add the paragraph** after the one ending `Treat a change here as a change to a golden file.`:

```markdown
**What "deterministic" covers.** The fixture is deterministic for the engine tests that drive it
with ScriptedPlayers and the FakeUmpire (T-025/T-026, `tests/test_integration_six_turn.py`): the
seed fixes every server roll. The provider-backed AI smoke test (T-044,
`tests/test_ai_smoke.py`) reuses this scenario for its *content* only — Arguments, Cons and
Umpire proposals are model output and are not reproducible (`benchmark-methodology §8`: "Seed
controls server stochasticity only. It does not guarantee identical LLM outputs."). Never build
an expected output from an AI run. A richer live-play scenario is T-065.
```

- [x] **Step 2: Commit**

```bash
git add tests/fixtures/README.md
git commit -m "T-044 clarify the fixture's determinism claim covers scripted play only"
```

---

## Part C — Closing the loop

- [x] Full offline suite; paste the output into `dev/tasks/T-044-ai-smoke-test.md`. (`460 passed, 4 deselected` at `49b7a46`.)
- [ ] Provider-backed smoke with `AMAW_SMOKE_EXPORT_DIR` set (Task 8 command). Paste the result, the `smoke adjudication summary` line and the `sweep=` lines into the brief.
- [ ] From `export.json`, check — as a smoke check, not evidence — whether any Actor cites its private information, whether Cons align with interest, and whether the leak assertion held.
- [ ] **Resolve with one run:** I-015, I-016, I-017, I-018, I-020, I-021 (each has an offline test that is the definition of done; the provider run is the acceptance context).
- [ ] **Do not resolve with one run:** I-019. It stays open, carried across the Gate 2 boundary with the note "calibration verdict needs ≥3 provider-backed runs on umpire-v1.2"; the probability lists from each run accumulate in the backlog entry.
- [ ] Move the resolved items (and I-004…I-014, per their own batch brief) to `dev/BACKLOG_RESOLVED.md` only with that evidence; then `/task-done T-044`.

## Self-review notes

- Coverage: obs 1→T2+T3, 2/3→T1, 4→T4+T5, 5→T5+T7, 7→T5, 8/11/12→T6, 10→T9, 13→T8, 6/9→none (planned work); all filed in T0.
- Names used consistently: `sweep_order`, `_packet_limits`, `messages_remaining_this_phase`, `_send_lock`, `_write_run_provenance`, `PLAYER_PROMPT_VERSION = "player-v1.2"`, `UMPIRE_PROMPT_VERSION = "umpire-v1.2"`.
- Task 1's expected grant is a set comparison — no ordering dependence.
- Tasks 2, 3 and 7 carry a Step 0 grep for the names they rely on; Task 7's helper names must be confirmed before use.
- `umpire-v1.2.md` is written once (Task 6), after the Task 0 decision — no post-commit edit of a versioned prompt.
