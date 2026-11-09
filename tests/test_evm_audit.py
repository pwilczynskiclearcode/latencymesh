from pathlib import Path

import pytest

boa = pytest.importorskip("boa")

from latencymesh.evm import CONTRACTS_DIR  # noqa: E402
from latencymesh.evm.audit import audit_erc20  # noqa: E402

pytestmark = pytest.mark.evm

SOURCE = Path(CONTRACTS_DIR / "Token.vy").read_text()


def deploy(source: str = SOURCE):  # type: ignore[no-untyped-def]
    return boa.loads(source, "Mesh Token", "MESH", 18, 10**24)


def failed_checks(token) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.check for f in audit_erc20(token, boa.env.eoa).failed()}


def test_reference_token_passes_everything() -> None:
    report = audit_erc20(deploy(), boa.env.eoa)
    assert report.ok, report.summary()
    assert not report.failed()


def test_detects_allowance_not_spent() -> None:
    buggy = SOURCE.replace("    self.allowance[sender][msg.sender] -= amount\n", "")
    assert "transferFrom spends allowance" in failed_checks(deploy(buggy))
    assert "no spending without approval" in failed_checks(deploy(buggy))


def test_detects_self_transfer_minting() -> None:
    buggy = SOURCE.replace(
        """    self.balanceOf[sender] -= amount
    self.balanceOf[receiver] += amount""",
        """    from_balance: uint256 = self.balanceOf[sender]
    to_balance: uint256 = self.balanceOf[receiver]
    self.balanceOf[sender] = from_balance - amount
    self.balanceOf[receiver] = to_balance + amount""",
    )
    assert failed_checks(deploy(buggy)) == {"self transfer keeps balance"}


def test_detects_fee_on_transfer() -> None:
    buggy = SOURCE.replace(
        "    self.balanceOf[receiver] += amount\n",
        "    self.balanceOf[receiver] += amount * 99 // 100\n"
        "    self.totalSupply -= amount - amount * 99 // 100\n",
    )
    assert "transfer moves exact amount" in failed_checks(deploy(buggy))


def test_zero_address_transfer_is_only_a_warning() -> None:
    lax = SOURCE.replace('    assert receiver != empty(address), "transfer to zero address"\n', "")
    report = audit_erc20(deploy(lax), boa.env.eoa)
    assert report.ok
    assert [f.check for f in report.failed("warning")] == ["transfer to zero address rejected"]
    assert "WARN" in report.summary()
