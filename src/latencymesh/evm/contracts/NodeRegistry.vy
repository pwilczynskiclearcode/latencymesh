# pragma version ~=0.4.3
"""
@title latencymesh NodeRegistry
@notice Registry of latency-verified nodes with on-chain selection of
        geographically diverse witnesses.

        * Genesis: trusted nodes, each assigned to a region by the deployer
          (regions are derived off-chain from the genesis RTT matrix only).
          Genesis nodes are always considered right and are never penalised.
        * Join: a newcomer stakes and names a genesis *anchor* - the trusted
          node it is closest to. Its region becomes the anchor's region.
        * Witness selection: once the beacon publishes randomness for the
          round fixed at join time, anyone can trigger the draw. The contract
          picks witnesses from distinct regions (weighted by region size^alpha)
          and, inside a region, the less frequently selected of two random
          members (power of two choices). The newcomer cannot influence who
          measures it.
        * Measurement: newcomer <-> each witness and newcomer <-> anchor are
          measured in both directions and committed, then revealed.
        * Finalisation (permissionless): pairs agree when their RTTs differ by
          at most max(abs, rel * min). The newcomer is admitted when a quorum of
          pairs agree and the anchor is at least as close as every witness.
          A non-genesis participant that did not reveal, or reported a
          discrepancy the majority did not observe, loses a share of its stake
          proportional to the newcomer's stake; everybody's agreement record ("RTT mutual
          accordance") is updated on-chain.
@dev    Commitments bind round, observer and target together with a 32-byte
        salt, so they can be neither copied between observers nor
        brute-forced.
"""

interface IBeacon:
    def latest_round() -> uint256: view
    def randomness(round: uint256) -> bytes32: view

flag Status:
    TRUSTED
    PENDING
    ACTIVE
    REJECTED

MAX_REGIONS: constant(uint256) = 128
MAX_MEMBERS: constant(uint256) = 4096
MAX_WITNESSES: constant(uint256) = 32
BPS: constant(uint256) = 10_000

# region weighting for witness draws: size ** alpha
WEIGHT_REGION_UNIFORM: constant(uint8) = 0  # alpha = 0, every region equally likely
WEIGHT_SQRT: constant(uint8) = 1  # alpha = 1/2
WEIGHT_NODE_UNIFORM: constant(uint8) = 2  # alpha = 1, every node equally likely

struct NodeInfo:
    status: Status
    region: uint256
    lat_e6: int32
    lon_e6: int32
    asn: uint32
    stake: uint256
    joined_block: uint256
    selected: uint256
    observations: uint256
    agreements: uint256

struct JoinRound:
    newcomer: address
    anchor: address
    beacon_round: uint256
    commit_end: uint256
    reveal_end: uint256
    witnesses: DynArray[address, MAX_WITNESSES]
    finalized: bool

struct Params:
    witness_count: uint256
    commit_blocks: uint256
    reveal_blocks: uint256
    tolerance_bps: uint256
    tolerance_abs_us: uint256
    quorum_bps: uint256
    penalty_bps: uint256
    weight_mode: uint8

event NodeRegistered:
    node: indexed(address)
    status: Status
    region: uint256

event WitnessesAssigned:
    round_id: indexed(uint256)
    newcomer: indexed(address)
    witnesses: DynArray[address, MAX_WITNESSES]

event Committed:
    round_id: indexed(uint256)
    observer: indexed(address)
    target: indexed(address)

event Revealed:
    round_id: indexed(uint256)
    observer: indexed(address)
    target: indexed(address)
    rtt_us: uint32

event Penalised:
    round_id: indexed(uint256)
    node: indexed(address)
    amount: uint256

event Finalised:
    round_id: indexed(uint256)
    newcomer: indexed(address)
    admitted: bool
    agreeing: uint256
    decided: uint256

owner: public(address)
beacon: public(IBeacon)
sealed: public(bool)
min_stake: public(uint256)
params: public(Params)

nodes: public(HashMap[address, NodeInfo])
node_count: public(uint256)
region_count: public(uint256)
region_members: HashMap[uint256, DynArray[address, MAX_MEMBERS]]

