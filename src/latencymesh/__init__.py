"""latencymesh: simulate RTT-distance graphs and proximity-aware node selection.

The package is organised in layers, each usable on its own:

* ``model`` / ``graph`` / ``genesis`` – nodes, measurements and the sparse RTT graph
* ``synth``  – synthetic but physically plausible latency worlds
* ``sim``    – discrete-event engine that keeps re-measuring the graph
* ``coords`` – network coordinate systems (GNP, Vivaldi)
* ``select`` – (partly) random node selection strategies
* ``cluster``/``geoloc`` – datacenter/city/country/continent clustering, CBG
* ``evm``    – titanoboa harness, on-chain registry and contract audit kit
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
