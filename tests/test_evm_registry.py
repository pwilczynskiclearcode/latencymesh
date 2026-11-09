"""Unit tests of NodeRegistry.vy: genesis, joins, witness draws, commit-reveal, accordance."""

import hashlib

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
from latencymesh.genesis import Genesis  # noqa: E402
from latencymesh.model import Location, Measurement, Node  # noqa: E402

pytestmark = pytest.mark.evm

# 4 regions x 3 genesis nodes; RTT 1 ms inside a region, 80 ms across regions
REGIONS = {f"g{r}{i}": r for r in range(4) for i in range(3)}


def make_genesis() -> Genesis:
    centres = [(50.0, 8.0), (40.0, -75.0), (35.0, 139.0), (-33.0, 151.0)]
    nodes = [
        Node(n, True, Location(centres[r][0] + 0.01 * int(n[-1]), centres[r][1]))
        for n, r in REGIONS.items()
    ]
    ids = list(REGIONS)
    rtt = {
        (a, b): 1.0 if REGIONS[a] == REGIONS[b] else 200.0
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
    }
    return Genesis.build(nodes, rtt)


@pytest.fixture
def chain() -> MeshChain:
    c = MeshChain()
    regions = c.register_genesis(make_genesis())
    assert regions == REGIONS  # RTT-only clustering recovers the four regions
    c.set_params(witness_count=3, commit_blocks=3, reveal_blocks=3)
    return c


def join(
    chain: MeshChain, name: str, anchor: str = "g00", stake: int = ETHER
) -> tuple[int, list[str]]:
    rid = chain.join(Node(name, location=Location(50.0, 8.0)), anchor, stake)
    chain.publish_randomness()
    return rid, chain.assign_witnesses(rid)


def honest_round(
    chain: MeshChain,
    rid: int,
    name: str,
    rtt: dict[str, float],
    lie: dict[str, float] | None = None,
) -> None:
    """Every participant commits and reveals; ``lie`` overrides what a participant reports."""
    lie = lie or {}
    ms = []
    for p, v in rtt.items():
        ms.append(Measurement(p, name, lie.get(p, v)))
        ms.append(Measurement(name, p, lie.get(name, v)))
    for m in ms:
        chain.commit(rid, m)
    chain.to_reveal_phase(rid)
    for m in ms:
        chain.reveal(rid, m)
    chain.to_round_end(rid)


def rtts_for(
    chain: MeshChain, rid: int, witnesses: list[str], anchor: str = "g00"
) -> dict[str, float]:
    out = {w: 80.0 for w in witnesses}
    out[anchor] = 1.0
    return out


def test_genesis_registration(chain: MeshChain) -> None:
    assert chain.registry.sealed() and chain.registry.region_count() == 4
    assert chain.status("g21") == STATUS_TRUSTED and chain.region("g21") == 2
    with boa.reverts("genesis sealed"):
        chain.registry.register_genesis(chain.address("x"), 0, 0, 0, 0)


def test_join_requires_stake_and_genesis_anchor(chain: MeshChain) -> None:
    with boa.reverts("stake too low"):
        chain.join(Node("a", location=Location(0, 0)), "g00", ETHER - 1)
    rid, _ = join(chain, "a")
    with boa.reverts("anchor must be a genesis node"):
        chain.join(Node("b", location=Location(0, 0)), "a")
    assert chain.status("a") == STATUS_PENDING and chain.region("a") == 0
    assert rid == 0


def test_witnesses_need_future_randomness(chain: MeshChain) -> None:
    rid = chain.join(Node("a", location=Location(0, 0)), "g00")
    with boa.reverts("randomness not available yet"):
        chain.assign_witnesses(rid)
    chain.publish_randomness()
    ws = chain.assign_witnesses(rid)
    with boa.reverts("already assigned"):
        chain.assign_witnesses(rid)
    assert len(ws) == 3 and "a" not in ws and "g00" not in ws


def test_witnesses_come_from_distinct_regions(chain: MeshChain) -> None:
    for i in range(200):
        seed = hashlib.sha256(str(i).encode()).digest()
        ws = chain.preview_witnesses(seed, 4, exclude=["g00"])
        assert len({REGIONS[w] for w in ws}) == 4
        assert "g00" not in ws


def test_more_witnesses_than_regions_starts_second_pass(chain: MeshChain) -> None:
    ws = chain.preview_witnesses(b"\x01" * 32, 8)
    regions = [REGIONS[w] for w in ws]
    assert len(set(ws)) == 8
    assert sorted(regions) == [0, 0, 1, 1, 2, 2, 3, 3]


