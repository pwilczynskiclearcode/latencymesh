# Roadmap

## 0.1 – foundations
- [x] Project skeleton, CI, lint, typing
- [ ] Node / measurement model, sparse RTT graph, genesis file format
- [ ] Synthetic latency world (fibre speed, routing detours, access delay, jitter, triangle-inequality violations)
- [ ] Discrete-event simulator with continuous re-probing under a probe budget
- [ ] Network coordinates: GNP, Vivaldi
- [ ] Selection strategies: uniform, balanced (power of two), dispersed (Hilbert systematic, farthest point),
      around a node, k nearest, iterative nearest-neighbour search
- [ ] Hierarchical clustering (datacenter / city / country / continent), CBG geolocation
- [ ] EVM layer: titanoboa harness, node registry with commit-reveal measurements, token audit kit

## 0.2 – adversaries
- [ ] Attack library: delay injection, selective delays, colluding witnesses, multi-site (proxied) nodes, Sybil regions
- [ ] Detection: embedding residuals, speed-of-light violations, witness disagreement scoring
- [ ] Scenario DSL + reports (precision/recall of detection, location error)

## 0.3 – protocols
- [ ] Join protocol: VRF/beacon-chosen witnesses, commit-reveal, multi-phase nearest-neighbour discovery
- [ ] Merkle commitments of off-chain measurement tables, fraud proofs
- [ ] Re-measurement audits and slashing
- [ ] Gas / storage cost reports per protocol variant

## Later
- [ ] Import real latency datasets (e.g. public ping matrices) as ground truth
- [ ] Meridian rings and Swap-or-not epoch permutations on-chain
- [ ] Larger worlds (10k+ nodes) with vectorised sampling
