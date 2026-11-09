"""EVM layer (requires the ``evm`` extra: ``pip install latencymesh[evm]``).

* :mod:`.chain`    – titanoboa harness: deploy contracts, map nodes to accounts, mine blocks
* :mod:`.protocol` – reference join protocol: witnesses + newcomer commit-reveal RTTs
* :mod:`.audit`    – reusable ERC-20 audit suite for tokens deployed next to the network
"""

from pathlib import Path

CONTRACTS_DIR = Path(__file__).parent / "contracts"

__all__ = ["CONTRACTS_DIR"]
