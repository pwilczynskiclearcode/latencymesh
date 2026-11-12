# Roadmap

## Done
- [x] `WitnessMesh.vy`: trusted genesis, 3 nearest nodes per node, lines 1-3, 4 witnesses per target
- [x] milky-way genesis (68 cloud regions) as YAML, loaded once and rewound per test
- [x] Tests an auditor would write for this stage: access control, sealed genesis, invalid
      links, lines checked against an independent definition, distinct witnesses, edge cases
      (tight cluster, smallest network), distribution of picks

## Decided, to be built next
- **Lines are defined by RTT rank from the target**, not by links of links: line 1 = the
  target's nearest nodes 1-3, line 2 = ranks 4-12, line 3 = ranks 13-39. This replaces the
  current definition, under which tight clusters (Sydney x3 + Melbourne) point only at each
  other: 20 of 68 milky-way targets have no line 2 and 35 have no line 3, so those witnesses
  silently become global ones.
- **The RTT graph lives off-chain; the contract keeps only its Merkle root.** A witness draw
  is checked against a proof instead of contract storage.
- **Off-chain, the graph is plain in-memory Python** - no database at this stage. Measured for
  1000 nodes (full 1000 x 1000 RTT matrix as lists of ints): 57 MB, built in about 2 s, every
  node's neighbours ranked in 0.7 s. With one Merkle leaf per node (its 39 nearest in rank
  order) the tree has 1000 leaves and depth 10: built in 0.15 s, a proof is 320 bytes plus an
  80 byte leaf. One leaf per pair would be 999 000 leaves and 55 s to build - not needed.

## Open design questions (found by simulation on milky-way)
- **Work is uneven.** If every node is a target once per cycle, a node does 1.8 to 7.7 jobs
  per cycle (mean 4). Nodes that nobody lists as a nearest node only get global work (~1.9).
  Decide whether rewards should be per job, or weighted so that remote nodes stay worthwhile.
- **Proportions.** 1 + 1 + 1 + 1 witnesses is fixed for now; make it a parameter.

## Next steps
- [ ] Where the seed comes from: a randomness beacon the target cannot influence
- [ ] Cycles: witnesses report what they observed about the target (commit, then reveal);
      tests fake the off-chain side - timeouts, resets, slow or partial replies, split networks
- [ ] Incentives for reporting truthfully (rewards first; no slashing yet)
- [ ] Joining after genesis: a new node proves its nearest nodes with RTT measured from both
      sides; links of existing nodes update when a closer node appears
- [ ] Real RTT measurements for milky-way (`rtt.yaml` with `source: measured`)
- [ ] Grow the genesis: remaining cloud regions, more providers, at least one node per country;
      decide how to treat edge networks where a workload's location cannot be pinned

## Ideas, not yet specified
These were prototyped in an earlier iteration of this repository (see git history before
version 0.2) and may return once the core needs them:

- Network coordinates (GNP, Vivaldi) to predict RTT between nodes that never measured each other
- Finding a newcomer's nearest nodes by probing only a few (Meridian-style search)
- Checking a claimed location against RTT (constraint-based geolocation, speed-of-light bounds)
- Clustering nodes into datacenter / city / region from RTT alone
- Region-stratified draws and balancing how often each node is selected (power of two choices)
- Selecting each node exactly once per epoch (swap-or-not shuffle)
- Detecting nodes of one operator from unusually low RTT between them (shared backbone)
- Recording how often a node's measurements agree with others ("RTT mutual accordance")
- Stake, and penalties proportional to it
