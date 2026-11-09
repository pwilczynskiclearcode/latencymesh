"""Reusable ERC-20 audit suite.

Each check runs inside ``boa.env.anchor()`` so it never changes the token's state, and
returns a :class:`Finding`. Severity ``error`` means the token breaks EIP-20 or a
property integrators rely on; ``warning`` marks behaviour that is allowed by the
standard but commonly dangerous (e.g. accepting transfers to the zero address).

Use it from tests::

    report = audit_erc20(token, holder)
    assert report.ok, report.summary()

or extend :data:`CHECKS` with project-specific properties.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import boa

ZERO = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    passed: bool
    severity: str  # "error" | "warning"
    detail: str = ""


@dataclass(slots=True)
class AuditReport:
    token: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.passed for f in self.findings if f.severity == "error")

    def failed(self, severity: str | None = None) -> list[Finding]:
        return [f for f in self.findings if not f.passed and severity in (None, f.severity)]

    def summary(self) -> str:
        lines = [f"ERC-20 audit of {self.token}:"]
        for f in self.findings:
            mark = "PASS" if f.passed else ("FAIL" if f.severity == "error" else "WARN")
            lines.append(f"  [{mark}] {f.check}" + (f" - {f.detail}" if f.detail else ""))
        return "\n".join(lines)


@dataclass(slots=True)
class Ctx:
    token: Any
    holder: str
    alice: str
    bob: str


Check = Callable[[Ctx], tuple[bool, str]]


def _events(token: Any, name: str) -> list[Any]:
    return [e for e in token.get_logs() if type(e).__name__ == name]


def _reverts(fn: Callable[[], Any]) -> bool:
    try:
        with boa.env.anchor():
            fn()
    except Exception:
        return True
    return False


def check_metadata(c: Ctx) -> tuple[bool, str]:
    t = c.token
    supply, dec = t.totalSupply(), t.decimals()
    ok = isinstance(t.name(), str) and isinstance(t.symbol(), str) and 0 <= dec <= 36
    return ok and supply > 0, f"supply={supply} decimals={dec}"


def check_transfer_moves_exact_amount(c: Ctx) -> tuple[bool, str]:
    t = c.token
    amount = max(1, t.balanceOf(c.holder) // 10)
    b0, a0, s0 = t.balanceOf(c.holder), t.balanceOf(c.alice), t.totalSupply()
    with boa.env.prank(c.holder):
        ret = t.transfer(c.alice, amount)
    ev = _events(t, "Transfer")
    ok = (
        t.balanceOf(c.holder) == b0 - amount
        and t.balanceOf(c.alice) == a0 + amount
        and t.totalSupply() == s0
        and ret in (True, None)
        and len(ev) == 1
        and ev[0].value == amount
    )
    return ok, f"returned={ret} events={len(ev)}"


def check_transfer_over_balance_reverts(c: Ctx) -> tuple[bool, str]:
    t = c.token

    def go() -> None:
        with boa.env.prank(c.alice):
            t.transfer(c.bob, t.balanceOf(c.alice) + 1)

    return _reverts(go), ""


def check_self_transfer_keeps_balance(c: Ctx) -> tuple[bool, str]:
    t = c.token
    b0 = t.balanceOf(c.holder)
    with boa.env.prank(c.holder):
        t.transfer(c.holder, max(1, b0 // 3))
    return t.balanceOf(c.holder) == b0, f"{b0} -> {t.balanceOf(c.holder)}"


def check_zero_transfer_allowed(c: Ctx) -> tuple[bool, str]:
    """EIP-20: transfers of 0 values MUST be treated as normal transfers."""
    t = c.token

    def go() -> None:
        with boa.env.prank(c.alice):
            t.transfer(c.bob, 0)

    return not _reverts(go), ""


def check_approve_sets_allowance(c: Ctx) -> tuple[bool, str]:
    t = c.token
    with boa.env.prank(c.holder):
        t.approve(c.alice, 123)
    ev = _events(t, "Approval")
    with boa.env.prank(c.holder):
        t.approve(c.alice, 7)  # overwrite, not add
    return t.allowance(c.holder, c.alice) == 7 and len(ev) == 1, f"events={len(ev)}"


def check_transfer_from_spends_allowance(c: Ctx) -> tuple[bool, str]:
    t = c.token
    with boa.env.prank(c.holder):
        t.approve(c.alice, 100)
    b0 = t.balanceOf(c.holder)
    with boa.env.prank(c.alice):
        t.transferFrom(c.holder, c.bob, 60)
    ok = (
        t.allowance(c.holder, c.alice) == 40
        and t.balanceOf(c.bob) == 60
        and t.balanceOf(c.holder) == b0 - 60
    )
    return ok, f"allowance left={t.allowance(c.holder, c.alice)}"


def check_transfer_from_over_allowance_reverts(c: Ctx) -> tuple[bool, str]:
    t = c.token
    with boa.env.prank(c.holder):
        t.approve(c.alice, 10)

    def go() -> None:
        with boa.env.prank(c.alice):
            t.transferFrom(c.holder, c.bob, 11)

    return _reverts(go), ""


def check_no_spend_without_approval(c: Ctx) -> tuple[bool, str]:
    t = c.token

    def go() -> None:
        with boa.env.prank(c.bob):
            t.transferFrom(c.holder, c.bob, 1)

    return _reverts(go), ""


def check_transfer_to_zero_rejected(c: Ctx) -> tuple[bool, str]:
    t = c.token

    def go() -> None:
        with boa.env.prank(c.holder):
            t.transfer(ZERO, 1)

    return _reverts(go), "tokens sent to 0x0 are burned silently" if not _reverts(go) else ""


#: (name, severity, check) - extend or filter for project-specific audits
CHECKS: list[tuple[str, str, Check]] = [
    ("metadata", "error", check_metadata),
    ("transfer moves exact amount", "error", check_transfer_moves_exact_amount),
    ("transfer over balance reverts", "error", check_transfer_over_balance_reverts),
    ("self transfer keeps balance", "error", check_self_transfer_keeps_balance),
    ("zero-value transfer allowed", "error", check_zero_transfer_allowed),
    ("approve sets allowance", "error", check_approve_sets_allowance),
    ("transferFrom spends allowance", "error", check_transfer_from_spends_allowance),
    ("transferFrom over allowance reverts", "error", check_transfer_from_over_allowance_reverts),
    ("no spending without approval", "error", check_no_spend_without_approval),
    ("transfer to zero address rejected", "warning", check_transfer_to_zero_rejected),
]


def audit_erc20(
    token: Any, holder: str, checks: list[tuple[str, str, Check]] | None = None
) -> AuditReport:
    """Run every check against ``token``; ``holder`` must own a positive balance."""
    if token.balanceOf(holder) == 0:
        raise ValueError("holder must have a positive balance")
    ctx = Ctx(token, holder, boa.env.generate_address("alice"), boa.env.generate_address("bob"))
    report = AuditReport(str(token.address))
    for name, severity, fn in checks or CHECKS:
        with boa.env.anchor():
            try:
                passed, detail = fn(ctx)
            except Exception as e:  # a check that crashes is a failed check
                passed, detail = False, f"{type(e).__name__}: {e}".splitlines()[0][:200]
        report.findings.append(Finding(name, passed, severity, detail))
    return report