rounds: HashMap[uint256, JoinRound]
round_count: public(uint256)
pending_round: public(HashMap[address, uint256])
commitments: public(HashMap[uint256, HashMap[address, HashMap[address, bytes32]]])
revealed_rtt: public(HashMap[uint256, HashMap[address, HashMap[address, uint32]]])
is_revealed: public(HashMap[uint256, HashMap[address, HashMap[address, bool]]])
slashed_total: public(uint256)


@deploy
def __init__(beacon: IBeacon, min_stake: uint256):
    self.owner = msg.sender
    self.beacon = beacon
    self.min_stake = min_stake
    self.params = Params(
        witness_count=8,
        commit_blocks=10,
        reveal_blocks=10,
        tolerance_bps=2_000,
        tolerance_abs_us=1_500,
        quorum_bps=8_000,
        penalty_bps=1_000,
        weight_mode=WEIGHT_SQRT,
    )


# -- administration ------------------------------------------------------------

@external
def set_params(p: Params):
    assert msg.sender == self.owner, "only owner"
    assert p.witness_count > 0 and p.witness_count <= MAX_WITNESSES, "bad witness count"
    assert p.commit_blocks > 0 and p.reveal_blocks > 0, "empty phase"
    assert p.quorum_bps <= BPS and p.penalty_bps <= BPS, "bad bps"
    assert p.weight_mode <= WEIGHT_NODE_UNIFORM, "bad weight mode"
    self.params = p


@internal
@pure
def _check_coords(lat_e6: int32, lon_e6: int32):
    assert lat_e6 >= -90_000_000 and lat_e6 <= 90_000_000, "bad latitude"
    assert lon_e6 >= -180_000_000 and lon_e6 <= 180_000_000, "bad longitude"


@external
def register_genesis(node: address, region: uint256, lat_e6: int32, lon_e6: int32, asn: uint32):
    assert msg.sender == self.owner, "only owner"
    assert not self.sealed, "genesis sealed"
    assert self.nodes[node].joined_block == 0, "already registered"
    assert region <= self.region_count and region < MAX_REGIONS, "regions must be contiguous"
    self._check_coords(lat_e6, lon_e6)
    if region == self.region_count:
        self.region_count += 1
    self.nodes[node] = NodeInfo(
        status=Status.TRUSTED,
        region=region,
        lat_e6=lat_e6,
        lon_e6=lon_e6,
        asn=asn,
        stake=0,
        joined_block=block.number,
        selected=0,
        observations=0,
        agreements=0,
    )
    self.region_members[region].append(node)
    self.node_count += 1
    log NodeRegistered(node=node, status=Status.TRUSTED, region=region)


@external
def seal_genesis():
    assert msg.sender == self.owner, "only owner"
    assert self.node_count > 0, "empty genesis"
    self.sealed = True


# -- joining -------------------------------------------------------------------

@external
@payable
def join(anchor: address, lat_e6: int32, lon_e6: int32, asn: uint32) -> uint256:
    """
    @notice Stake and request admission. ``anchor`` must be the genesis node the
            newcomer is closest to; witnesses are drawn from randomness the
            beacon publishes only after this call.
    """
    assert self.sealed, "genesis not sealed"
    assert msg.value >= self.min_stake, "stake too low"
    assert self.nodes[msg.sender].joined_block == 0, "already registered"
    assert self.nodes[anchor].status == Status.TRUSTED, "anchor must be a genesis node"
    self._check_coords(lat_e6, lon_e6)
    self.nodes[msg.sender] = NodeInfo(
        status=Status.PENDING,
        region=self.nodes[anchor].region,
        lat_e6=lat_e6,
        lon_e6=lon_e6,
        asn=asn,
        stake=msg.value,
        joined_block=block.number,
        selected=0,
        observations=0,
        agreements=0,
    )
    self.node_count += 1
    rid: uint256 = self.round_count
    self.round_count = rid + 1
    self.rounds[rid] = JoinRound(
        newcomer=msg.sender,
        anchor=anchor,
        beacon_round=staticcall self.beacon.latest_round() + 1,
        commit_end=0,
        reveal_end=0,
        witnesses=[],
        finalized=False,
    )
    self.pending_round[msg.sender] = rid
    log NodeRegistered(node=msg.sender, status=Status.PENDING, region=self.nodes[anchor].region)
    return rid


# -- witness selection -----------------------------------------------------------

