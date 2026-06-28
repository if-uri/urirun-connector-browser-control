# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.
"""Kontrakt connectora konformuje i pokrywa KAŻDĄ trasę handlera (anty-dryf)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_uc = pytest.importorskip("urirun_contract")
_scaffold = pytest.importorskip("urirun_contract.contract_scaffold")
conform, Contract = _uc.conform, _uc.Contract

PKG = Path(__file__).resolve().parents[1] / "urirun_connector_browser_control"
CONTRACTS = PKG / "contracts.json"


def _load() -> dict:
    doc = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    return {
        route: Contract(
            version=c["version"],
            effect=c["effect"],
            reversible=c["reversible"],
            inverse_route=c.get("inverseRoute", ""),
            inp=c["inp"],
            out=c["out"],
            errors=tuple(c["errors"]),
            examples=tuple(c["examples"]),
        )
        for route, c in doc["contracts"].items()
    }


def test_contract_conforms() -> None:
    conform(_load())


def test_every_handler_route_has_a_contract() -> None:
    core = (PKG / "core.py").read_text(encoding="utf-8")
    declared = set(json.loads(CONTRACTS.read_text(encoding="utf-8"))["contracts"])
    for route in _scaffold.discover_routes(core):
        assert _scaffold.route_key(route) in declared, \
            f"trasa {route!r} z core.py nie ma kontraktu"
