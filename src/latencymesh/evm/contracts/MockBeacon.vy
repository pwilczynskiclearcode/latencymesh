# pragma version ~=0.4.3
"""
@title MockBeacon (testing only)
@notice Stand-in for a public randomness beacon (drand, a VRF oracle, ...).
        The owner publishes one value per round. A production deployment
        replaces it with an adapter that verifies the beacon's signatures or
        proofs on-chain; NodeRegistry only depends on the two view functions.
"""

owner: public(address)
latest_round: public(uint256)
values: HashMap[uint256, bytes32]


@deploy
def __init__():
    self.owner = msg.sender


@external
def publish(round: uint256, randomness_: bytes32):
    assert msg.sender == self.owner, "only owner"
    assert round == self.latest_round + 1, "rounds must be consecutive"
    assert randomness_ != empty(bytes32), "empty randomness"
    self.values[round] = randomness_
    self.latest_round = round


@external
@view
def randomness(round: uint256) -> bytes32:
    return self.values[round]