def test_only_assigned_witnesses_may_measure(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    outsider = next(n for n in REGIONS if n not in ws and n != "g00")
    with chain.as_node(outsider), boa.reverts("not a witness"):
        chain.registry.commit(rid, chain.address("a"), b"\x01" * 32)
    with chain.as_node("a"), boa.reverts("target is not a witness"):
        chain.registry.commit(rid, chain.address(outsider), b"\x01" * 32)


def test_reveal_must_match_commitment(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    chain.commit(rid, Measurement(ws[0], "a", 80.0))
    chain.to_reveal_phase(rid)
    with chain.as_node(ws[0]), boa.reverts("commitment mismatch"):
        chain.registry.reveal(
            rid, chain.address("a"), encode_rtt(81.0), chain.salt(rid, ws[0], "a")
        )


def test_honest_newcomer_is_admitted_into_anchor_region(chain: MeshChain) -> None:
    rid, ws = join(chain, "a", anchor="g10")
    honest_round(chain, rid, "a", rtts_for(chain, rid, ws, "g10"))
    admitted, penalties = chain.finalize(rid)
    assert admitted and not any(penalties.values())
    assert chain.status("a") == STATUS_ACTIVE and chain.region("a") == 1
    assert chain.registry.region_size(1) == 4
    assert chain.accordance("a") == 1.0


def test_lying_newcomer_is_rejected_and_penalised(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    before = boa.env.get_balance(chain.address("a"))
    honest_round(chain, rid, "a", rtts_for(chain, rid, ws), lie={"a": 20.0})
    admitted, penalties = chain.finalize(rid)
    assert not admitted and chain.status("a") == STATUS_REJECTED
    assert penalties == {"a": ETHER // 10}  # 10 % penalty, rest refunded
    assert boa.env.get_balance(chain.address("a")) - before == ETHER - ETHER // 10
    assert chain.accordance("a") == 0.0


def test_anchor_must_be_the_closest(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    rtt = rtts_for(chain, rid, ws)
    rtt["g00"] = 150.0  # claims an anchor farther away than the witnesses
    honest_round(chain, rid, "a", rtt)
    admitted, penalties = chain.finalize(rid)
    assert not admitted and penalties["a"] == ETHER // 10


def admit(chain: MeshChain, name: str, anchor: str, stake: int = ETHER) -> None:
    rid, ws = join(chain, name, anchor, stake)
    honest_round(chain, rid, name, rtts_for(chain, rid, ws, anchor))
    assert chain.finalize(rid)[0]


def test_outvoted_non_genesis_witness_loses_stake_proportional_to_newcomer(
    chain: MeshChain,
) -> None:
    # grow the network so that a non-genesis node is drawn as a witness
    chain.set_params(witness_count=8)
    for i in range(4):
        admit(chain, f"m{i}", f"g{i}0", stake=4 * ETHER)
    for i in range(30):
        rid, ws = join(chain, f"n{i}", stake=2 * ETHER)
        liars = [w for w in ws if w.startswith("m")]
        if liars:
            break
    else:
        pytest.fail("no non-genesis witness drawn")
    liar = liars[0]
    honest_round(chain, rid, f"n{i}", rtts_for(chain, rid, ws), lie={liar: 300.0})
    admitted, penalties = chain.finalize(rid)
    assert admitted
    assert penalties == {liar: 2 * ETHER // 10}  # 10 % of the *newcomer's* stake
    assert chain.stake(liar) == 4 * ETHER - 2 * ETHER // 10
    assert chain.accordance(liar) < 1.0


def test_genesis_witness_is_always_right(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    honest_round(chain, rid, "a", rtts_for(chain, rid, ws), lie={ws[0]: 300.0})
    admitted, penalties = chain.finalize(rid)
    # 3 of 4 pairs agree (75 % < 80 % quorum): the newcomer is blamed, never genesis
    assert not admitted
    assert set(penalties) == {"a"}
    assert chain.stake(ws[0]) == 0 and chain.status(ws[0]) == STATUS_TRUSTED


def test_silent_newcomer_is_penalised(chain: MeshChain) -> None:
    rid, ws = join(chain, "a")
    ms = [Measurement(p, "a", v) for p, v in rtts_for(chain, rid, ws).items()]
    for m in ms:
        chain.commit(rid, m)
    chain.to_reveal_phase(rid)
    for m in ms:
        chain.reveal(rid, m)  # witnesses reveal, the newcomer never commits or reveals
    chain.to_round_end(rid)
    admitted, penalties = chain.finalize(rid)
    assert not admitted and penalties == {"a": ETHER // 10}


def test_selection_counts_stay_balanced_over_many_draws(chain: MeshChain) -> None:
    chain.set_params(witness_count=4)
    for i in range(60):
        join(chain, f"p{i}")
    counts = {n: int(chain.info(n).selected) for n in REGIONS}
    # 60 draws x 4 witnesses; with k == #regions every region is drawn every time, so
    # balancing happens *inside* a region (g00 is the anchor and is never drawn here)
    assert sum(counts.values()) == 240
    for r in range(4):
        inside = [c for n, c in counts.items() if REGIONS[n] == r and n != "g00"]
        assert max(inside) - min(inside) <= 2, (r, inside)


def test_owner_veto_refunds(chain: MeshChain) -> None:
    join(chain, "a")
    before = boa.env.get_balance(chain.address("a"))
    chain.registry.veto(chain.address("a"))
    assert chain.status("a") == STATUS_REJECTED
    assert boa.env.get_balance(chain.address("a")) - before == ETHER
    with chain.as_node("a"), boa.reverts("only owner"):
        chain.registry.veto(chain.address("a"))