@internal
@view
def _region_weight(r: uint256, mode: uint8) -> uint256:
    n: uint256 = len(self.region_members[r])
    if n == 0:
        return 0
    if mode == WEIGHT_REGION_UNIFORM:
        return 1
    if mode == WEIGHT_SQRT:
        return isqrt(n * 1_000_000)
    return n


@internal
@pure
def _rand(seed: bytes32, a: uint256, b: uint256) -> uint256:
    return convert(keccak256(abi_encode(seed, a, b)), uint256)


@internal
@view
def _contains(xs: DynArray[address, MAX_WITNESSES + 2], x: address) -> bool:
    for y: address in xs:
        if y == x:
            return True
    return False


@internal
@view
def _pick_in_region(
    seed: bytes32, slot: uint256, r: uint256, taken: DynArray[address, MAX_WITNESSES + 2]
) -> address:
    """Power of two choices among eligible members; up to 8 attempts."""
    members: uint256 = len(self.region_members[r])
    best: address = empty(address)
    for attempt: uint256 in range(8):
        a: address = self.region_members[r][self._rand(seed, slot, 2 * attempt + 1000) % members]
        b: address = self.region_members[r][self._rand(seed, slot, 2 * attempt + 1001) % members]
        a_ok: bool = not self._contains(taken, a)
        b_ok: bool = not self._contains(taken, b)
        if a_ok and b_ok:
            if self.nodes[b].selected < self.nodes[a].selected:
                return b
            return a
        if a_ok:
            best = a
        elif b_ok:
            best = b
        if best != empty(address):
            return best
    # deterministic fallback: first eligible member
    for i: uint256 in range(MAX_MEMBERS):
        if i == members:
            break
        m: address = self.region_members[r][i]
        if not self._contains(taken, m):
            return m
    return empty(address)


@internal
@view
def _select(
    seed: bytes32, k: uint256, exclude: DynArray[address, MAX_WITNESSES + 2], mode: uint8
) -> DynArray[address, MAX_WITNESSES]:
    out: DynArray[address, MAX_WITNESSES] = []
    taken: DynArray[address, MAX_WITNESSES + 2] = exclude
    used: bool[MAX_REGIONS] = empty(bool[MAX_REGIONS])
    regions: uint256 = self.region_count
    for slot: uint256 in range(4 * MAX_WITNESSES):
        if len(out) == k:
            break
        total: uint256 = 0
        for r: uint256 in range(MAX_REGIONS):
            if r == regions:
                break
            if not used[r]:
                total += self._region_weight(r, mode)
        if total == 0:
            # every region was used once: start a new pass over all regions
            used = empty(bool[MAX_REGIONS])
            for r: uint256 in range(MAX_REGIONS):
                if r == regions:
                    break
                total += self._region_weight(r, mode)
            if total == 0:
                break
        x: uint256 = self._rand(seed, slot, 1) % total
        chosen: uint256 = 0
        for r: uint256 in range(MAX_REGIONS):
            if r == regions:
                break
            if used[r]:
                continue
            w: uint256 = self._region_weight(r, mode)
            if x < w:
                chosen = r
                break
            x -= w
        used[chosen] = True
        node: address = self._pick_in_region(seed, slot, chosen, taken)
        if node != empty(address):
            out.append(node)
            taken.append(node)
    return out


@external
@view
def preview_witnesses(
    seed: bytes32, k: uint256, exclude: DynArray[address, MAX_WITNESSES + 2]
) -> DynArray[address, MAX_WITNESSES]:
    """Read-only draw for simulations and audits; does not update selection counts."""
    assert k > 0 and k <= MAX_WITNESSES, "bad k"
    return self._select(seed, k, exclude, self.params.weight_mode)


