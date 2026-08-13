"""Embedding contracts.

Present in Phase 1 only so the backend interface is complete and adapters must
declare whether they support embeddings. Retrieval itself lands in Phase 6.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from janus_schemas.chat import JanusRequestOptions, Usage


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[str]
    dimensions: int | None = Field(default=None, gt=0)
    encoding_format: Literal["float", "base64"] = "float"
    user: str | None = None
    janus: JanusRequestOptions = Field(default_factory=JanusRequestOptions)

    @property
    def inputs(self) -> list[str]:
        return [self.input] if isinstance(self.input, str) else self.input


class EmbeddingItem(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingItem]
    model: str
    usage: Usage = Field(default_factory=Usage)
