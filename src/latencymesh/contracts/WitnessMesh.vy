# pragma version ~=0.4.3
"""
@title WitnessMesh
@notice A network of nodes that know their nearest neighbours (by measured
        round-trip time, RTT) and a rule for choosing who watches whom.

        Every node stores its K = 3 nearest nodes. From these links the
        contract derives, for any target node, three "lines" around it:

          line 1 - the target's 3 nearest nodes
          line 2 - the nearest nodes of line 1, minus the target and line 1
          line 3 - the nearest nodes of line 2, minus the target, line 1 and 2

        Four witnesses are chosen for a target from a random seed:

          1 from line 1   (sees the target from right next to it)
          1 from line 2   (the next ring out)
          1 from line 3   (the ring after that)
          1 from the whole network (a distant, global view)

        When a line is empty (a tight cluster whose members are all each
        other's nearest nodes has no line 2) its witness is drawn from the
        whole network instead, so a target always gets 4 distinct witnesses.

@dev    This first version only holds a trusted genesis loaded by the owner.
        The seed is an argument: where it comes from (a randomness beacon)
        and how new nodes join are later steps - see ROADMAP.md.
"""

K: constant(uint256) = 3  # nearest neighbours stored per node
LINE2_MAX: constant(uint256) = 9  # K * K
LINE3_MAX: constant(uint256) = 27  # K * K * K
SEEN_MAX: constant(uint256) = 13  # target + line 1 + line 2
WITNESSES: constant(uint256) = 4
MAX_NODES: constant(uint256) = 1024

LINE_GLOBAL: constant(uint8) = 0  # witness drawn from the whole network

owner: public(address)
sealed: public(bool)

nodes: public(DynArray[address, MAX_NODES])
is_node: public(HashMap[address, bool])
has_links: public(HashMap[address, bool])
linked_count: public(uint256)

nearest: HashMap[address, address[K]]
nearest_rtt_us: HashMap[address, uint32[K]]

event NodeAdded:
    node: indexed(address)

event LinksSet:
    node: indexed(address)
    nearest: address[K]
    rtt_us: uint32[K]

event Sealed:
    node_count: uint256


@deploy
def __init__():
    self.owner = msg.sender


# -- genesis ---------------------------------------------------------------------

@external
def add_node(node: address):
    assert msg.sender == self.owner, "only owner"
    assert not self.sealed, "genesis sealed"
    assert node != empty(address), "zero address"
    assert not self.is_node[node], "already a node"
    self.is_node[node] = True
    self.nodes.append(node)
    log NodeAdded(node=node)


@external
def set_nearest(node: address, neighbours: address[K], rtt_us: uint32[K]):
    """
    @notice Record a node's K nearest nodes, closest first, with the measured
            RTT to each in microseconds.
    """
    assert msg.sender == self.owner, "only owner"
    assert not self.sealed, "genesis sealed"
    assert self.is_node[node], "unknown node"
    for i: uint256 in range(K):
        n: address = neighbours[i]
        assert self.is_node[n], "unknown neighbour"
        assert n != node, "node cannot be its own neighbour"
        assert rtt_us[i] > 0, "rtt must be positive"
        for j: uint256 in range(K):
            if j < i:
                assert neighbours[j] != n, "duplicate neighbour"
        if i > 0:
            assert rtt_us[i] >= rtt_us[i - 1], "neighbours must be sorted by rtt"
    if not self.has_links[node]:
        self.has_links[node] = True
        self.linked_count += 1
    self.nearest[node] = neighbours
    self.nearest_rtt_us[node] = rtt_us
    log LinksSet(node=node, nearest=neighbours, rtt_us=rtt_us)


@external
def seal():
    """
    @notice Freeze the genesis. Afterwards nobody, including the owner, can
            add nodes or change links through this version of the contract.
    """
    assert msg.sender == self.owner, "only owner"
    assert not self.sealed, "genesis sealed"
    assert len(self.nodes) > WITNESSES, "need at least 5 nodes"
    assert self.linked_count == len(self.nodes), "a node has no links"
    self.sealed = True
    log Sealed(node_count=len(self.nodes))


