# Algorithms, sources and licensing

Every algorithm in latencymesh was **implemented from its published description**. No source
code from the papers' authors or from other implementations was copied. Copyright protects the
*text* of a paper and any *code* published with it, not the method it describes, so writing an
independent implementation and releasing it under MIT is allowed.

Patents are a separate question from copyright. The table records what is known; it is not a
legal opinion. Before deploying commercially, run a freedom-to-operate check on the components
you ship.

| Component | Module | Source | How it is used | Notes |
|---|---|---|---|---|
| Speed-of-light bound on RTT | `geo` | physics: light in fibre travels at about 2/3 c | own implementation | not protectable |
| Haversine distance | `geo` | classic spherical trigonometry | own implementation | public domain knowledge |
| GNP | `coords.gnp` | T. S. E. Ng, H. Zhang, *Predicting Internet Network Distance with Coordinates-Based Approaches*, INFOCOM 2002 | idea only; optimiser (MDS init + projected Adam) is our own | no known patent |
| Classical MDS | `coords.gnp` | W. S. Torgerson, 1952 | textbook method | public domain knowledge |
| Adam optimiser | `coords.gnp` | D. P. Kingma, J. Ba, 2014 | textbook update rule | widely used, no known patent |
| Vivaldi | `coords.vivaldi` | F. Dabek, R. Cox, F. Kaashoek, R. Morris, *Vivaldi: A Decentralized Network Coordinate System*, SIGCOMM 2004 | update rule from the paper, own code | no known patent |
| Meridian-style search | `select.iterative_nearest` | B. Wong, A. Slivkins, E. G. Sirer, *Meridian*, SIGCOMM 2005 | only the triangle-inequality pruning idea; the multi-phase procedure is the project's own | no known patent |
| Power of d choices | `select.balanced` | Azar, Broder, Karlin, Upfal 1999; Mitzenmacher 2001 | balls-into-bins result | no known patent |
| Hilbert curve | `select._hilbert_d` | D. Hilbert, 1891; standard xy→d conversion | textbook algorithm | public domain knowledge |
| Systematic sampling | `select.hilbert_systematic` | classic survey sampling | textbook method | public domain knowledge |
| Farthest-point sampling | `select.dispersed` | Gonzalez 1985 (k-center greedy) | textbook method | public domain knowledge |
| Swap-or-not shuffle | `select.swap_or_not_index` | V. T. Hoang, B. Morris, P. Rogaway, CRYPTO 2012; byte layout follows Ethereum consensus-specs `compute_shuffled_index` | own code following the spec's structure | Ethereum consensus-specs are released under CC0 1.0 |
| Average-linkage clustering | `cluster` | UPGMA (Sokal & Michener 1958), Lance–Williams update | textbook method | public domain knowledge |
| CBG geolocation | `geoloc` | B. Gueye, A. Ziviani, M. Crovella, S. Fdida, *Constraint-Based Geolocation of Internet Hosts*, IEEE/ACM ToN 2006 | idea from the paper; bestline fit via lower convex hull is our own | no known patent |
| Two-sided claim check | `geoloc.verify_claim` | the project's own, motivated by Gill et al., *Dude, where's my server?*, PAM 2010 | own method | n/a |
| Inverse normal CDF | `synth` | Python standard library `statistics.NormalDist` | library call | PSF license, MIT-compatible |
| ERC-20 behaviour | `evm.audit`, `Token.vy` | EIP-20 (F. Vogelsteller, V. Buterin) | checks are derived from the standard's text | EIPs are released under CC0 |
| City coordinates | `synth.CITIES` | public geographic facts | rounded coordinates | facts are not copyrightable |

## Runtime and development dependencies

| Package | License | Scope |
|---|---|---|
| numpy | BSD-3-Clause (+0BSD, MIT, Zlib, CC0 for bundled parts) | runtime |
| titanoboa | MIT | `evm` extra |
| vyper | Apache-2.0 | `evm` extra (compiler only; contracts are not derived from it) |
| pytest, pytest-cov, ruff, mypy | MIT | development |
| hypothesis | MPL-2.0 | development (not distributed with the package) |

All of these are compatible with releasing latencymesh under MIT.
