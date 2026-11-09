# pragma version ~=0.4.3
"""
@title Reference ERC-20
@notice Minimal, standard-conforming ERC-20 used as the "known good" baseline for
        latencymesh.evm.audit. The full supply is minted to the deployer.
"""
from ethereum.ercs import IERC20

implements: IERC20

event Transfer:
    sender: indexed(address)
    receiver: indexed(address)
    value: uint256

event Approval:
    owner: indexed(address)
    spender: indexed(address)
    value: uint256

name: public(String[32])
symbol: public(String[8])
decimals: public(uint8)
totalSupply: public(uint256)
balanceOf: public(HashMap[address, uint256])
allowance: public(HashMap[address, HashMap[address, uint256]])


@deploy
def __init__(name: String[32], symbol: String[8], decimals: uint8, supply: uint256):
    self.name = name
    self.symbol = symbol
    self.decimals = decimals
    self.totalSupply = supply
    self.balanceOf[msg.sender] = supply
    log Transfer(sender=empty(address), receiver=msg.sender, value=supply)


@internal
def _transfer(sender: address, receiver: address, amount: uint256):
    assert receiver != empty(address), "transfer to zero address"
    self.balanceOf[sender] -= amount
    self.balanceOf[receiver] += amount
    log Transfer(sender=sender, receiver=receiver, value=amount)


@external
def transfer(receiver: address, amount: uint256) -> bool:
    self._transfer(msg.sender, receiver, amount)
    return True


@external
def transferFrom(sender: address, receiver: address, amount: uint256) -> bool:
    self.allowance[sender][msg.sender] -= amount
    self._transfer(sender, receiver, amount)
    return True


@external
def approve(spender: address, amount: uint256) -> bool:
    self.allowance[msg.sender][spender] = amount
    log Approval(owner=msg.sender, spender=spender, value=amount)
    return True
