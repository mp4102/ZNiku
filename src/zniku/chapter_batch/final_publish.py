"""直接封装到成片目录的独占候选区，检查后发布；不移动任何已登记的上游 Artifact。

新 exact 定义与旧 final 完全隔离。候选只在本 attempt 创建的目标同卷目录内生成，失败保留；
媒体发布和 SQLite 登记不是一个事务，发布后的错误必须保留正式文件并明确待核对，不能自动重试覆盖。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

from pydantic import Field, JsonValue, ValidationError, field_validator

from zniku.avenhance_v27 import adapters as av27
from zniku.avenhance_v27.probe import Av27MediaError, audio_signatures_from_metadata
from zniku.graph import NodeDefinition, PythonExecutorSpec, ValidatorSpec
from zniku.media.probe import MediaNodeError
from zniku.media.publication import checked_output_target, no_replace_publisher
from zniku.runtime import (
    NodeExecutionRequest,
    NodeValidatorContext,
    NodeValidatorResult,
    ProducedOutput,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.runtime.runner import RunnerProcessCleanupError, ValidatedOutput
from zniku.source_aligned import node_contracts as contracts
from zniku.source_aligned import validators as shared

from .contracts import NAMESPACE, VERSION, BatchMetadata

TYPE_ID = "zniku.source-admitted.chapter-batch.final-publish"
ADAPTER = "zniku.chapter_batch.final_publish:execute"
VALIDATOR = "zniku.chapter_batch.final_publish:validate"


class Parameters(contracts.FinalParameters):
    """保留原 Final 输入合同，增加显式发布权限；不把 UI 命名变成另一套媒体合同。"""

    target_path: Annotated[str, Field(min_length=1)]
    overwrite: bool = False
    output_root: Annotated[str, Field(min_length=1)]
    create_parent: bool = False
    protected_paths: tuple[Annotated[str, Field(min_length=1)], ...] = ()

    @field_validator("protected_paths", mode="before")
    @classmethod
    def path_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class Metadata(BatchMetadata):
    """只改变本新 Final 的真实 producer 身份，所有上游仍使用旧批量族身份。"""

    @classmethod
    def producer_type(
        cls, role: contracts.Role, split_count: int = 0, leaf: contracts.LeafBinding | None = None
    ) -> str:
        return TYPE_ID if role == "final" else super().producer_type(role, split_count, leaf)


@lru_cache(maxsize=1)
def definition() -> NodeDefinition:
    from .definitions import definition as batch_definition

    old = batch_definition("final")
    schema = Parameters.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return NodeDefinition(
        type_id=TYPE_ID,
        version=VERSION,
        input_ports=old.input_ports,
        output_ports=old.output_ports,
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=old.execution_mode,
        executor=PythonExecutorSpec(adapter=ADAPTER),
        validator=ValidatorSpec(adapter=VALIDATOR),
    )


def is_definition(value: NodeDefinition) -> bool:
    return value.type_id == TYPE_ID and value == definition()


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> contracts.NodeContract:
    if role != "final":
        raise Av27MediaError("E_FINAL_PUBLISH_ROLE", "只允许完整成片发布")
    params = Parameters.model_validate(contracts._plain(parameters))
    return contracts.preflight(
        role,
        inputs,
        {"source": params.source.model_dump(), "mr_mode": params.mr_mode},
        metadata_model=Metadata,
        namespace=NAMESPACE,
    )


def _target(params: Parameters, inputs: tuple[RunnerInput, ...], *, allow_create: bool) -> Path:
    program = next(item for item in inputs if item.port_id == "video")
    values = params.model_dump(mode="json", exclude={"source", "mr_mode"})
    # 不允许用户通过缩短 protected_paths 去掉直接输入的保护，包括原音轨与 gate。
    values["protected_paths"] = list(
        dict.fromkeys((*params.protected_paths, *(str(item.path) for item in inputs)))
    )
    return checked_output_target(values, program.path, allow_create=allow_create)


def _check_media(context: NodeValidatorContext, expected_name: str) -> NodeValidatorResult:
    # 私有候选检查和正式 validator 共用全部媒体规则；路径权限由各自调用点独立验证。
    return shared._validate(
        replace(context, request=replace(context.request, output_paths=())),
        "final",
        contract_reader=preflight,
        role_reader=lambda item: "final" if is_definition(item) else None,
        metadata_model=Metadata,
        namespace=NAMESPACE,
        verify_original_audio_origins=False,
        output_name=lambda _role, _port: expected_name,
    )


def validate(
    context: NodeValidatorContext,
    *,
    _contract_reader: Callable[
        [str, tuple[RunnerInput, ...], Mapping[str, object]], contracts.NodeContract
    ]
    | None = None,
    _media_checker: Callable[[NodeValidatorContext, str], NodeValidatorResult] | None = None,
) -> NodeValidatorResult:
    """正式产物必须等于显式发布目标；候选路径不能直接登记为成片 Artifact。"""

    def unpublished_message(error: object) -> str:
        target = context.request.node.parameters.get("target_path", "未知目标")
        return (
            "E_FINAL_PUBLISH_UNREGISTERED: 成片尚未通过登记前检查；如正式文件已存在请保留核对，"
            f"不要盲目重跑覆盖：{target}；{error}"
        )

    try:
        params = Parameters.model_validate(contracts._plain(context.request.node.parameters))
        (_contract_reader or preflight)(
            "final", context.request.inputs, context.request.node.parameters
        )
        target = _target(params, context.request.inputs, allow_create=False)
        if (
            len(context.outputs) != 1
            or context.outputs[0].port_id != "media"
            or context.outputs[0].path.resolve(strict=True) != target
        ):
            raise Av27MediaError("E_FINAL_PUBLISH_PATH", "正式输出没有绑定所选成片路径")
        result = (_media_checker or _check_media)(context, target.name)
        return (
            result
            if result.passed
            else replace(result, message=unpublished_message(result.message))
        )
    except (Av27MediaError, MediaNodeError, ValidationError, OSError, ValueError) as error:
        return NodeValidatorResult(passed=False, message=unpublished_message(error))


def execute(
    context: PythonAdapterContext,
    *,
    _definition_checker: Callable[[NodeDefinition], bool] | None = None,
    _contract_reader: Callable[
        [str, tuple[RunnerInput, ...], Mapping[str, object]], contracts.NodeContract
    ]
    | None = None,
    _media_checker: Callable[[NodeValidatorContext, str], NodeValidatorResult] | None = None,
) -> PythonAdapterResult:
    """先检查完整媒体再发布；失败不删除候选或现有成品，任何已有上游都不改名。"""
    if not (_definition_checker or is_definition)(context.definition):
        raise Av27MediaError("E_FINAL_PUBLISH_DEFINITION", "只允许完整新 exact 定义")
    params = Parameters.model_validate(contracts._plain(context.node.parameters))
    contract = (_contract_reader or preflight)("final", context.inputs, context.node.parameters)
    publish = os.replace if params.overwrite else no_replace_publisher()
    target = _target(params, context.inputs, allow_create=True)
    if target.exists() and not params.overwrite:
        raise Av27MediaError("E_MEDIA_OUTPUT_EXISTS", "目标已存在，未授权覆盖")
    # 独占小目录让 FFmpeg 继续使用 -n；不预建空媒体再启用 -y 绕开覆盖保护。
    candidate_dir = target.parent / f".zniku-publish-{uuid4().hex}.pending"
    candidate_dir.mkdir(exist_ok=False)
    candidate = candidate_dir / target.name
    published = False
    try:
        av27._append_log(
            context.stdout_log_path,
            f"Final publication candidate={candidate}; target={target}; not registered\n",
        )
        program = next(item for item in context.inputs if item.port_id == "video")
        sources = tuple(item for item in context.inputs if item.port_id == "sources")
        count = contract.outputs[0].metadata.frame_count
        actual = av27._execute_final_mux(
            context,
            program,
            sources,
            (),
            audio_signatures_from_metadata(sources[0].media_info),
            mode="program",
            target=candidate,
            expected_frames=count,
        )
        if actual is not None and actual != count:
            raise Av27MediaError("E_CHAPTER_BATCH_FINAL_COUNT", "Final 实际帧数与 Program 不一致")
        stat = candidate.stat()
        if not candidate.is_file() or stat.st_size <= 0:
            raise Av27MediaError("E_FINAL_PUBLISH_EMPTY", "候选不是非空媒体")
        request = NodeExecutionRequest(
            context.node_run_id,
            context.attempt,
            context.definition,
            context.node,
            context.inputs,
            work_dir=context.work_dir,
        )
        validation = (_media_checker or _check_media)(
            NodeValidatorContext(
                request,
                context.work_dir,
                (
                    ValidatedOutput(
                        "media",
                        "MediaFile",
                        candidate,
                        stat.st_size,
                        stat.st_mtime_ns,
                        {},
                        {"output_frames": count},
                    ),
                ),
            ),
            target.name,
        )
        if not validation.passed:
            raise Av27MediaError("E_FINAL_PUBLISH_VALIDATION", validation.message or "候选验收失败")
        # 检查后再核对目录和所有保护源；竞态同名目标仍由无覆盖原语拒绝。
        if _target(params, context.inputs, allow_create=False) != target:
            raise Av27MediaError("E_FINAL_PUBLISH_PATH", "发布期间目标目录发生改变")
        if (
            candidate_dir.resolve(strict=True) != candidate_dir
            or candidate.resolve(strict=True) != candidate
        ):
            raise Av27MediaError("E_FINAL_PUBLISH_PATH", "候选目录或媒体路径发生改变")
        after = candidate.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise Av27MediaError("E_FINAL_PUBLISH_CHANGED", "验收期间候选媒体发生变化")
        publish(candidate, target)
        published = True
        av27._append_log(
            context.stdout_log_path,
            f"Final publication committed={target}; Artifact registration pending\n",
        )
        return PythonAdapterResult(
            outputs=(ProducedOutput("media", target, allow_external=True),),
            producer_metadata={"media": {"output_frames": count}},
            media_summary={"published_path": str(target), "publication": "direct-mux"},
            validation_summary={"candidate_checked": True, "source_moved": False},
        )
    except RunnerProcessCleanupError:
        raise
    except Exception as error:
        if published:
            raise Av27MediaError(
                "E_FINAL_PUBLISH_UNREGISTERED",
                f"成片已发布但未确认登记；保留文件并核对工程状态，不要自动重试覆盖：{target}；{error}",
            ) from error
        raise Av27MediaError(
            "E_FINAL_PUBLISH_FAILED",
            f"本次发布未完成，候选保留：{candidate}；正式目标是否改变需核对：{target}；{error}",
        ) from error
