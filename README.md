# latencymesh

**Vyper contracts for a network of nodes that watch each other, where the watchers
("witnesses") of a node are chosen by network distance, plus a small Python harness
for simulating that network and attacking it in tests.**

> Status: **pre-alpha**, rebuilt from a minimal core. See [ROADMAP.md](ROADMAP.md).

The contracts are the project. Everything that happens off-chain between nodes
(pings, HTTP requests, timeouts) is *not* implemented here: tests fake it and check
what its outcomes do to the state on-chain.

## The idea

Every node knows its 3 nearest nodes by measured round-trip time (RTT). For any target
node this gives three *lines* around it:

```
line 1   the target's 3 nearest nodes
line 2   the nearest nodes of line 1   (without the target and line 1)
line 3   the nearest nodes of line 2   (without the target, line 1 and line 2)
```

`WitnessMesh.vy` picks 4 witnesses for a target from a random seed:

| Witness | Drawn from | Why |
|---|---|---|
| 1 | line 1 | sees the target from right next to it, even if the rest of the world is cut off |
| 2 | line 2 | the next ring out |
| 3 | line 3 | the ring after that |
| 4 | the whole network | a distant view, and work for nodes far from everyone |

If a line is empty, its witness is drawn from the whole network, so there are always
4 distinct witnesses and never the target itself.

## The milky-way genesis

[`networks/milky-way`](src/latencymesh/networks/milky-way) is the trusted starting network
every test begins from: 68 nodes, one per public cloud region of AWS, Google Cloud and Azure
that can be rented today.

* `nodes.yaml` – hand-written: provider, region, city, country, coordinates.
* `rtt.yaml` – each node's nearest nodes with RTT. **Currently estimated from city
  distances** (`tools/estimate_rtt.py`), to be replaced by real measurements.

## Writing a scenario

```python
from latencymesh.network import seed


def test_witnesses_of_frankfurt(mesh):  # `mesh` = milky-way, sealed, on a fresh chain
    line1, line2, line3 = mesh.lines_of("aws-eu-central-1")
    witnesses = mesh.witnesses_for("aws-eu-central-1", seed("cycle 1"))
    # [("azure-germanywestcentral", 1), ("aws-eu-south-1", 2),
    #  ("aws-ap-southeast-4", 0), ("aws-eu-west-1", 0)]      0 = drawn from the whole network
    # Frankfurt has no line 3 in this genesis, so its third witness is a global one.
```

The genesis is deployed once; every test runs inside `boa.env.anchor()` and the chain
is rewound to the genesis afterwards. `mesh.contract` is the titanoboa contract, so tests
can call it as any account (`boa.env.prank`) and expect reverts (`boa.reverts`).

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest          # verbose, with coverage
```

Built with Vyper 0.4, [titanoboa](https://github.com/vyperlang/titanoboa) (MIT) and
pytest. All code in this repository is original and MIT licensed, see [LICENSE](LICENSE).
