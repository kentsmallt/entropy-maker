"""Check that the configured API wallet is registered and not about to expire.

``userDexAbstraction`` is recorded by the caller and is never treated as
permission to trade.
"""

from __future__ import annotations

from dataclasses import dataclass


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentRecord:
    name: str | None
    address: str
    valid_until_ms: int


def parse_agents(payload: object) -> tuple[AgentRecord, ...]:
    if not isinstance(payload, list):
        raise AgentError("extraAgents response is not a list")
    records: list[AgentRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            raise AgentError("extraAgents entry is not an object")
        address = item.get("address")
        if not isinstance(address, str):
            raise AgentError("extraAgents entry has no address")
        try:
            valid = int(item["validUntil"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentError("extraAgents entry has no validUntil") from exc
        name = item.get("name")
        if name is not None and not isinstance(name, str):
            raise AgentError("extraAgents name is not a string")
        records.append(AgentRecord(name=name, address=address, valid_until_ms=valid))
    return tuple(records)


def require_agent(
    agents: tuple[AgentRecord, ...],
    derived_address: str,
    *,
    now_ms: int,
    min_ttl_ms: int,
    expected_name: str | None,
    account_address: str,
) -> AgentRecord:
    if derived_address.lower() == account_address.lower():
        raise AgentError(
            "agent address matches the account address; refusing to sign with the master wallet"
        )
    matches = [item for item in agents if item.address.lower() == derived_address.lower()]
    if len(matches) != 1:
        raise AgentError(
            f"expected one registered agent for {derived_address}, found {len(matches)}"
        )
    agent = matches[0]
    if expected_name is not None and agent.name != expected_name:
        raise AgentError("registered agent name does not match ENTROPY_AGENT_NAME")
    if agent.valid_until_ms - now_ms < min_ttl_ms:
        raise AgentError("agent expires inside ENTROPY_AGENT_MIN_TTL_SECONDS; refusing to start")
    return agent
