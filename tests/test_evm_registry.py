import pytest

boa = pytest.importorskip("boa")

from latencymesh.evm.chain import (  # noqa: E402
    ETHER,
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_TRUSTED,
    MeshChain,
    encode_rtt,
)
from latencymesh.model import Location, Measurement, Node  # noqa: E402
from latencymesh.synth import WorldConfig, generate_world  # noqa: E402

pytestmark = pytest.mark.evm


@pytest.fixture
def chain():  # type: ignore[no-untyped-def]
    world = generate_world(WorldConfig(n_nodes=12, n_trusted=6, seed=1))
    c = MeshChain()
    c.register_genesis(world.genesis())
    return c


NEW = Node("new", location=Location(52.2, 21.0, asn=64999))


def test_genesis_is_sealed_and_trusted(chain) -> None:  # type: ignore[no-untyped-def]
    assert chain.registry.sealed()
    assert chain.registry.node_count() == 6
    assert chain.status("n00000") == STATUS_TRUSTED
    with boa.reverts("genesis sealed"):
        chain.registry.register_genesis(chain.address("x"), 0, 0, 0)


def test_join_requires_stake_and_valid_coordinates(chain) -> None:  # type: ignore[no-untyped-def]
    with boa.reverts("stake too low"):
        chain.join(NEW, stake=ETHER - 1)
    with chain.as_node("bad"), boa.reverts("bad latitude"):
        chain.registry.join(91_000_000, 0, 0, value=ETHER)
    chain.join(NEW)
    assert chain.status("new") == STATUS_PENDING
    assert chain.stake("new") == ETHER


def test_commit_reveal_happy_path_and_phase_rules(chain) -> None:  # type: ignore[no-untyped-def]
    chain.join(NEW)
    rid = chain.open_round(commit_blocks=5, reveal_blocks=5)
    m = Measurement("n00000", "new", 12.345)
    chain.commit(rid, m)
    with boa.reverts("still in commit phase"):
        chain.reveal(rid, m)
    chain.to_reveal_phase(rid)
    with boa.reverts("commit phase over"):
        chain.commit(rid, Measurement("new", "n00000", 12.3))
    chain.reveal(rid, m)
    assert chain.revealed(rid, "n00000", "new") == pytest.approx(12.345)


def test_reveal_must_match_commitment(chain) -> None:  # type: ignore[no-untyped-def]
    chain.join(NEW)
    rid = chain.open_round()
    chain.commit(rid, Measurement("n00000", "new", 10.0))
    chain.to_reveal_phase(rid)
    salt = chain.salt(rid, "n00000", "new")
    with chain.as_node("n00000"), boa.reverts("commitment mismatch"):
        chain.registry.reveal(rid, chain.address("new"), encode_rtt(11.0), salt)


def test_commitment_cannot_be_copied_by_another_observer(chain) -> None:  # type: ignore[no-untyped-def]
    chain.join(NEW)
    rid = chain.open_round()
    m = Measurement("n00000", "new", 10.0)
    chain.commit(rid, m)
    copied = chain.registry.commitments(rid, chain.address("n00000"), chain.address("new"))
    with chain.as_node("n00001"):
        chain.registry.commit(rid, chain.address("new"), copied)
    chain.to_reveal_phase(rid)
    with chain.as_node("n00001"), boa.reverts("commitment mismatch"):
        chain.registry.reveal(
            rid, chain.address("new"), encode_rtt(10.0), chain.salt(rid, "n00000", "new")
        )


def test_non_revealer_is_slashed_and_reporter_paid(chain) -> None:  # type: ignore[no-untyped-def]
    chain.join(NEW)
    rid = chain.open_round(commit_blocks=3, reveal_blocks=3)
    chain.commit(rid, Measurement("new", "n00000", 10.0))
    chain.to_reveal_phase(rid)
    with boa.reverts("reveal phase not over"):
        chain.registry.slash_non_revealer(rid, chain.address("new"), chain.address("n00000"))
    chain.to_round_end(rid)
    reporter = chain.address("reporter")
    before = boa.env.get_balance(reporter)
    with boa.env.prank(reporter):
        chain.registry.slash_non_revealer(rid, chain.address("new"), chain.address("n00000"))
    assert boa.env.get_balance(reporter) - before == ETHER // 2
    assert chain.stake("new") == 0
    with boa.reverts("already slashed"):
        chain.registry.slash_non_revealer(rid, chain.address("new"), chain.address("n00000"))


def test_status_decisions_and_refund(chain) -> None:  # type: ignore[no-untyped-def]
    chain.join(NEW)
    other = Node("other", location=Location(0, 0))
    chain.join(other)
    before = boa.env.get_balance(chain.address("other"))
    chain.set_status("new", STATUS_ACTIVE)
    chain.set_status("other", STATUS_REJECTED)
    assert chain.status("new") == STATUS_ACTIVE
    assert boa.env.get_balance(chain.address("other")) - before == ETHER
    with boa.reverts("not pending"):
        chain.set_status("new", STATUS_REJECTED)
    with chain.as_node("new"), boa.reverts("only owner"):
        chain.registry.set_status(chain.address("other"), STATUS_ACTIVE)