@external
def assign_witnesses(round_id: uint256) -> DynArray[address, MAX_WITNESSES]:
    """
    @notice Permissionless: draw the witnesses of a join round once the beacon
            has published the randomness fixed at join time.
    """
    jr: JoinRound = self.rounds[round_id]
    assert jr.newcomer != empty(address), "no such round"
    assert jr.commit_end == 0, "already assigned"
    rnd: bytes32 = staticcall self.beacon.randomness(jr.beacon_round)
    assert rnd != empty(bytes32), "randomness not available yet"
    seed: bytes32 = keccak256(abi_encode(rnd, round_id, jr.newcomer))
    exclude: DynArray[address, MAX_WITNESSES + 2] = [jr.newcomer, jr.anchor]
    p: Params = self.params
    ws: DynArray[address, MAX_WITNESSES] = self._select(seed, p.witness_count, exclude, p.weight_mode)
    for w: address in ws:
        self.nodes[w].selected += 1
    self.rounds[round_id].witnesses = ws
    self.rounds[round_id].commit_end = block.number + p.commit_blocks
    self.rounds[round_id].reveal_end = block.number + p.commit_blocks + p.reveal_blocks
    log WitnessesAssigned(round_id=round_id, newcomer=jr.newcomer, witnesses=ws)
    return ws


@external
@view
def round_info(round_id: uint256) -> JoinRound:
    return self.rounds[round_id]


# -- commit / reveal -------------------------------------------------------------

@internal
@view
def _is_participant(jr: JoinRound, node: address) -> bool:
    if node == jr.anchor:
        return True
    for w: address in jr.witnesses:
        if w == node:
            return True
    return False


@external
@pure
def measurement_hash(
    round_id: uint256, observer: address, target: address, rtt_us: uint32, salt: bytes32
) -> bytes32:
    return keccak256(abi_encode(round_id, observer, target, rtt_us, salt))


@external
def commit(round_id: uint256, target: address, commitment: bytes32):
    jr: JoinRound = self.rounds[round_id]
    assert jr.commit_end > 0, "witnesses not assigned"
    assert block.number <= jr.commit_end, "commit phase over"
    if msg.sender == jr.newcomer:
        assert self._is_participant(jr, target), "target is not a witness"
    else:
        assert target == jr.newcomer, "witnesses only measure the newcomer"
        assert self._is_participant(jr, msg.sender), "not a witness"
    assert self.commitments[round_id][msg.sender][target] == empty(bytes32), "already committed"
    self.commitments[round_id][msg.sender][target] = commitment
    log Committed(round_id=round_id, observer=msg.sender, target=target)


@external
def reveal(round_id: uint256, target: address, rtt_us: uint32, salt: bytes32):
    jr: JoinRound = self.rounds[round_id]
    assert jr.commit_end > 0, "witnesses not assigned"
    assert block.number > jr.commit_end, "still in commit phase"
    assert block.number <= jr.reveal_end, "reveal phase over"
    c: bytes32 = self.commitments[round_id][msg.sender][target]
    assert c != empty(bytes32), "nothing committed"
    assert not self.is_revealed[round_id][msg.sender][target], "already revealed"
    assert keccak256(abi_encode(round_id, msg.sender, target, rtt_us, salt)) == c, "commitment mismatch"
    self.is_revealed[round_id][msg.sender][target] = True
    self.revealed_rtt[round_id][msg.sender][target] = rtt_us
    log Revealed(round_id=round_id, observer=msg.sender, target=target, rtt_us=rtt_us)


# -- finalisation ----------------------------------------------------------------

