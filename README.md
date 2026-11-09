# latencymesh

**A Python framework for building and attacking systems that rely on network distance (RTT) between nodes,
with an in-process EVM so the on-chain part can be tested together with the network part.**

Many decentralised protocols need to answer questions such as:

* *Where is this new node really?* (proof of location, anti-Sybil)
* *Give me X random nodes that are far apart from each other* (geographically diverse witnesses)
* *Give me X random nodes around node A* / *the k nearest nodes to A*
* *Which nodes probably sit in the same datacenter, city, country or continent?*

`latencymesh` keeps a sparse, continuously re-measured RTT graph instead of a full `n × n` matrix,
embeds nodes into network coordinates and offers selection strategies whose long-run choice
frequency stays uniform. Everything runs from a seed, so experiments are reproducible, and attacks
(delay injection, colluding witnesses, proxied multi-site nodes) can be modelled as plain Python.

> Status: **pre-alpha**, under active development. APIs will change. See [ROADMAP.md](ROADMAP.md).

## Who is it for

* people designing proof-of-location / proof-of-latency / network-monitoring protocols,
* researchers who want to compare network-coordinate and nearest-neighbour algorithms under attack,
* smart-contract developers who need a reproducible, simulated network of nodes behind their contracts
  and a reusable audit test-suite for the token contracts deployed there.

## Built with

* Python ≥ 3.11, NumPy
* [titanoboa](https://github.com/vyperlang/titanoboa) as the in-process EVM, contracts written in Vyper
* pytest + Hypothesis for tests

## Getting started

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest          # verbose, with a branch-coverage report
```

## A taste

```python
from latencymesh.synth import WorldConfig, generate_world
from latencymesh.sim import Simulation
from latencymesh.coords import Vivaldi

world = generate_world(WorldConfig(n_nodes=500, n_trusted=100, seed=1))  # ground truth
sim = Simulation.from_genesis(world, probe_budget=4)  # what the network knows
viv = Vivaldi(dims=5)
sim.on_measurement(viv.observe)  # coordinates update online
sim.run(3600)  # one simulated hour
print(viv.predict("n00120", "n00377"), world.base_rtt("n00120", "n00377"))
```

### Geographically diverse witnesses, drawn by the contract

`NodeRegistry.vy` selects witnesses on-chain, from public randomness the newcomer cannot
influence, using measured RTT as the only input:

1. **Regions** – the genesis is split into regions by clustering its RTT matrix
   (`cluster.regions_from_rtt`). A joining node names its nearest genesis node as *anchor* and
   inherits its region; the contract checks that the anchor really is at least as close as
   every witness.
2. **Draw** – witnesses come from *distinct* regions (weighted by `size^α`: per-region fair,
   √size, or per-node fair). Inside a region the less frequently selected of two random
   members is taken (power of two choices), so selection counts stay even over unlimited draws.
3. **Both directions** – newcomer and witnesses measure each other, commit, then reveal.
   Neither side has to trust the other.
4. **Accordance** – pairs agree when `|a − b| ≤ max(abs, rel·min)`. A quorum admits the
   newcomer. Genesis nodes are always right. Any other node that stays silent, or reports a
   discrepancy the majority did not see, loses a share of its stake proportional to the
   newcomer's stake. Each node's record of agreement is kept on-chain (`accordance_bps`).

On a 72-node synthetic world, 45 % of plain random 8-witness sets contain two witnesses closer
than 300 km to each other; none of the contract's draws do (median closest pair 2 057 km vs
328 km). Flooding one region with 20 extra nodes does not get more than one of them into any
draw.

[`examples/join_attacks.py`](examples/join_attacks.py) runs the full join protocol:

```
honest       ADMITTED  region 0
             witnesses: Ashburn, Bogota, Dallas, Dubai, Jakarta, Milan, Sao Paulo, Seattle
rtt-liar     REJECTED  penalised: rtt-liar 0.10 ETH
fake-tokyo   VETOED    claimed location contradicts witness RTTs
delay-tokyo  VETOED    claimed location contradicts witness RTTs
```

What RTT can and cannot prove: a small RTT proves proximity; a large RTT proves nothing,
because delay can always be added. Distance between witnesses is therefore established through
their anchors (small, two-sided RTTs to genesis nodes), not through RTTs between the witnesses
themselves. Independence of operators (e.g. several nodes of one cloud provider in different
regions) cannot be proven from RTT; see the roadmap.

### Modules

| Module | What it does |
|---|---|
| `model`, `graph`, `genesis` | nodes (ASN, lat/lon, country, city, datacenter), measurements, sparse RTT graph, genesis file |
| `synth` | synthetic world: fibre propagation, routing detours, TIVs, access delay, IXP hops, jitter |
| `sim` | discrete-event engine, probe policies, hooks for lying/lazy observers |
| `coords` | GNP and Vivaldi network coordinates |
| `select` | uniform, balanced, Hilbert-systematic, dispersed, around, k-nearest, iterative nearest, swap-or-not |
| `cluster`, `geoloc` | datacenter/city/country/continent clustering, CBG geolocation and claim verification |
| `evm` | titanoboa harness, `NodeRegistry.vy` (regions, diverse witness draws, commit-reveal, accordance and penalties), `MockBeacon.vy`, join protocol, ERC-20 audit kit |

### Auditing token contracts

```python
from latencymesh.evm.audit import audit_erc20

report = audit_erc20(my_token, holder=deployer)
assert report.ok, report.summary()
```

The suite checks EIP-20 behaviour and common pitfalls (allowance accounting, self-transfer
minting, fee-on-transfer, events, zero-value transfers, transfers to the zero address) and can be
extended with project-specific checks.

## Trust model in one paragraph

A network starts from a **genesis**: a set of trusted nodes whose locations (lat/lon, ASN, country,
datacenter…) and pairwise RTTs are known. Everyone who joins later implicitly trusts this starting
state, much like weak subjectivity checkpoints in proof-of-stake chains. New nodes are positioned only
relative to nodes that are already accepted.

## Algorithms (implemented from the papers, no third-party code)

| Area | Algorithm | Reference |
|---|---|---|
| Coordinates | GNP (landmark-based) | Ng & Zhang, INFOCOM 2002 |
| Coordinates | Vivaldi (with height vectors) | Dabek et al., SIGCOMM 2004 |
| Nearest node | Meridian-style iterative search | Wong, Slivkins & Sirer, SIGCOMM 2005 |
| Geolocation | Constraint-Based Geolocation (CBG) | Gueye et al., IEEE/ACM ToN 2006 |
| Selection | Power-of-two-choices balancing | Azar et al. 1999; Mitzenmacher 2001 |
| Selection | Systematic sampling along a Hilbert curve | classic survey-sampling technique |
| Shuffling | Swap-or-not shuffle | Hoang, Morris & Rogaway, CRYPTO 2012 |

Where every algorithm comes from and why its use is compatible with the MIT license is
documented in [docs/REFERENCES.md](docs/REFERENCES.md).

## License

MIT, see [LICENSE](LICENSE).
