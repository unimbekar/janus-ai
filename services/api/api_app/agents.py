"""Agents: versioned definitions and a governed run loop.

Graph nodes never name a provider. Every model call goes through the gateway.
Tool output is treated as untrusted data and is not stored as chain-of-thought.
"""

from __future__ import annotations

from datetime import UTC, datetime
from re import fullmatch

from janus_core.errors import NotFoundError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_app.gateway_client import GatewayClient
from api_app.knowledge import KnowledgeService, RetrievedChunk
from api_app.models import Agent, AgentRun, AgentStep, AgentVersion, Checkpoint, KnowledgeBase

SLUG_PATTERN = r"[a-z0-9][a-z0-9-]{1,62}"
NATIVE_TOOLS = ("clock", "knowledge_search")
DEFAULT_MAX_STEPS = 8
DEFAULT_INSTRUCTIONS = (
    "You are a Janus agent. Use retrieved context when it is provided. "
    "Never invent citations. Do not reveal hidden chain-of-thought."
)


class AgentService:
    def __init__(self, knowledge: KnowledgeService | None = None) -> None:
        self._knowledge = knowledge or KnowledgeService()

    async def create(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        user_id: str,
        name: str,
        slug: str,
        instructions: str | None = None,
        knowledge_base_ids: list[str] | None = None,
        tools: list[str] | None = None,
        model: str = "auto",
        mode: ExecutionMode | None = None,
    ) -> Agent:
        if not fullmatch(SLUG_PATTERN, slug):
            raise ValidationError("Slug must be lowercase letters, digits, and hyphens.")
        agent = Agent(
            id=new_id(IdPrefix.AGENT),
            organization_id=organization_id,
            slug=slug,
            name=name,
            status="draft",
            current_version=1,
            created_by=user_id,
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            id=new_id(IdPrefix.AGENT_VERSION),
            agent_id=agent.id,
            organization_id=organization_id,
            version=1,
            instructions=instructions or DEFAULT_INSTRUCTIONS,
            model_policy={"selection": model, "mode": (mode.value if mode else "auto")},
            knowledge_base_ids=list(knowledge_base_ids or []),
            tools=list(tools) if tools is not None else ["clock", "knowledge_search"],
        )
        session.add(version)
        await session.flush()
        return agent

    async def publish(self, session: AsyncSession, agent: Agent, user_id: str) -> Agent:
        version = await self.current_version(session, agent)
        version.published_at = datetime.now(UTC)
        version.published_by = user_id
        agent.status = "published"
        return agent

    async def list_agents(self, session: AsyncSession) -> list[Agent]:
        result = await session.scalars(
            select(Agent).where(Agent.deleted_at.is_(None)).order_by(Agent.updated_at.desc())
        )
        return list(result)

    async def get(self, session: AsyncSession, agent_id: str) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.deleted_at is not None:
            raise NotFoundError("Agent not found.", code="agent_not_found")
        return agent

    async def current_version(self, session: AsyncSession, agent: Agent) -> AgentVersion:
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == agent.current_version,
            )
        )
        if version is None:
            raise NotFoundError("Agent version not found.", code="agent_version_not_found")
        return version

    async def run(
        self,
        session: AsyncSession,
        *,
        agent: Agent,
        user_id: str | None,
        actor_id: str,
        prompt: str,
        gateway: GatewayClient,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification,
    ) -> AgentRun:
        version = await self.current_version(session, agent)
        policy = version.model_policy or {}
        max_steps = int(policy.get("max_steps") or DEFAULT_MAX_STEPS)
        selection = str(policy.get("selection") or "auto")
        started = datetime.now(UTC)
        run = AgentRun(
            id=new_id(IdPrefix.AGENT_RUN),
            organization_id=agent.organization_id,
            agent_id=agent.id,
            agent_version_id=version.id,
            triggered_by=user_id,
            status="running",
            mode=mode,
            input=prompt,
            started_at=started,
        )
        session.add(run)
        await session.flush()

        citations: list[dict] = []
        context_blocks: list[str] = []
        sequence = 0

        if "knowledge_search" in (version.tools or []) and version.knowledge_base_ids:
            sequence += 1
            retrieved = await self._retrieve_all(
                session,
                version.knowledge_base_ids,
                prompt,
                gateway=gateway,
                organization_id=agent.organization_id,
                request_id=request_id,
                mode=mode,
                classification=classification,
            )
            for item in retrieved:
                context_blocks.append(item.content)
                citations.append(
                    {
                        "chunk_id": item.chunk_id,
                        "document_id": item.document_id,
                        "quote": item.content[:240],
                        "score": round(item.score, 5),
                    }
                )
            session.add(
                AgentStep(
                    id=new_id(IdPrefix.AGENT_STEP),
                    run_id=run.id,
                    organization_id=agent.organization_id,
                    sequence=sequence,
                    node="retrieve",
                    tool_name="knowledge_search",
                    tool_input={"query": prompt},
                    tool_output={"hits": len(retrieved)},
                    status="complete",
                )
            )
            if sequence >= max_steps:
                return await self._halt(session, run, "max_steps_reached", citations)

        if "clock" in (version.tools or []) and "time" in prompt.lower():
            sequence += 1
            now = datetime.now(UTC).isoformat()
            context_blocks.append(f"Current UTC time: {now}")
            session.add(
                AgentStep(
                    id=new_id(IdPrefix.AGENT_STEP),
                    run_id=run.id,
                    organization_id=agent.organization_id,
                    sequence=sequence,
                    node="tool",
                    tool_name="clock",
                    tool_output={"utc": now},
                    status="complete",
                )
            )

        messages = [
            {"role": "system", "content": version.instructions},
        ]
        if context_blocks:
            grounded = "Retrieved context:\n" + "\n---\n".join(context_blocks)
            messages.append({"role": "system", "content": grounded})
        messages.append({"role": "user", "content": prompt})

        sequence += 1
        status_code, completion = await gateway.chat_completion(
            {"model": selection, "messages": messages},
            organization_id=agent.organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
            actor_id=actor_id,
        )
        if status_code >= 400:
            run.status = "failed"
            run.error = completion.get("error") or {"message": "gateway error"}
            run.finished_at = datetime.now(UTC)
            run.step_count = sequence
            run.citations = citations
            return run

        answer = (
            completion.get("choices", [{}])[0].get("message", {}).get("content")
            or completion.get("output")
            or ""
        )
        usage = completion.get("usage") or {}
        janus = completion.get("janus") or {}
        session.add(
            AgentStep(
                id=new_id(IdPrefix.AGENT_STEP),
                run_id=run.id,
                organization_id=agent.organization_id,
                sequence=sequence,
                node="compose",
                model_slug=janus.get("model"),
                request_id=janus.get("request_id") or request_id,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                status="complete",
            )
        )
        session.add(
            Checkpoint(
                run_id=run.id,
                organization_id=agent.organization_id,
                step=sequence,
                state={"messages": messages, "output": answer},
            )
        )
        run.status = "completed"
        run.output = answer
        run.step_count = sequence
        run.input_tokens = int(usage.get("prompt_tokens") or 0)
        run.output_tokens = int(usage.get("completion_tokens") or 0)
        run.citations = citations
        run.finished_at = datetime.now(UTC)
        return run

    async def get_run(self, session: AsyncSession, run_id: str) -> AgentRun:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise NotFoundError("Run not found.", code="agent_run_not_found")
        return run

    async def steps_for(self, session: AsyncSession, run_id: str) -> list[AgentStep]:
        result = await session.scalars(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)
        )
        return list(result)

    async def _retrieve_all(
        self,
        session: AsyncSession,
        knowledge_base_ids: list[str],
        query: str,
        **embed_kwargs: object,
    ) -> list[RetrievedChunk]:
        hits: list[RetrievedChunk] = []
        for kb_id in knowledge_base_ids:
            base = await session.get(KnowledgeBase, kb_id)
            if base is None:
                continue
            hits.extend(
                await self._knowledge.retrieve(
                    session,
                    knowledge_base_id=base.id,
                    query=query,
                    embedding_model=base.embedding_model,
                    dimensions=base.embedding_dimensions,
                    **embed_kwargs,  # type: ignore[arg-type]
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:6]

    async def _halt(
        self, session: AsyncSession, run: AgentRun, reason: str, citations: list[dict]
    ) -> AgentRun:
        run.status = "halted"
        run.halt_reason = reason
        run.citations = citations
        run.finished_at = datetime.now(UTC)
        return run
