"""提供 Phase 6 的可安装合成 Engine 示例。

Decensoring 与 ``new01`` 只通过既有 Engine SDK、Manifest 和 Catalog 接入。adapter 仅转换合成
Artifact authority，不执行媒体 I/O；其存在目的是证明 Runtime 不需要识别私有 Engine ID。
"""

from __future__ import annotations

import hashlib
import inspect
from typing import cast

from zniku.contracts import (
    Artifact,
    EngineBinding,
    EngineManifest,
    JsonObject,
    Scope,
)
from zniku.contracts.schema import JSON_SCHEMA_DIALECT

from .catalog import EnginePackage
from .models import (
    ArtifactValue,
    EngineInvocationRequest,
    EngineInvocationResult,
    EnginePackageDescriptor,
)


def _media_schema() -> JsonObject:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {"duration_frames": {"type": "integer", "minimum": 1}},
        "required": ["duration_frames"],
        "additionalProperties": True,
    }


def _parameters(properties: JsonObject, required: tuple[str, ...]) -> JsonObject:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _video_manifest(
    *,
    engine_id: str,
    display_name: str,
    scope: Scope,
    parameter_schema: JsonObject,
) -> EngineManifest:
    return EngineManifest.from_data(
        cast(
            JsonObject,
            {
                "contract_version": "0.1.0",
                "engine_id": engine_id,
                "engine_version": "0.1.0",
                "display_name": display_name,
                "execution_mode": "automatic",
                "lifecycle": {
                    "supports_acceptance": True,
                    "supports_publication": True,
                    "supports_recovery": True,
                },
                "supported_scopes": [scope.value],
                "inputs": [
                    {
                        "port": {
                            "port_id": "video_in",
                            "artifact_type": "media",
                            "media_kind": "video",
                            "scope": scope.value,
                            "cardinality": "one",
                        },
                        "preconditions_schema": _media_schema(),
                    }
                ],
                "outputs": [
                    {
                        "port": {
                            "port_id": "video_out",
                            "artifact_type": "media",
                            "media_kind": "video",
                            "scope": scope.value,
                            "cardinality": "one",
                        },
                        "guarantees_schema": _media_schema(),
                        "attribute_rules": [
                            {
                                "path": "duration_frames",
                                "disposition": "preserved",
                                "source": {
                                    "port_id": "video_in",
                                    "path": "duration_frames",
                                },
                            }
                        ],
                    }
                ],
                "parameter_schema": dict(parameter_schema),
            },
        )
    )


def synthetic_decensoring_manifest() -> EngineManifest:
    """返回 chapter-scope Decensoring 示例合同。"""

    return _video_manifest(
        engine_id="zniku.extension.synthetic-decensoring",
        display_name="ZNIKU Synthetic Decensoring",
        scope=Scope.CHAPTER,
        parameter_schema=_parameters(
            {
                "model_name": {"type": "string", "enum": ["Jasna Synthetic"]},
                "model_version": {"type": "string", "enum": ["0.1.0"]},
            },
            ("model_name", "model_version"),
        ),
    )


def synthetic_new01_manifest() -> EngineManifest:
    """返回 program-scope ``new01`` 示例合同。"""

    return _video_manifest(
        engine_id="zniku.extension.synthetic-new01",
        display_name="ZNIKU Synthetic new01",
        scope=Scope.PROGRAM,
        parameter_schema=_parameters(
            {"profile": {"type": "string", "enum": ["phase6-demo"]}},
            ("profile",),
        ),
    )


class _SyntheticVideoTransformAdapter:
    """保持媒体形状并创建全新 Artifact identity 的确定性 adapter。"""

    effect_id: str

    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        value = request.input_value("video_in")
        if not isinstance(value, ArtifactValue):  # pragma: no cover - SDK preflight 已保证
            raise TypeError("video_in 必须绑定单一 Artifact")
        input_artifact = value.artifact
        seed = (
            f"{request.stage_run.stage_run_id}\0{input_artifact.artifact_id}\0{self.effect_id}"
        ).encode()
        suffix = hashlib.sha256(seed).hexdigest()[:24]
        attributes = cast(dict[str, object], input_artifact.to_data()["attributes"])
        attributes["phase6_effect"] = self.effect_id
        output = Artifact(
            artifact_id=f"artifact.phase6.{suffix}",
            artifact_type=input_artifact.artifact_type,
            media_kind=input_artifact.media_kind,
            scope=input_artifact.scope,
            scope_id=input_artifact.scope_id,
            producer_stage_run_id=request.stage_run.stage_run_id,
            attributes=cast(JsonObject, attributes),
        )
        return EngineInvocationResult(
            sdk_contract_version="0.1.0",
            invocation_id=request.invocation_id,
            engine=request.stage_run.engine,
            outputs=(ArtifactValue(port_id="video_out", artifact=output),),
        )


class SyntheticDecensoringAdapter(_SyntheticVideoTransformAdapter):
    """Decensoring 合成 adapter；不加载模型或读取文件。"""

    effect_id = "mosaic-removal"


class SyntheticNew01Adapter(_SyntheticVideoTransformAdapter):
    """``new01`` 合成 adapter；不预定义未来真实算法。"""

    effect_id = "zniku:new01"


def _package(
    package_id: str,
    manifest: EngineManifest,
    adapter: _SyntheticVideoTransformAdapter,
) -> EnginePackage:
    source = inspect.getsource(type(adapter)).encode("utf-8")
    digest = hashlib.sha256(manifest.to_canonical_bytes() + b"\0" + source).hexdigest()
    descriptor = EnginePackageDescriptor(
        sdk_contract_version="0.1.0",
        package_id=package_id,
        package_version="0.1.0",
        implementation_digest=f"sha256:{digest}",
        engine=EngineBinding.from_manifest(manifest),
    )
    return EnginePackage(descriptor=descriptor, manifest=manifest, adapter=adapter)


def phase6_extension_packages() -> tuple[EnginePackage, EnginePackage]:
    """返回由受信应用组装根显式安装的两个 Phase 6 示例 package。"""

    decensoring = synthetic_decensoring_manifest()
    new01 = synthetic_new01_manifest()
    return (
        _package(
            "zniku.extension.synthetic-decensoring",
            decensoring,
            SyntheticDecensoringAdapter(),
        ),
        _package("zniku.extension.synthetic-new01", new01, SyntheticNew01Adapter()),
    )
