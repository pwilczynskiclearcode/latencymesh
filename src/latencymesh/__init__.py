"""latencymesh: Vyper contracts for a witness network, and tools to simulate it.

The contracts in ``latencymesh/contracts`` are the project. This Python package only
loads a network description (YAML) into a contract running in titanoboa's in-process
EVM, so that scenarios and attacks can be written as plain pytest tests.
"""

from .network import Mesh, Network, Node, deploy, load_network

__version__ = "0.2.0.dev0"

__all__ = ["Mesh", "Network", "Node", "__version__", "deploy", "load_network"]
