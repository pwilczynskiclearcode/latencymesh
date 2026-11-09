# pragma version ~=0.4.3
"""
@title latencymesh NodeRegistry
@notice Reference registry for latency-verified nodes:
        - a sealed genesis of trusted nodes with known locations,
        - staked joins of new nodes with a claimed location,
        - commit-reveal rounds of RTT measurements between nodes,
        - slashing of observers that commit but never reveal.
        Admission decisions are made off-chain by the protocol (see
        latencymesh.evm.protocol) and recorded here by the owner (an oracle /
        committee in a production deployment).
@dev    The commitment binds round, observer and target, so it cannot be copied
        from another node, and a 32-byte salt makes brute-forcing the small RTT
        space infeasible.
"""

flag Status:
    TRUSTED
    PENDING
    ACTIVE
    REJECTED

struct NodeInfo:
    status: Status
    lat_e6: int32
    lon_e6: int32
    asn: uint32
    stake: uint256
    joined_block: uint256

struct Round:
    commit_end: uint256
    reveal_end: uint256

event NodeRegistered:
    node: indexed(address)
    status: Status
    lat_e6: int32
    lon_e6: int32
    asn: uint32

event StatusChanged:
    node: indexed(address)
    status: Status

event RoundOpened:
    round_id: indexed(uint256)
    commit_end: uint256
    reveal_end: uint256

event Committed:
    round_id: indexed(uint256)
    observer: indexed(address)
    target: indexed(address)

event Revealed:
    round_id: indexed(uint256)
    observer: indexed(address)
    target: indexed(address)
    rtt_us: uint32

event Slashed:
    node: indexed(address)
    amount: uint256
    reporter: address

owner: public(address)
sealed: public(bool)
min_stake: public(uint256)
nodes: public(HashMap[address, NodeInfo])
node_count: public(uint256)
rounds: public(HashMap[uint256, Round])
round_count: public(uint256)
commitments: public(HashMap[uint256, HashMap[address, HashMap[address, bytes32]]])
revealed_rtt: public(HashMap[uint256, HashMap[address, HashMap[address, uint32]]])
is_revealed: public(HashMap[uint256, HashMap[address, HashMap[address, bool]]])
is_slashed: public(HashMap[uint256, HashMap[address, HashMap[address, bool]]])
slashed_total: public(uint256)


@deploy
def __init__(min_stake: uint256):
    self.owner = msg.sender
    self.min_stake = min_stake


@internal
@view
def _is_member(node: address) -> bool:
    return self.nodes[node].status in (Status.TRUSTED | Status.PENDING | Status.ACTIVE)


@internal
@pure
def _check_coords(lat_e6: int32, lon_e6: int32):
    assert lat_e6 >= -90_000_000 and lat_e6 <= 90_000_000, "bad latitude"
    assert lon_e6 >= -180_000_000 and lon_e6 <= 180_000_000, "bad longitude"


@internal
def _register(node: address, status: Status, lat_e6: int32, lon_e6: int32, asn: uint32, stake: uint256):
    assert self.nodes[node].joined_block == 0, "already registered"
    self._check_coords(lat_e6, lon_e6)
    self.nodes[node] = NodeInfo(
        status=status, lat_e6=lat_e6, lon_e6=lon_e6, asn=asn, stake=stake, joined_block=block.number
    )
    self.node_count += 1
    log NodeRegistered(node=node, status=status, lat_e6=lat_e6, lon_e6=lon_e6, asn=asn)


@external
def register_genesis(node: address, lat_e6: int32, lon_e6: int32, asn: uint32):
    assert msg.sender == self.owner, "only owner"
    assert not self.sealed, "genesis sealed"
    self._register(node, Status.TRUSTED, lat_e6, lon_e6, asn, 0)


@external
def seal_genesis():
    assert msg.sender == self.owner, "only owner"
    assert self.node_count > 0, "empty genesis"
    self.sealed = True


