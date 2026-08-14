"""Agents and the OpenAI-shaped responses surface."""

from __future__ import annotations

from fastapi import APIRouter, status
from janus_core.errors import AuthorizationError
from pydantic import BaseModel, ConfigDict, Field

from api_app.agents import AgentService
from api_app.deps import (
    ClassificationDep,
    GatewayDep,
    ModeDep,
    PrincipalDep,
    RequestIdDep,
    SessionDep,
)
from api_app.knowledge import KnowledgeService
from api_app.models import Agent, AgentRun

router = APIRouter(prefix="/v1", tags=["agents"])
agents = AgentService(KnowledgeService())


class CreateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=64)
    instructions: str | None = Field(default=None, max_length=20_000)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    model: str = "auto"


class RunAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=50_000)


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=50_000)
    model: str = "auto"
    agent_id: str | None = None


class AgentResponse(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    current_version: int


class RunResponse(BaseModel):
    id: str
    status: str
    output: str | None
    step_count: int
    halt_reason: str | None
    citations: list
    model: str | None = None


def _agent(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        status=agent.status,
        current_version=agent.current_version,
    )


def _run(run: AgentRun, model: str | None = None) -> RunResponse:
    return RunResponse(
        id=run.id,
        status=run.status,
        output=run.output,
        step_count=run.step_count,
        halt_reason=run.halt_reason,
        citations=run.citations or [],
        model=model,
    )


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: CreateAgentRequest, principal: PrincipalDep, session: SessionDep
) -> AgentResponse:
    principal.require_role("member")
    if principal.user_id is None:
        raise AuthorizationError("Creating an agent requires a signed-in user.")
    agent = await agents.create(
        session,
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        name=body.name,
        slug=body.slug,
        instructions=body.instructions,
        knowledge_base_ids=body.knowledge_base_ids,
        tools=body.tools,
        model=body.model,
    )
    return _agent(agent)


@router.get("/agents")
async def list_agents(principal: PrincipalDep, session: SessionDep) -> dict:
    return {"data": [_agent(item) for item in await agents.list_agents(session)]}


@router.get("/agents/runs/{run_id}")
async def get_run(run_id: str, principal: PrincipalDep, session: SessionDep) -> dict:
    run = await agents.get_run(session, run_id)
    steps = await agents.steps_for(session, run_id)
    return {
        **_run(run).model_dump(),
        "steps": [
            {
                "sequence": step.sequence,
                "node": step.node,
                "tool": step.tool_name,
                "status": step.status,
                "model": step.model_slug,
            }
            for step in steps
        ],
    }


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, principal: PrincipalDep, session: SessionDep) -> AgentResponse:
    return _agent(await agents.get(session, agent_id))


@router.post("/agents/{agent_id}/publish")
async def publish_agent(
    agent_id: str, principal: PrincipalDep, session: SessionDep
) -> AgentResponse:
    principal.require_role("member")
    if principal.user_id is None:
        raise AuthorizationError("Publishing an agent requires a signed-in user.")
    agent = await agents.get(session, agent_id)
    await agents.publish(session, agent, principal.user_id)
    return _agent(agent)


@router.post("/agents/{agent_id}/runs", status_code=status.HTTP_201_CREATED)
async def run_agent(
    agent_id: str,
    body: RunAgentRequest,
    principal: PrincipalDep,
    session: SessionDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> RunResponse:
    agent = await agents.get(session, agent_id)
    run = await agents.run(
        session,
        agent=agent,
        user_id=principal.user_id,
        actor_id=principal.actor_id,
        prompt=body.input,
        gateway=gateway,
        request_id=request_id,
        mode=mode,
        classification=classification,
    )
    return _run(run)


@router.post("/responses")
async def responses(
    body: ResponsesRequest,
    principal: PrincipalDep,
    session: SessionDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict:
    """OpenAI-shaped responses: an agent run, or a single grounded completion."""
    if body.agent_id:
        agent = await agents.get(session, body.agent_id)
        run = await agents.run(
            session,
            agent=agent,
            user_id=principal.user_id,
            actor_id=principal.actor_id,
            prompt=body.input,
            gateway=gateway,
            request_id=request_id,
            mode=mode,
            classification=classification,
        )
        return {
            "id": run.id,
            "object": "response",
            "status": run.status,
            "output": [{"type": "text", "text": run.output or ""}],
            "janus": {"citations": run.citations, "step_count": run.step_count},
        }

    status_code, completion = await gateway.chat_completion(
        {
            "model": body.model,
            "messages": [{"role": "user", "content": body.input}],
        },
        organization_id=principal.organization_id,
        request_id=request_id,
        mode=mode,
        classification=classification,
        actor_id=principal.actor_id,
    )
    text = completion.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {
        "id": request_id,
        "object": "response",
        "status": "completed" if status_code < 400 else "failed",
        "output": [{"type": "text", "text": text}],
        "janus": completion.get("janus") or {},
    }
