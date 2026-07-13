"""In-app review notifications — the pure mention-parse + roster-match logic. Auto-provisioning
(_touch_reviewer) and the DB fan-out (_emit_notifications) need Postgres and run on the deploy."""
from ugs_warehouse import comments


def test_mention_tokens_parses_and_ignores_email_domains():
    toks = comments._mention_tokens("hey @alice and @bob.smith — email foo@bar.com, cc @Carol")
    assert toks == {"alice", "bob.smith", "carol"}  # @Carol lowercased; bar.com (email domain) ignored
    assert comments._mention_tokens("") == set()
    assert "bar.com" not in comments._mention_tokens("write to foo@bar.com")


def test_match_roster_matches_localpart_and_full_email():
    roster = ["alice@utah.gov", "bob.smith@utah.gov", "carol@dnr.utah.gov"]
    assert comments._match_roster(roster, {"alice"}) == {"alice@utah.gov"}
    assert comments._match_roster(roster, {"bob.smith"}) == {"bob.smith@utah.gov"}
    assert comments._match_roster(roster, {"carol@dnr.utah.gov"}) == {"carol@dnr.utah.gov"}  # full-email token
    assert comments._match_roster(roster, {"nobody"}) == set()
    assert comments._match_roster(roster, set()) == set()


def test_match_roster_is_case_insensitive():
    assert comments._match_roster(["Alice@Utah.gov"], {"alice"}) == {"Alice@Utah.gov"}


def test_bearer_token_extraction():
    class Req:
        def __init__(self, h):
            self.headers = h
    assert comments._bearer_token(Req({"authorization": "Bearer abc.def.ghi"})) == "abc.def.ghi"
    assert comments._bearer_token(Req({"authorization": "bearer xyz"})) == "xyz"  # case-insensitive scheme
    assert comments._bearer_token(Req({"authorization": "Basic abc"})) is None
    assert comments._bearer_token(Req({})) is None


def test_verify_firebase_email_returns_none_on_bad_token():
    # No ADC / garbage token → None, never raises (falls through to 401, not 500).
    assert comments._verify_firebase_email("not-a-real-token") is None
