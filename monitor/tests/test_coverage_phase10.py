"""Offline coverage audit: synthetic DOM protocol fixtures, real contract/SQLite.

No Playwright, HTTP server, provider access, worker process or submission occurs.
Characterization cases deliberately expose omissions; they are not coverage proof.
"""
import asyncio
from itertools import permutations
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import discovery_contract as contract
from discovery_visit import collect_panel
from test_monitor import m, insert_scheduled


def row(identity, version=1, state="Running", slug="audit-job"):
    return {"href": f"https://www.kaggle.com/code/thangma/{slug}?scriptVersionId={identity}",
            "text": f"Version #{version} {state}"}


class Panel:
    """Implements only the DOM sampling protocol; time is simulated in ticks."""
    def __init__(self, samples, scrolling=False, extra_text=""):
        self.samples = samples
        self.scrolling = scrolling
        self.extra_text = extra_text
        self.ticks = 0

    def get_by_role(self, *_args, **_kwargs):
        return self

    async def pause(self, _milliseconds):
        self.ticks += 1

    async def evaluate_all(self, _script):
        return [dict(r) for r in self.samples(self.ticks)]

    async def inner_text(self):
        return "\n".join(r["text"] for r in self.samples(self.ticks)) + self.extra_text

    async def evaluate(self, _script):
        return self.scrolling


def payload(rows, at=1000):
    return {"account": "thangma", "observed_at": at, "coverage": "partial",
            "observations": [contract.event(r["href"], r["text"], at) for r in rows],
            "error": None, "auth_error": None}


def collect(panel, resolve=None):
    return asyncio.run(collect_panel(panel, panel.pause, resolve))


@pytest.mark.parametrize("arrival", [1, 5, 8, 9, 13])
def test_bounded_delay_has_no_completeness_implication(tmp_path, arrival):
    panel = Panel(lambda tick: [row(101)] if tick >= arrival else [])
    rows = collect(panel)
    assert panel.ticks == 8
    assert bool(rows) == (arrival <= 8)
    snapshot = contract.validate(payload(rows), {"thangma"}, 1000)
    store = m.Store(tmp_path / "db")
    store.record_activity("thangma", "browser_discovery", snapshot["observations"], "partial", 1000)
    assert store.admission("thangma", 1000)["would_defer"]
    assert m.account_activity("thangma", [], store.activity_evidence(), 1000)["status"] != "observed_idle"


def test_virtualized_scroll_union_is_bounded_and_cannot_visit_thirteenth_page():
    panel = Panel(lambda tick: [row(100 + tick, tick)], scrolling=True)
    rows = collect(panel)
    assert panel.ticks == 12
    assert {r["identity"] for r in payload(rows)["observations"]} == {
        f"script-version:{100 + tick}" for tick in range(1, 13)}
    assert "script-version:113" not in {r["identity"] for r in payload(rows)["observations"]}


@pytest.mark.parametrize("order", list(permutations([101, 102, 103])))
def test_href_identities_survive_reorder_and_same_version_number_across_refs(order):
    # Version #1 is notebook-local, so it is deliberately identical in all rows.
    panel = Panel(lambda tick: [row(i, slug=f"job-{i}") for i in
                               (order if tick % 2 else tuple(reversed(order)))])
    observations = payload(collect(panel))["observations"]
    assert {o["identity"] for o in observations} == {f"script-version:{i}" for i in order}
    assert len(contract.validate(payload(collect(panel)), {"thangma"}, 1000)["observations"]) == 3


def test_one_hundred_rows_supported_but_overflow_is_failure_not_truncation():
    rows = [row(i, i) for i in range(1, 102)]
    assert len(collect(Panel(lambda _: rows[:100]))) == 100
    with pytest.raises(ValueError, match="panel_unavailable"):
        collect(Panel(lambda _: rows))


def test_url_aliases_do_not_turn_one_script_id_into_two_accepted_observations():
    first = row(101)
    alias = {**first, "href": first["href"] + "&view=read"}
    rows = collect(Panel(lambda _: [first, alias]))
    assert len(rows) == 2  # Raw-URL keys are not canonical script-version keys.
    with pytest.raises(ValueError, match="invalid_discovery_snapshot"):
        contract.validate(payload(rows), {"thangma"}, 1000)


def test_unversioned_interactive_text_can_be_omitted_but_never_proves_idle(tmp_path):
    panel = Panel(lambda _: [], extra_text="Interactive notebook session Running; Next page")
    assert collect(panel) == []  # Neither pagination nor native sessions are traversed.
    snapshot = contract.validate(payload([]), {"thangma"}, 1000)
    store = m.Store(tmp_path / "db")
    store.record_activity("thangma", "browser_discovery", snapshot["observations"], "partial", 1000)
    assert store.admission("thangma", 1000)["would_defer"]


def test_identical_href_less_text_must_not_collapse_distinct_destinations():
    panel = Panel(lambda _: [{"href": "", "text": "Version #1 Running"}] * 2)
    calls = []
    async def resolve(position, text):
        calls.append(position)
        return row(101 + position, slug=f"job-{position}")["href"]
    rows = collect(panel, resolve)
    assert len(rows) == 2 and set(calls) == {0, 1}


def test_terminal_word_in_title_must_not_resolve_unknown_provider_status():
    observation = contract.event(row(101)["href"],
                                 "Status for Successful experiment. Click to open Version #1 Retrying", 1000)
    assert observation["state"] == "unresolved"


def test_empty_success_replaces_previous_browser_positive_without_terminal_proof(tmp_path):
    store = m.Store(tmp_path / "db")
    store.record_activity("thangma", "browser_discovery", payload([row(101)])["observations"], "partial", 1000)
    assert m.account_activity("thangma", [], store.activity_evidence(), 1000)["status"] == "running"
    store.record_activity("thangma", "browser_discovery", [], "partial", 1001)
    activity = m.account_activity("thangma", [], store.activity_evidence(), 1001)
    assert activity["observations"] == [] and activity["status"] == "partial"
    assert store.admission("thangma", 1001)["would_defer"]


@pytest.mark.parametrize("account", ["thangma", "annalynhi", "thanglong1996", "thnhmngc"])
@pytest.mark.parametrize("policy", ["enforce", "observe_only"])
def test_current_four_account_coverage_has_explicit_policy_outcome(tmp_path, account, policy):
    store = m.Store(tmp_path / "db")
    store.record_activity("thangma", "browser_discovery", [], "partial", 1000)
    run = insert_scheduled(store, account=account, due=1000)
    assert store.claim(run, 1000, policy) == (policy == "observe_only")
    assert run["admission"]["would_defer"]
    expected = ("required_coverage_incomplete:" if account == "thangma" else "required_source_missing:")
    assert expected + "browser_discovery" in run["admission"]["reasons"]
    assert run["state"] == ("skipped" if policy == "enforce" else "pushing")


def test_success_cadence_can_exceed_fixed_ttl_without_worker_failure():
    # Completion-relative 300s delay + next successful visit's wall time.
    import discovery_worker as worker
    class Maximum:
        def uniform(self, low, high):
            return high
    next_observation = 1000 + worker.next_delay(0, Maximum()) + 27
    snapshot = {"account": "thangma", "source": "browser_discovery", "observed_at": m.iso(1000),
                "last_attempt_at": m.iso(1000), "coverage": "partial", "observations": [],
                "error": None, "auth_error": None, "stale_after_seconds": 300}
    assert next_observation == 1327
    assert m.account_activity("thangma", [], [snapshot], 1301)["status"] == "stale"
