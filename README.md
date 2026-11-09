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
uv run pytest
```

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

## License

MIT, see [LICENSE](LICENSE).
