"""Shared fixtures.

The milky-way genesis is deployed once per test session. Every test then runs inside
``boa.env.anchor()``, which rolls the chain back when the test ends - so each test
starts from exactly the same genesis state, whatever earlier tests did.
"""

from collections.abc import Iterator

import boa
import pytest

from latencymesh import Mesh, Network, deploy, load_network


@pytest.fixture(scope="session")
def milky_way() -> Network:
    return load_network("milky-way")


@pytest.fixture(scope="session")
def _deployed(milky_way: Network) -> Mesh:
    return deploy(milky_way)


@pytest.fixture(autouse=True)
def _rewind_chain() -> Iterator[None]:
    with boa.env.anchor():
        yield


@pytest.fixture
def mesh(_deployed: Mesh) -> Mesh:
    """WitnessMesh with the sealed milky-way genesis."""
    return _deployed