@internal
@view
def _agree(a: uint256, b: uint256) -> bool:
    lo: uint256 = min(a, b)
    diff: uint256 = max(a, b) - lo
    return diff <= max(self.params.tolerance_abs_us, lo * self.params.tolerance_bps // BPS)


@internal
def _penalise(round_id: uint256, node: address, amount: uint256):
    stake: uint256 = self.nodes[node].stake
    taken: uint256 = min(stake, amount)
    if taken > 0:
        self.nodes[node].stake = stake - taken
        self.slashed_total += taken
    log Penalised(round_id=round_id, node=node, amount=taken)


@external
def finalize(round_id: uint256) -> bool:
    """
    @notice Permissionless: evaluate a join round after its reveal phase.
    @return True when the newcomer was admitted.
    """
    jr: JoinRound = self.rounds[round_id]
    assert jr.commit_end > 0, "witnesses not assigned"
    assert block.number > jr.reveal_end, "reveal phase not over"
    assert not jr.finalized, "already finalised"
    assert self.nodes[jr.newcomer].status == Status.PENDING, "not pending"
    self.rounds[round_id].finalized = True
    n: address = jr.newcomer

    # outcome per participant: 0 = witness did not reveal, 1 = agree, 2 = disagree
    participants: DynArray[address, MAX_WITNESSES + 1] = []
    outcome: DynArray[uint8, MAX_WITNESSES + 1] = []
    min_witness_rtt: uint256 = max_value(uint256)
    agreeing: uint256 = 0
    decided: uint256 = 0
    for w: address in jr.witnesses:
        participants.append(w)
    participants.append(jr.anchor)
    for p: address in participants:
        if not self.is_revealed[round_id][p][n]:
            outcome.append(0)
            continue
        decided += 1
        if not self.is_revealed[round_id][n][p]:
            outcome.append(2)  # the newcomer withheld its side
            continue
        a: uint256 = convert(self.revealed_rtt[round_id][p][n], uint256)
        b: uint256 = convert(self.revealed_rtt[round_id][n][p], uint256)
        if self._agree(a, b):
            outcome.append(1)
            agreeing += 1
            if p != jr.anchor:
                min_witness_rtt = min(min_witness_rtt, a)
        else:
            outcome.append(2)

    # majority of the measurement pairs decides who was right in each pair
    pairs_agree: bool = decided > 0 and agreeing * BPS >= self.params.quorum_bps * decided
    majority: uint8 = 1 if pairs_agree else 2

    # the anchor (a genesis node) must agree and be at least as close as every
    # agreeing witness; a silent anchor blocks admission without blaming anyone
    anchor_outcome: uint8 = outcome[len(outcome) - 1]
    newcomer_at_fault: bool = not pairs_agree or anchor_outcome == 2
    admitted: bool = pairs_agree and anchor_outcome == 1
    if admitted:
        anchor_rtt: uint256 = convert(self.revealed_rtt[round_id][jr.anchor][n], uint256)
        if anchor_rtt > min_witness_rtt + self.params.tolerance_abs_us:
            admitted = False
            newcomer_at_fault = True

    penalty: uint256 = self.nodes[n].stake * self.params.penalty_bps // BPS
    for i: uint256 in range(MAX_WITNESSES + 1):
        if i == len(participants):
            break
        p: address = participants[i]
        o: uint8 = outcome[i]
        if o != 0:
            self.nodes[p].observations += 1
            if o == majority:
                self.nodes[p].agreements += 1
        # Genesis nodes are always right. Others lose stake when they stayed silent,
        # or reported a discrepancy that the majority did not observe. Agreeing with a
        # rejected newcomer is not penalised (a lie below the tolerance looks like
        # agreement up close) but earns no accordance credit either.
        if self.nodes[p].status != Status.TRUSTED and (o == 0 or (o == 2 and majority == 1)):
            self._penalise(round_id, p, penalty)

    self.nodes[n].observations += decided
    self.nodes[n].agreements += agreeing  # pairs where both sides saw the same RTT
    if admitted:
        self.nodes[n].status = Status.ACTIVE
        self.region_members[self.nodes[n].region].append(n)
    else:
        self.nodes[n].status = Status.REJECTED
        if newcomer_at_fault:
            self._penalise(round_id, n, penalty)
        refund: uint256 = self.nodes[n].stake
        if refund > 0:
            self.nodes[n].stake = 0
            send(n, refund)
    log Finalised(round_id=round_id, newcomer=n, admitted=admitted, agreeing=agreeing, decided=decided)
    return admitted


@external
def veto(node: address):
    """
    @notice Owner veto of a pending node, e.g. when an off-chain location check
            fails. The full stake is refunded: a veto is not proof of malice.
    """
    assert msg.sender == self.owner, "only owner"
    assert self.nodes[node].status == Status.PENDING, "not pending"
    self.nodes[node].status = Status.REJECTED
    self.rounds[self.pending_round[node]].finalized = True
    refund: uint256 = self.nodes[node].stake
    self.nodes[node].stake = 0
    if refund > 0:
        send(node, refund)


# -- views -------------------------------------------------------------------------

@external
@view
def accordance_bps(node: address) -> uint256:
    """Share of evaluated measurements in which the node sided with the decision."""
    info: NodeInfo = self.nodes[node]
    if info.observations == 0:
        return BPS
    return info.agreements * BPS // info.observations


@external
@view
def region_size(region: uint256) -> uint256:
    return len(self.region_members[region])


@external
@view
def region_member(region: uint256, i: uint256) -> address:
    return self.region_members[region][i]