# -- reading the graph -------------------------------------------------------------

@external
@view
def node_count() -> uint256:
    return len(self.nodes)


@external
@view
def nearest_of(node: address) -> (address[K], uint32[K]):
    assert self.is_node[node], "unknown node"
    return self.nearest[node], self.nearest_rtt_us[node]


@internal
@view
def _line2(target: address, line1: address[K]) -> DynArray[address, LINE2_MAX]:
    out: DynArray[address, LINE2_MAX] = []
    for a: address in line1:
        for n: address in self.nearest[a]:
            if n != target and n not in line1 and n not in out:
                out.append(n)
    return out


@internal
@view
def _line3(
    target: address, line1: address[K], line2: DynArray[address, LINE2_MAX]
) -> DynArray[address, LINE3_MAX]:
    out: DynArray[address, LINE3_MAX] = []
    for a: address in line2:
        for n: address in self.nearest[a]:
            if n != target and n not in line1 and n not in line2 and n not in out:
                out.append(n)
    return out


@external
@view
def lines_of(
    target: address
) -> (address[K], DynArray[address, LINE2_MAX], DynArray[address, LINE3_MAX]):
    """
    @notice The three lines of nodes around a target (see the contract notice).
    """
    assert self.is_node[target], "unknown node"
    line1: address[K] = self.nearest[target]
    line2: DynArray[address, LINE2_MAX] = self._line2(target, line1)
    return line1, line2, self._line3(target, line1, line2)


# -- choosing witnesses --------------------------------------------------------------

@internal
@pure
def _rand(seed: bytes32, target: address, slot: uint256, attempt: uint256) -> uint256:
    return convert(keccak256(abi_encode(seed, target, slot, attempt)), uint256)


@internal
@view
def _from_network(
    seed: bytes32, target: address, slot: uint256, taken: DynArray[address, WITNESSES]
) -> address:
    """A random node that is neither the target nor already chosen."""
    count: uint256 = len(self.nodes)
    candidate: address = empty(address)
    # independent random tries keep the choice uniform over the usable nodes
    for attempt: uint256 in range(8):
        candidate = self.nodes[self._rand(seed, target, slot, attempt + 1) % count]
        if candidate != target and candidate not in taken:
            return candidate
    # practically never reached: walk on from the last try (at most 4 unusable nodes)
    start: uint256 = self._rand(seed, target, slot, 9) % count
    for step: uint256 in range(WITNESSES + 1):
        candidate = self.nodes[(start + step) % count]
        if candidate != target and candidate not in taken:
            return candidate
    raise "unreachable"  # seal() guarantees at least 5 nodes


@external
@view
def witnesses_for(target: address, seed: bytes32) -> (address[WITNESSES], uint8[WITNESSES]):
    """
    @notice Choose the 4 witnesses of `target` for the given random seed.
    @return The witnesses, and for each the line it was drawn from:
            1, 2 or 3, or 0 for a witness drawn from the whole network.
    """
    assert self.sealed, "genesis not sealed"
    assert self.is_node[target], "unknown node"
    line1: address[K] = self.nearest[target]
    line2: DynArray[address, LINE2_MAX] = self._line2(target, line1)
    line3: DynArray[address, LINE3_MAX] = self._line3(target, line1, line2)

    chosen: DynArray[address, WITNESSES] = []
    origin: uint8[WITNESSES] = empty(uint8[WITNESSES])

    chosen.append(line1[self._rand(seed, target, 0, 0) % K])
    origin[0] = 1

    if len(line2) > 0:
        chosen.append(line2[self._rand(seed, target, 1, 0) % len(line2)])
        origin[1] = 2
    else:
        chosen.append(self._from_network(seed, target, 1, chosen))

    if len(line3) > 0:
        chosen.append(line3[self._rand(seed, target, 2, 0) % len(line3)])
        origin[2] = 3
    else:
        chosen.append(self._from_network(seed, target, 2, chosen))

    chosen.append(self._from_network(seed, target, 3, chosen))

    return [chosen[0], chosen[1], chosen[2], chosen[3]], origin
