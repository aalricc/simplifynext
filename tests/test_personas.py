"""Demo personas: each tells its own story, and Maya's is unchanged.

The whole point of the fixture refactor is that USE_FIXTURES=1 can demo more
than one creator. These tests guard both halves of that: the new personas are
genuinely distinct, and the original Maya demo still behaves exactly as it did.
"""

from __future__ import annotations

import json

import pytest

from shared import fixtures

# Every persona must be able to drive the full board.
REQUIRED_FILES = [
    "profile.json",
    "agents.json",
    "opportunities_seed.json",
    "rag_corpus.json",
    "inbox.json",
    "analytics_week1.json",
    "search_web.json",
]

# The agents the campaign graph actually calls.
REQUIRED_AGENTS = [
    "CDRRootAgent",
    "NicheQueryAgent",
    "TrendHarvesterAgent",
    "BrandGapAgent",
    "CollabScoutAgent",
    "OpportunityScorerAgent",
    "ResearchLeadAgent",
    "AudienceResearchAgent",
    "PeerCreatorAnalysisAgent",
    "PlatformPresenceAgent",
    "PainPointAgent",
    "ProposalGenerationAgent",
    "DraftWriterAgent",
    "FactCheckerAgent",
    "VoiceCritiqueAgent",
    "OutreachStrategyAgent",
    "PitchEmailAgent",
    "PerformanceAdaptAgent",
]

ALL = fixtures.personas()


@pytest.fixture(autouse=True)
def _reset_persona():
    token = fixtures.set_persona("")
    yield
    fixtures.reset_persona(token)


def test_expected_personas_exist():
    assert {"maya", "henry", "john"} <= set(ALL)


@pytest.mark.parametrize("persona", ALL)
def test_persona_has_every_data_file(persona):
    missing = [
        name
        for name in REQUIRED_FILES
        if not (fixtures.DEMO / persona / name).exists()
    ]
    assert not missing, f"{persona} is missing {missing}"


@pytest.mark.parametrize("persona", ALL)
def test_persona_defines_every_agent_the_graph_calls(persona):
    fixtures.set_persona(persona)
    agents = fixtures.load_persona_file("agents.json", {})
    missing = [a for a in REQUIRED_AGENTS if a not in agents]
    assert not missing, f"{persona}/agents.json is missing {missing}"


@pytest.mark.parametrize("persona", ALL)
def test_root_agent_selects_ids_that_exist(persona):
    """A selected id that is not in the seed file means an empty campaign."""
    fixtures.set_persona(persona)
    seeded = {o["id"] for o in fixtures.seed_opportunities()}
    selected = fixtures.fixture_json("CDRRootAgent", "").get("selected_ids", [])
    assert selected, f"{persona} selects no opportunities"
    assert set(selected) <= seeded, f"{persona} selects unknown ids: {set(selected) - seeded}"


@pytest.mark.parametrize("persona", ALL)
def test_critique_fails_the_draft_then_passes_the_rewrite(persona):
    """The demo's fail-then-fix beat. Without this the board shows no loop."""
    fixtures.set_persona(persona)
    draft = fixtures.fixture_json("ProposalGenerationAgent", "")["hero_script"]
    rewrite = fixtures.fixture_json("DraftWriterAgent", "")["hero_script"]

    assert fixtures.fixture_json("FactCheckerAgent", draft)["verdict"] == "fail"
    assert fixtures.fixture_json("FactCheckerAgent", rewrite)["verdict"] == "pass"
    assert fixtures.fixture_json("VoiceCritiqueAgent", rewrite)["verdict"] == "pass"


@pytest.mark.parametrize("persona", ALL)
def test_adapt_loop_has_a_win_a_loss_and_a_bias(persona):
    fixtures.set_persona(persona)
    memory = fixtures.fixture_json("PerformanceAdaptAgent", "")
    for field in ("wins", "losses", "next_bias"):
        assert memory.get(field), f"{persona} has no {field}"


@pytest.mark.parametrize("persona", ALL)
def test_week_two_reply_is_classified_as_interested(persona):
    """The inbox item must move an opportunity to engaged, or week 2 is flat."""
    fixtures.set_persona(persona)
    items = fixtures.load_persona_file("inbox.json", {}).get("items", [])
    assert items, f"{persona} has an empty inbox"

    body = f"{items[0].get('subject','')} {items[0].get('body','')}"
    result = fixtures.fixture_json("ReplyClassifierAgent", body)
    assert result["label"] == "interested"
    assert result["next_status"] == "engaged"

    seeded = {o["id"] for o in fixtures.seed_opportunities()}
    assert result["opportunity_id"] in seeded


@pytest.mark.parametrize("persona", ALL)
def test_analytics_has_a_clear_winner_and_loser(persona):
    """PerformanceAdaptAgent ranks by views; a tie makes the demo ambiguous."""
    fixtures.set_persona(persona)
    posts = fixtures.load_persona_file("analytics_week1.json", {}).get("posts", [])
    assert len(posts) >= 3
    views = sorted((p["views"] for p in posts), reverse=True)
    assert views[0] >= views[-1] * 3, f"{persona}'s best post barely beats its worst"


def test_personas_do_not_share_content():
    """Henry must not be handed Maya's laksa, and vice versa."""
    scripts = {}
    for persona in ("maya", "henry", "john"):
        fixtures.set_persona(persona)
        scripts[persona] = fixtures.fixture_json("DraftWriterAgent", "")["hero_script"]

    assert len(set(scripts.values())) == 3
    assert "laksa" in scripts["maya"].lower()
    assert "laksa" not in scripts["henry"].lower()
    assert "laksa" not in scripts["john"].lower()


def test_unknown_persona_falls_back_to_maya():
    fixtures.set_persona("nobody-by-that-name")
    # persona_dir points at a directory that does not exist; persona_file falls
    # back so a half-authored persona never blanks the board.
    assert fixtures.fixture_json("CDRRootAgent", "")["selected_ids"]


@pytest.mark.parametrize(
    "profile,expected",
    [
        ({"handle": "@mayacooks.sg"}, "maya"),
        ({"handle": "henrypulls"}, "henry"),
        ({"handle": "@johnthrifts"}, "john"),
        ({"handle": "someone", "niche": "Pokemon card openings"}, "henry"),
        ({"handle": "someone", "niche": "thrift flipping"}, "john"),
        ({"handle": "someone", "niche": "astrophysics"}, "maya"),
        (None, "maya"),
    ],
)
def test_profile_maps_to_persona(profile, expected):
    assert fixtures.persona_for_profile(profile) == expected


def test_seed_ids_are_unique_within_a_persona():
    for persona in ALL:
        fixtures.set_persona(persona)
        ids = [o["id"] for o in fixtures.seed_opportunities()]
        assert len(ids) == len(set(ids)), f"{persona} has duplicate opportunity ids"


def test_agents_json_is_valid_json():
    for persona in ALL:
        path = fixtures.DEMO / persona / "agents.json"
        json.loads(path.read_text())