@external
@payable
def join(lat_e6: int32, lon_e6: int32, asn: uint32):
    assert self.sealed, "genesis not sealed"
    assert msg.value >= self.min_stake, "stake too low"
    self._register(msg.sender, Status.PENDING, lat_e6, lon_e6, asn, msg.value)


@external
def set_status(node: address, status: Status):
    """
    @notice Record the outcome of an off-chain admission check. A rejected node
            gets its remaining stake back: rejection is not proof of malice.
    """
    assert msg.sender == self.owner, "only owner"
    info: NodeInfo = self.nodes[node]
    assert info.status == Status.PENDING, "not pending"
    assert status == Status.ACTIVE or status == Status.REJECTED, "bad status"
    self.nodes[node].status = status
    log StatusChanged(node=node, status=status)
    if status == Status.REJECTED and info.stake > 0:
        self.nodes[node].stake = 0
        send(node, info.stake)


@external
def open_round(commit_blocks: uint256, reveal_blocks: uint256) -> uint256:
    assert msg.sender == self.owner, "only owner"
    assert commit_blocks > 0 and reveal_blocks > 0, "empty phase"
    rid: uint256 = self.round_count
    self.round_count = rid + 1
    commit_end: uint256 = block.number + commit_blocks
    self.rounds[rid] = Round(commit_end=commit_end, reveal_end=commit_end + reveal_blocks)
    log RoundOpened(round_id=rid, commit_end=commit_end, reveal_end=commit_end + reveal_blocks)
    return rid


@external
@pure
def measurement_hash(
    round_id: uint256, observer: address, target: address, rtt_us: uint32, salt: bytes32
) -> bytes32:
    return keccak256(abi_encode(round_id, observer, target, rtt_us, salt))


@external
def commit(round_id: uint256, target: address, commitment: bytes32):
    r: Round = self.rounds[round_id]
    assert r.commit_end > 0, "no such round"
    assert block.number <= r.commit_end, "commit phase over"
    assert self._is_member(msg.sender), "observer not a member"
    assert self._is_member(target), "target not a member"
    assert target != msg.sender, "self measurement"
    assert self.commitments[round_id][msg.sender][target] == empty(bytes32), "already committed"
    self.commitments[round_id][msg.sender][target] = commitment
    log Committed(round_id=round_id, observer=msg.sender, target=target)


@external
def reveal(round_id: uint256, target: address, rtt_us: uint32, salt: bytes32):
    r: Round = self.rounds[round_id]
    assert block.number > r.commit_end, "still in commit phase"
    assert block.number <= r.reveal_end, "reveal phase over"
    c: bytes32 = self.commitments[round_id][msg.sender][target]
    assert c != empty(bytes32), "nothing committed"
    assert not self.is_revealed[round_id][msg.sender][target], "already revealed"
    h: bytes32 = keccak256(abi_encode(round_id, msg.sender, target, rtt_us, salt))
    assert h == c, "commitment mismatch"
    self.is_revealed[round_id][msg.sender][target] = True
    self.revealed_rtt[round_id][msg.sender][target] = rtt_us
    log Revealed(round_id=round_id, observer=msg.sender, target=target, rtt_us=rtt_us)


@external
def slash_non_revealer(round_id: uint256, observer: address, target: address):
    """
    @notice Anyone may slash an observer that committed but did not reveal in time.
            Half of the observer's stake goes to the reporter, half stays locked.
    """
    r: Round = self.rounds[round_id]
    assert r.reveal_end > 0, "no such round"
    assert block.number > r.reveal_end, "reveal phase not over"
    assert self.commitments[round_id][observer][target] != empty(bytes32), "nothing committed"
    assert not self.is_revealed[round_id][observer][target], "was revealed"
    assert not self.is_slashed[round_id][observer][target], "already slashed"
    self.is_slashed[round_id][observer][target] = True
    amount: uint256 = self.nodes[observer].stake
    self.nodes[observer].stake = 0
    self.slashed_total += amount
    log Slashed(node=observer, amount=amount, reporter=msg.sender)
    if amount // 2 > 0:
        send(msg.sender, amount // 2)
