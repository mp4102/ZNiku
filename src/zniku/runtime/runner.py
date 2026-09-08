"""执行单个 ZNIKU 0.2.0 节点 attempt。

Runner 只负责在独立工作目录中调用可信的 Python adapter、直接命令或人工外部流程，并在全部声明
输出通过轻量校验后返回普通结果。它不持久化 Runtime 状态，不实现 resume/checkpoint，也不会清理
Project、上游 Artifact 或用户媒体。调用方必须在执行前后通过 Service 原子推进 NodeRun 状态。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from zniku.graph import (
    Cardinality,
    CommandExecutorSpec,
    ExecutionMode,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PythonExecutorSpec,
)
from zniku.runtime.models import FrameRange

from .process_window import background_creation_flags
from .progress import ProgressError, ProgressInfrastructureError, ProgressReporter

_MEDIA_TYPES = frozenset({"MediaFile", "VideoFile", "AudioFile"})
_DEFAULT_OUTPUT_SUFFIX = {
    "MediaFile": ".mkv",
    "VideoFile": ".mkv",
    "AudioFile": ".mka",
}
_PLACEHOLDER = re.compile(
    r"^\{(?P<kind>workdir|input|inputs|output|param)(?::(?P<name>[^{}:]+))?\}$"
)
_HANDOFF_SCHEMA_VERSION = 1
_MEDIA_INFO_NAMESPACE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+$")


def _readonly_json(value: object) -> object:
    """把已完成严格 JSON 校验的值递归转换为只读视图。"""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _readonly_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_readonly_json(item) for item in value)
    return value


def _plain_json(value: object) -> object:
    """把内部只读 JSON 视图还原为可被持久模型正常冻结的普通值。"""

    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value


class RunnerFailureReason(StrEnum):
    """为 Service 映射 ``failed.reason`` 提供稳定、非业务化分类。"""

    CONFIGURATION = "configuration"
    PATH_INVALID = "path_invalid"
    PROCESS_FAILED = "process_failed"
    ADAPTER_FAILED = "adapter_failed"
    OUTPUT_INVALID = "output_invalid"
    PROBE_FAILED = "probe_failed"
    VALIDATOR_FAILED = "validator_failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class RunnerError(RuntimeError):
    """表示一次 attempt 未产生可登记结果的结构化失败。"""

    def __init__(
        self,
        code: str,
        reason: RunnerFailureReason,
        message: str,
        *,
        exit_code: int | None = None,
        stdout_log_path: Path | None = None,
        stderr_log_path: Path | None = None,
    ) -> None:
        self.code = code
        self.reason = reason
        self.exit_code = exit_code
        self.stdout_log_path = stdout_log_path
        self.stderr_log_path = stderr_log_path
        super().__init__(f"{code}: {message}")

    def to_data(self) -> dict[str, object]:
        """返回可直接写入 NodeRun error 字段的普通 JSON 数据。"""

        return {
            "code": self.code,
            "reason": self.reason.value,
            "message": str(self),
            "exit_code": self.exit_code,
            "stdout_log_path": (
                None if self.stdout_log_path is None else str(self.stdout_log_path)
            ),
            "stderr_log_path": (
                None if self.stderr_log_path is None else str(self.stderr_log_path)
            ),
        }


class RunnerCancelled(Exception):
    """供 adapter 或上层取消控制显式终止当前 attempt。"""


class RunnerInterrupted(Exception):
    """供受控进程包装器显式报告非正常中断。"""


@dataclass(frozen=True, slots=True)
class RunnerInput:
    """把 Repository 已登记 Artifact 的完整只读事实绑定到 input port。

    ``ordinal`` 是 edge 的 input ordinal；``artifact_ordinal`` 则来自 Artifact 自身，两者不能
    混用。adapter/validator 只能读取这里的 ``media_info``，不得从 NodeResult 摘要或客户端
    补充另一份媒体 authority。
    """

    port_id: str
    artifact_id: str
    kind: str
    path: Path
    ordinal: int | None = None
    producer_node_run_id: str | None = None
    producer_port_id: str | None = None
    artifact_ordinal: int | None = None
    frame_range: FrameRange | None = None
    media_info: Mapping[str, object] = field(default_factory=dict)
    size: int | None = None
    mtime_ns: int | None = None

    @property
    def input_ordinal(self) -> int | None:
        """以合同名称公开 edge input ordinal，同时兼容既有 ``ordinal`` API。"""

        return self.ordinal


@dataclass(frozen=True, slots=True)
class OutputPathSpec:
    """为声明 output port 覆盖默认的 attempt 内相对路径。"""

    port_id: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class NodeExecutionRequest:
    """描述 Service 已创建、但尚未持久推进结果的一次 attempt。"""

    node_run_id: str
    attempt: int
    definition: NodeDefinition
    node: NodeInstance
    inputs: tuple[RunnerInput, ...] = ()
    output_paths: tuple[OutputPathSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """Runner 分配给 adapter、命令或人工流程的声明输出。

    外部路径权限只能由受信任 Python adapter 通过 ``ProducedOutput`` 显式申请；
    command 与人工流程仍只能在 attempt 工作目录中产生输出。
    """

    port_id: str
    kind: str
    path: Path
    frame_range: FrameRange | None = None
    allow_external: bool = False


@dataclass(frozen=True, slots=True)
class ProducedOutput:
    """允许受信任 adapter 返回实际输出路径及可选帧区间。

    ``allow_external`` 是 Source/Output 类节点使用的最小权限开关，不会通过
    NodeDefinition 或用户 JSON 传入。默认严格限制在 attempt 内。
    """

    port_id: str
    path: Path
    frame_range: FrameRange | None = None
    allow_external: bool = False


@dataclass(frozen=True, slots=True)
class PythonAdapterContext:
    """Python adapter 的最小稳定调用上下文。

    ``progress`` 只在 Runtime 为 automatic Python attempt 注入可信 reporter 时存在；command
    executor 不会取得该对象，manual external 也不构造本上下文。
    """

    node_run_id: str
    attempt: int
    definition: NodeDefinition
    node: NodeInstance
    work_dir: Path
    inputs: tuple[RunnerInput, ...]
    outputs: tuple[OutputTarget, ...]
    stdout_log_path: Path
    stderr_log_path: Path
    progress: ProgressReporter | None = None


@dataclass(frozen=True, slots=True)
class PythonAdapterResult:
    """Python adapter 返回声明输出、诊断摘要与 per-port 原始 producer facts。"""

    outputs: tuple[ProducedOutput, ...] = ()
    media_summary: Mapping[str, object] = field(default_factory=dict)
    validation_summary: Mapping[str, object] = field(default_factory=dict)
    producer_metadata: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidatedOutput:
    """传给节点 validator 的默认 probe 与独立原始 producer metadata。"""

    port_id: str
    kind: str
    path: Path
    size: int
    mtime_ns: int
    media_info: Mapping[str, object]
    producer_metadata: Mapping[str, object] = field(default_factory=dict)
    frame_range: FrameRange | None = None


@dataclass(frozen=True, slots=True)
class NodeValidatorContext:
    """节点级 validator 只读取本 attempt 的输入、输出与配置。"""

    request: NodeExecutionRequest
    work_dir: Path
    outputs: tuple[ValidatedOutput, ...]


@dataclass(frozen=True, slots=True)
class NodeValidatorResult:
    """节点 validator 的 pass/fail 结论与通过后才可合并的媒体扩展。"""

    passed: bool
    summary: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    message: str | None = None
    media_info_extensions: Mapping[str, Mapping[str, Mapping[str, object]]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class RunnerArtifact:
    """全部校验完成后才创建的普通 Artifact 候选。"""

    artifact_id: str
    kind: str
    path: Path
    producer_node_run_id: str
    producer_port_id: str
    ordinal: int | None
    frame_range: FrameRange | None
    media_info: Mapping[str, object]
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """一次成功 attempt 的完整、尚未持久化结果。"""

    result_id: str
    node_run_id: str
    attempt: int
    artifacts: tuple[RunnerArtifact, ...]
    work_dir: Path
    stdout_log_path: Path
    stderr_log_path: Path
    exit_code: int | None
    media_summary: Mapping[str, object]
    validation_summary: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class HandoffInput:
    """人工 handoff 中可持久化的输入路径绑定。"""

    port_id: str
    artifact_id: str
    kind: str
    path: str
    ordinal: int | None


@dataclass(frozen=True, slots=True)
class HandoffOutput:
    """人工 handoff 中必须由操作者完整提交的目标路径。"""

    port_id: str
    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class ManualHandoff:
    """不依赖进程内状态、可跨应用重启保存的人工交接。"""

    schema_version: int
    node_run_id: str
    attempt: int
    type_id: str
    definition_version: str
    work_dir: str
    inputs: tuple[HandoffInput, ...]
    outputs: tuple[HandoffOutput, ...]
    instructions: str | None

    def to_json(self) -> str:
        """序列化为普通 JSON；该记录只是 waiting_external 的业务数据。"""

        return json.dumps(asdict(self), ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> ManualHandoff:
        """严格读取持久化 handoff，拒绝未知或缺失字段。"""

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"handoff object 键 {key!r} 重复")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"handoff 不允许非标准 JSON 常量 {value}")

        try:
            data = json.loads(
                payload,
                object_pairs_hook=reject_duplicates,
                parse_constant=reject_constant,
            )
            if not isinstance(data, dict):
                raise TypeError("handoff 根值必须为 object")
            expected = {
                "schema_version",
                "node_run_id",
                "attempt",
                "type_id",
                "definition_version",
                "work_dir",
                "inputs",
                "outputs",
                "instructions",
            }
            if set(data) != expected:
                raise ValueError("handoff 字段集合无效")
            raw_inputs = data["inputs"]
            raw_outputs = data["outputs"]
            if not isinstance(raw_inputs, list) or not isinstance(raw_outputs, list):
                raise TypeError("handoff inputs/outputs 必须为 array")
            inputs = tuple(cls._input_from_data(item) for item in raw_inputs)
            outputs = tuple(cls._output_from_data(item) for item in raw_outputs)
            instructions = data["instructions"]
            if instructions is not None and not isinstance(instructions, str):
                raise TypeError("instructions 必须为 string 或 null")
            schema_version = _strict_int(data["schema_version"], "schema_version")
            attempt = _strict_int(data["attempt"], "attempt")
            if schema_version != _HANDOFF_SCHEMA_VERSION:
                raise ValueError(f"未知 handoff schema version：{schema_version}")
            if attempt < 1:
                raise ValueError("handoff attempt 必须从 1 开始")
            return cls(
                schema_version=schema_version,
                node_run_id=_strict_str(data["node_run_id"], "node_run_id"),
                attempt=attempt,
                type_id=_strict_str(data["type_id"], "type_id"),
                definition_version=_strict_str(data["definition_version"], "definition_version"),
                work_dir=_strict_str(data["work_dir"], "work_dir"),
                inputs=inputs,
                outputs=outputs,
                instructions=instructions,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RunnerError(
                "E_RUNNER_HANDOFF_INVALID",
                RunnerFailureReason.CONFIGURATION,
                str(error),
            ) from error

    @staticmethod
    def _input_from_data(value: object) -> HandoffInput:
        if not isinstance(value, dict) or set(value) != {
            "port_id",
            "artifact_id",
            "kind",
            "path",
            "ordinal",
        }:
            raise ValueError("handoff input 字段集合无效")
        ordinal = value["ordinal"]
        if ordinal is not None:
            ordinal = _strict_int(ordinal, "ordinal")
            if ordinal < 0:
                raise ValueError("handoff input ordinal 不得为负数")
        return HandoffInput(
            port_id=_strict_str(value["port_id"], "port_id"),
            artifact_id=_strict_str(value["artifact_id"], "artifact_id"),
            kind=_strict_str(value["kind"], "kind"),
            path=_strict_str(value["path"], "path"),
            ordinal=ordinal,
        )

    @staticmethod
    def _output_from_data(value: object) -> HandoffOutput:
        if not isinstance(value, dict) or set(value) != {"port_id", "kind", "path"}:
            raise ValueError("handoff output 字段集合无效")
        return HandoffOutput(
            port_id=_strict_str(value["port_id"], "port_id"),
            kind=_strict_str(value["kind"], "kind"),
            path=_strict_str(value["path"], "path"),
        )


@dataclass(frozen=True, slots=True)
class ManualSubmission:
    """人工 Submit 可覆盖目标，但覆盖路径仍必须位于本 attempt 内。"""

    outputs: tuple[ProducedOutput, ...] = ()
    media_summary: Mapping[str, object] = field(default_factory=dict)
    validation_summary: Mapping[str, object] = field(default_factory=dict)


type PythonAdapter = Callable[[PythonAdapterContext], PythonAdapterResult]
type NodeValidator = Callable[[NodeValidatorContext], NodeValidatorResult]
type MediaProbe = Callable[[Path, str], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class _AttemptLayout:
    work_dir: Path
    output_dir: Path
    stdout_log_path: Path
    stderr_log_path: Path


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field_name} 必须为非空 string")
    return value


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} 必须为 integer")
    return value


class NodeRunner:
    """在受控 attempt 目录中执行一个普通 NodeDefinition。"""

    def __init__(
        self,
        work_root: str | Path,
        *,
        python_adapters: Mapping[str, PythonAdapter] | None = None,
        validators: Mapping[str, NodeValidator] | None = None,
        media_probe: MediaProbe | None = None,
        ffprobe_executable: str = "ffprobe",
    ) -> None:
        root = Path(work_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._work_root = root.resolve(strict=True)
        except OSError as error:
            raise RunnerError(
                "E_RUNNER_WORK_ROOT_INVALID",
                RunnerFailureReason.PATH_INVALID,
                str(error),
            ) from error
        if not self._work_root.is_dir():
            raise RunnerError(
                "E_RUNNER_WORK_ROOT_INVALID",
                RunnerFailureReason.PATH_INVALID,
                "work_root 必须是目录",
            )
        self._python_adapters = dict(python_adapters or {})
        self._validators = dict(validators or {})
        self._media_probe = media_probe or self._build_ffprobe(ffprobe_executable)

    def run_automatic(
        self,
        request: NodeExecutionRequest,
        *,
        progress: ProgressReporter | None = None,
    ) -> RunnerResult:
        """执行 automatic attempt；失败时只抛错，不返回部分 Artifact/Result。

        Reporter 只传给受信任 Python adapter。command executor 第一版没有冻结机器可读进度协议，
        即使调用方误传 reporter 也保持 indeterminate。
        """

        ordered_inputs = self._validate_request(request)
        if request.definition.execution_mode is not ExecutionMode.AUTOMATIC:
            raise self._configuration_error("E_RUNNER_MODE_INVALID", "节点不是 automatic")
        layout = self._create_layout(request.node_run_id)
        targets = self._output_targets(request, layout)
        self._create_output_parents(targets, layout)
        executor = request.definition.executor
        context = PythonAdapterContext(
            node_run_id=request.node_run_id,
            attempt=request.attempt,
            definition=request.definition,
            node=request.node,
            work_dir=layout.work_dir,
            inputs=ordered_inputs,
            outputs=targets,
            stdout_log_path=layout.stdout_log_path,
            stderr_log_path=layout.stderr_log_path,
            progress=progress if isinstance(executor, PythonExecutorSpec) else None,
        )

        try:
            if isinstance(executor, PythonExecutorSpec):
                adapter_result, exit_code = self._run_python(executor, context), None
                produced = self._resolve_produced_outputs(
                    adapter_result.outputs, targets, layout.work_dir
                )
                media_summary = adapter_result.media_summary
                validation_summary = adapter_result.validation_summary
                producer_metadata = adapter_result.producer_metadata
            elif isinstance(executor, CommandExecutorSpec):
                exit_code = self._run_command(executor, context)
                produced = targets
                media_summary = {}
                validation_summary = {}
                producer_metadata = {}
            else:
                raise self._configuration_error(
                    "E_RUNNER_EXECUTOR_INVALID",
                    "automatic 节点必须使用 python 或 command executor",
                )
            return self._validate_and_build_result(
                request,
                layout,
                produced,
                exit_code=exit_code,
                media_summary=media_summary,
                adapter_validation_summary=validation_summary,
                producer_metadata=producer_metadata,
            )
        except RunnerError:
            raise
        except (RunnerCancelled, asyncio.CancelledError) as error:
            raise self._control_error(
                "E_RUNNER_CANCELLED",
                RunnerFailureReason.CANCELLED,
                str(error) or "attempt 已取消",
                layout,
            ) from error
        except (RunnerInterrupted, KeyboardInterrupt) as error:
            raise self._control_error(
                "E_RUNNER_INTERRUPTED",
                RunnerFailureReason.INTERRUPTED,
                str(error) or "attempt 已中断",
                layout,
            ) from error

    def prepare_manual(self, request: NodeExecutionRequest) -> ManualHandoff:
        """创建 manual_external attempt 目录并返回可跨重启持久化的 handoff。"""

        ordered_inputs = self._validate_request(request)
        executor = request.definition.executor
        if request.definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or not isinstance(
            executor, ManualExternalExecutorSpec
        ):
            raise self._configuration_error("E_RUNNER_MODE_INVALID", "节点不是 manual_external")
        layout = self._create_layout(request.node_run_id)
        targets = self._output_targets(request, layout)
        self._create_output_parents(targets, layout)
        return ManualHandoff(
            schema_version=_HANDOFF_SCHEMA_VERSION,
            node_run_id=request.node_run_id,
            attempt=request.attempt,
            type_id=request.definition.type_id,
            definition_version=request.definition.version,
            work_dir=str(layout.work_dir),
            inputs=tuple(
                HandoffInput(
                    port_id=item.port_id,
                    artifact_id=item.artifact_id,
                    kind=item.kind,
                    path=str(item.path),
                    ordinal=item.ordinal,
                )
                for item in ordered_inputs
            ),
            outputs=tuple(
                HandoffOutput(port_id=item.port_id, kind=item.kind, path=str(item.path))
                for item in targets
            ),
            instructions=executor.instructions,
        )

    def submit_manual(
        self,
        request: NodeExecutionRequest,
        handoff: ManualHandoff,
        submission: ManualSubmission | None = None,
    ) -> RunnerResult:
        """验收人工输出；所有声明输出都通过后才构造完整结果。"""

        ordered_inputs = self._validate_request(request)
        executor = request.definition.executor
        if request.definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or not isinstance(
            executor, ManualExternalExecutorSpec
        ):
            raise self._configuration_error("E_RUNNER_MODE_INVALID", "节点不是 manual_external")
        layout = self._existing_layout(request.node_run_id)
        try:
            targets = self._output_targets(request, layout)
            self._assert_handoff_matches(
                request,
                handoff,
                layout,
                targets,
                ordered_inputs,
                executor.instructions,
            )
            submitted = self._normalize_manual_submission(
                ManualSubmission() if submission is None else submission,
                layout,
            )
            produced = self._resolve_produced_outputs(
                submitted.outputs,
                targets,
                layout.work_dir,
            )
            return self._validate_and_build_result(
                request,
                layout,
                produced,
                exit_code=None,
                media_summary=submitted.media_summary,
                adapter_validation_summary=submitted.validation_summary,
                producer_metadata={},
            )
        except RunnerError:
            raise
        except (RunnerCancelled, asyncio.CancelledError) as error:
            raise self._control_error(
                "E_RUNNER_CANCELLED",
                RunnerFailureReason.CANCELLED,
                str(error) or "attempt 已取消",
                layout,
            ) from error
        except (RunnerInterrupted, KeyboardInterrupt) as error:
            raise self._control_error(
                "E_RUNNER_INTERRUPTED",
                RunnerFailureReason.INTERRUPTED,
                str(error) or "attempt 已中断",
                layout,
            ) from error

    def inspect_manual_outputs(
        self,
        request: NodeExecutionRequest,
        handoff: ManualHandoff,
    ) -> tuple[ValidatedOutput, ...]:
        """只读验证一个既有 handoff 的全部声明输出。

        该入口与正式 Submit 复用 request、handoff、媒体 probe 和节点 validator 合同，但不会创建
        attempt 目录、写日志、构造 Artifact/NodeResult identity 或持久化任何状态。调用方仍必须在
        正式 Submit 时重新验证，以关闭 readiness 与提交之间的文件变化窗口。
        """

        ordered_inputs = self._validate_request(request)
        executor = request.definition.executor
        if request.definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or not isinstance(
            executor, ManualExternalExecutorSpec
        ):
            raise self._configuration_error("E_RUNNER_MODE_INVALID", "节点不是 manual_external")
        layout = self._existing_layout(request.node_run_id)
        targets = self._output_targets(request, layout)
        self._assert_handoff_matches(
            request,
            handoff,
            layout,
            targets,
            ordered_inputs,
            executor.instructions,
        )
        produced = self._resolve_produced_outputs((), targets, layout.work_dir)
        validated, _ = self._validate_outputs(
            request,
            layout,
            produced,
            exit_code=None,
            producer_metadata={},
        )
        return validated

    def inspect_manual_candidate(
        self,
        request: NodeExecutionRequest,
        handoff: ManualHandoff,
        *,
        port_id: str,
        candidate: Path,
    ) -> tuple[ValidatedOutput, ...]:
        """验证已绑定人工交接的 attempt 内候选文件，不覆盖目标、不构造任何结果。

        候选保留声明的文件名，但位于宿主创建的独立 staging 子目录。只替换一个声明输出的
        本次只读验证路径，其余输出仍使用原目标；全部输出必须通过同一个媒体和节点 validator。
        该入口不接收前端任意路径，Project Service 必须先完成选择句柄、目标及文件变化检查。
        """

        ordered_inputs = self._validate_request(request)
        executor = request.definition.executor
        if request.definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or not isinstance(
            executor, ManualExternalExecutorSpec
        ):
            raise self._configuration_error("E_RUNNER_MODE_INVALID", "节点不是 manual_external")
        layout = self._existing_layout(request.node_run_id)
        targets = self._output_targets(request, layout)
        self._assert_handoff_matches(
            request, handoff, layout, targets, ordered_inputs, executor.instructions
        )
        matched = [target for target in targets if target.port_id == port_id]
        if len(matched) != 1:
            raise self._configuration_error("E_RUNNER_OUTPUT_PORT_UNKNOWN", "候选输出端口不存在")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(layout.work_dir.resolve(strict=True))
        except ValueError as error:
            raise self._configuration_error(
                "E_RUNNER_PATH_ESCAPE", "候选输出必须位于已绑定 attempt 内"
            ) from error
        if resolved.name != matched[0].path.name or resolved == matched[0].path:
            raise self._configuration_error(
                "E_RUNNER_CANDIDATE_PATH", "候选必须保留声明文件名且不得替代正式目标路径"
            )
        produced = tuple(
            replace(target, path=resolved) if target.port_id == port_id else target
            for target in targets
        )
        validated, _ = self._validate_outputs(
            request, layout, produced, exit_code=None, producer_metadata={}
        )
        return validated

    def _validate_request(self, request: NodeExecutionRequest) -> tuple[RunnerInput, ...]:
        if request.attempt < 1:
            raise self._configuration_error("E_RUNNER_ATTEMPT_INVALID", "attempt 必须从 1 开始")
        self._node_run_uuid(request.node_run_id)
        if (
            request.node.type_id != request.definition.type_id
            or request.node.definition_version != request.definition.version
        ):
            raise self._configuration_error(
                "E_RUNNER_DEFINITION_BINDING_INVALID",
                "NodeInstance 与 NodeDefinition identity 不一致",
            )

        ports = {port.port_id: port for port in request.definition.input_ports}
        grouped: dict[str, list[RunnerInput]] = {port_id: [] for port_id in ports}
        for item in request.inputs:
            port = ports.get(item.port_id)
            if port is None:
                raise self._configuration_error(
                    "E_RUNNER_INPUT_PORT_UNKNOWN", f"未知 input port {item.port_id!r}"
                )
            if not item.artifact_id:
                raise self._configuration_error(
                    "E_RUNNER_INPUT_ARTIFACT_INVALID", "input artifact_id 不得为空"
                )
            if item.kind != port.data_type:
                raise self._configuration_error(
                    "E_RUNNER_INPUT_KIND_INVALID",
                    f"{item.port_id!r} 需要 {port.data_type}，实际为 {item.kind}",
                )
            try:
                if item.ordinal is not None and (
                    isinstance(item.ordinal, bool)
                    or not isinstance(item.ordinal, int)
                    or item.ordinal < 0
                ):
                    raise TypeError("input ordinal 必须是非负 integer 或 None")
                if item.producer_node_run_id is not None and (
                    not isinstance(item.producer_node_run_id, str) or not item.producer_node_run_id
                ):
                    raise TypeError("producer_node_run_id 必须是非空 string 或 None")
                if item.producer_port_id is not None and (
                    not isinstance(item.producer_port_id, str) or not item.producer_port_id
                ):
                    raise TypeError("producer_port_id 必须是非空 string 或 None")
                if item.artifact_ordinal is not None and (
                    isinstance(item.artifact_ordinal, bool)
                    or not isinstance(item.artifact_ordinal, int)
                    or item.artifact_ordinal < 0
                ):
                    raise TypeError("artifact_ordinal 必须是非负 integer 或 None")
                if item.frame_range is not None and not isinstance(item.frame_range, FrameRange):
                    raise TypeError("frame_range 必须是 FrameRange 或 None")
                if item.size is not None and (
                    isinstance(item.size, bool) or not isinstance(item.size, int) or item.size < 0
                ):
                    raise TypeError("size 必须是非负 integer 或 None")
                if item.mtime_ns is not None and (
                    isinstance(item.mtime_ns, bool)
                    or not isinstance(item.mtime_ns, int)
                    or item.mtime_ns < 0
                ):
                    raise TypeError("mtime_ns 必须是非负 integer 或 None")
                media_info = self._strict_readonly_mapping(
                    item.media_info,
                    field_name="input.media_info",
                )
            except (SystemExit, GeneratorExit, Exception) as error:
                raise self._configuration_error(
                    "E_RUNNER_INPUT_METADATA_INVALID",
                    str(error) or "input Artifact metadata 无效",
                ) from error
            resolved_input = self._assert_readable_input(item.path)
            grouped[item.port_id].append(
                RunnerInput(
                    port_id=item.port_id,
                    artifact_id=item.artifact_id,
                    kind=item.kind,
                    path=resolved_input,
                    ordinal=item.ordinal,
                    producer_node_run_id=item.producer_node_run_id,
                    producer_port_id=item.producer_port_id,
                    artifact_ordinal=item.artifact_ordinal,
                    frame_range=item.frame_range,
                    media_info=media_info,
                    size=item.size,
                    mtime_ns=item.mtime_ns,
                )
            )

        ordered: list[RunnerInput] = []
        for port in request.definition.input_ports:
            items = grouped[port.port_id]
            if port.cardinality is Cardinality.ONE:
                if len(items) > 1 or any(item.ordinal is not None for item in items):
                    raise self._configuration_error(
                        "E_RUNNER_INPUT_CARDINALITY_INVALID",
                        f"one input {port.port_id!r} 只接受一个无 ordinal 绑定",
                    )
                if port.required and not items:
                    raise self._configuration_error(
                        "E_RUNNER_REQUIRED_INPUT_MISSING",
                        f"required input {port.port_id!r} 缺失",
                    )
                ordered.extend(items)
                continue
            if port.required and not items:
                raise self._configuration_error(
                    "E_RUNNER_REQUIRED_INPUT_MISSING",
                    f"required input {port.port_id!r} 缺失",
                )
            ordinals = [item.ordinal for item in items]
            if any(value is None for value in ordinals) or sorted(
                cast(list[int], ordinals)
            ) != list(range(len(items))):
                raise self._configuration_error(
                    "E_RUNNER_INPUT_ORDINAL_INVALID",
                    f"ordered_many input {port.port_id!r} ordinal 必须从 0 连续唯一",
                )
            ordered.extend(sorted(items, key=lambda item: cast(int, item.ordinal)))
        return tuple(ordered)

    @staticmethod
    def _assert_readable_input(path: Path) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
            stat = resolved.stat()
        except OSError as error:
            raise RunnerError(
                "E_RUNNER_INPUT_UNREADABLE",
                RunnerFailureReason.OUTPUT_INVALID,
                str(error),
            ) from error
        if not resolved.is_file() or stat.st_size <= 0:
            raise RunnerError(
                "E_RUNNER_INPUT_UNREADABLE",
                RunnerFailureReason.OUTPUT_INVALID,
                f"input 不存在、不是文件或为空：{path}",
            )
        return resolved

    def _create_layout(self, node_run_id: str) -> _AttemptLayout:
        run_uuid = self._node_run_uuid(node_run_id)
        work_dir = self._resolve_inside(self._work_root, run_uuid.hex)
        try:
            work_dir.mkdir(exist_ok=False)
            output_dir = work_dir / "outputs"
            log_dir = work_dir / "logs"
            output_dir.mkdir()
            log_dir.mkdir()
            stdout_path = log_dir / "stdout.log"
            stderr_path = log_dir / "stderr.log"
            stdout_path.touch(exist_ok=False)
            stderr_path.touch(exist_ok=False)
        except FileExistsError as error:
            raise RunnerError(
                "E_RUNNER_ATTEMPT_EXISTS",
                RunnerFailureReason.PATH_INVALID,
                "node_run_id 对应的 attempt 目录已存在，禁止覆盖或恢复",
            ) from error
        except OSError as error:
            raise RunnerError(
                "E_RUNNER_ATTEMPT_CREATE_FAILED",
                RunnerFailureReason.PATH_INVALID,
                str(error),
            ) from error
        return _AttemptLayout(work_dir, output_dir, stdout_path, stderr_path)

    def _existing_layout(self, node_run_id: str) -> _AttemptLayout:
        run_uuid = self._node_run_uuid(node_run_id)
        work_dir = self._resolve_inside(self._work_root, run_uuid.hex)
        output_dir = self._resolve_inside(work_dir, "outputs")
        stdout_path = self._resolve_inside(work_dir, "logs/stdout.log")
        stderr_path = self._resolve_inside(work_dir, "logs/stderr.log")
        if not work_dir.is_dir() or not output_dir.is_dir():
            raise RunnerError(
                "E_RUNNER_HANDOFF_WORKDIR_MISSING",
                RunnerFailureReason.PATH_INVALID,
                "manual handoff attempt 目录不存在",
            )
        return _AttemptLayout(work_dir, output_dir, stdout_path, stderr_path)

    @staticmethod
    def _node_run_uuid(node_run_id: str) -> UUID:
        try:
            parsed = UUID(node_run_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise RunnerError(
                "E_RUNNER_NODE_RUN_ID_INVALID",
                RunnerFailureReason.PATH_INVALID,
                "node_run_id 必须是规范 UUIDv4，不能作为任意路径片段",
            ) from error
        if parsed.version != 4 or str(parsed) != node_run_id.lower():
            raise RunnerError(
                "E_RUNNER_NODE_RUN_ID_INVALID",
                RunnerFailureReason.PATH_INVALID,
                "node_run_id 必须是规范 UUIDv4，不能作为任意路径片段",
            )
        return parsed

    @staticmethod
    def _resolve_inside(base: Path, relative: str | Path) -> Path:
        candidate_path = Path(relative)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise RunnerError(
                "E_RUNNER_PATH_ESCAPE",
                RunnerFailureReason.PATH_INVALID,
                f"路径必须是 attempt 内相对路径：{relative}",
            )
        try:
            resolved_base = base.resolve(strict=True)
            resolved = (resolved_base / candidate_path).resolve(strict=False)
            resolved.relative_to(resolved_base)
        except (OSError, ValueError) as error:
            raise RunnerError(
                "E_RUNNER_PATH_ESCAPE",
                RunnerFailureReason.PATH_INVALID,
                f"路径逃逸 attempt：{relative}",
            ) from error
        return resolved

    def _output_targets(
        self,
        request: NodeExecutionRequest,
        layout: _AttemptLayout,
    ) -> tuple[OutputTarget, ...]:
        overrides: dict[str, str] = {}
        declared = {port.port_id for port in request.definition.output_ports}
        for item in request.output_paths:
            if item.port_id not in declared:
                raise self._configuration_error(
                    "E_RUNNER_OUTPUT_PORT_UNKNOWN", f"未知 output port {item.port_id!r}"
                )
            if item.port_id in overrides:
                raise self._configuration_error(
                    "E_RUNNER_OUTPUT_PATH_DUPLICATE",
                    f"output port {item.port_id!r} 重复声明路径",
                )
            if not item.relative_path or Path(item.relative_path) == Path("."):
                raise self._configuration_error(
                    "E_RUNNER_OUTPUT_PATH_INVALID", "output relative_path 不得为空"
                )
            overrides[item.port_id] = item.relative_path
        return tuple(
            OutputTarget(
                port_id=port.port_id,
                kind=port.data_type,
                path=self._resolve_inside(
                    layout.output_dir,
                    overrides.get(
                        port.port_id,
                        f"{index:03d}{_DEFAULT_OUTPUT_SUFFIX.get(port.data_type, '.out')}",
                    ),
                ),
            )
            for index, port in enumerate(request.definition.output_ports)
        )

    @staticmethod
    def _create_output_parents(targets: tuple[OutputTarget, ...], layout: _AttemptLayout) -> None:
        for target in targets:
            try:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                target.path.parent.resolve(strict=True).relative_to(layout.output_dir)
            except (OSError, ValueError) as error:
                raise RunnerError(
                    "E_RUNNER_PATH_ESCAPE",
                    RunnerFailureReason.PATH_INVALID,
                    f"output path 逃逸 attempt：{target.path}",
                ) from error

    def _run_python(
        self,
        executor: PythonExecutorSpec,
        context: PythonAdapterContext,
    ) -> PythonAdapterResult:
        adapter = self._python_adapters.get(executor.adapter)
        if adapter is None:
            raise self._configuration_error(
                "E_RUNNER_ADAPTER_UNKNOWN", f"未注册 Python adapter {executor.adapter!r}"
            )
        try:
            result = adapter(context)
        except (ProgressError, ProgressInfrastructureError):
            # Progress 的稳定合同码与 Repository 失败语义必须穿过 adapter 边界。
            raise
        except (RunnerCancelled, RunnerInterrupted, asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (SystemExit, GeneratorExit) as error:
            raise RunnerError(
                "E_RUNNER_ADAPTER_FAILED",
                RunnerFailureReason.ADAPTER_FAILED,
                str(error) or "Python adapter 非正常终止",
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            ) from error
        except Exception as error:
            raise RunnerError(
                "E_RUNNER_ADAPTER_FAILED",
                RunnerFailureReason.ADAPTER_FAILED,
                str(error),
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            ) from error
        if not isinstance(result, PythonAdapterResult):
            raise RunnerError(
                "E_RUNNER_ADAPTER_RESULT_INVALID",
                RunnerFailureReason.ADAPTER_FAILED,
                "Python adapter 必须返回 PythonAdapterResult",
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            )
        try:
            if not isinstance(result.outputs, tuple) or any(
                not isinstance(item, ProducedOutput)
                or not isinstance(item.port_id, str)
                or not item.port_id
                or not isinstance(item.path, Path)
                or (item.frame_range is not None and not isinstance(item.frame_range, FrameRange))
                or type(item.allow_external) is not bool
                for item in result.outputs
            ):
                raise TypeError("outputs 必须是 tuple[ProducedOutput, ...]，且字段类型有效")
            media_summary = self._strict_summary_mapping(
                result.media_summary,
                field_name="media_summary",
            )
            validation_summary = self._strict_summary_mapping(
                result.validation_summary,
                field_name="validation_summary",
            )
            producer_metadata = self._strict_producer_metadata(
                result.producer_metadata,
                declared_ports={port.port_id for port in context.definition.output_ports},
            )
        except (SystemExit, GeneratorExit, Exception) as error:
            raise RunnerError(
                "E_RUNNER_ADAPTER_RESULT_INVALID",
                RunnerFailureReason.ADAPTER_FAILED,
                str(error),
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            ) from error
        return PythonAdapterResult(
            outputs=result.outputs,
            media_summary=media_summary,
            validation_summary=validation_summary,
            producer_metadata=producer_metadata,
        )

    def _normalize_manual_submission(
        self,
        submission: ManualSubmission,
        layout: _AttemptLayout,
    ) -> ManualSubmission:
        """在人工提交边界拒绝伪造 dataclass 字段与非 JSON 摘要。"""

        if not isinstance(submission, ManualSubmission):
            raise RunnerError(
                "E_RUNNER_SUBMISSION_INVALID",
                RunnerFailureReason.VALIDATOR_FAILED,
                "submission 必须是 ManualSubmission",
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            )
        try:
            if not isinstance(submission.outputs, tuple) or any(
                not isinstance(item, ProducedOutput)
                or not isinstance(item.port_id, str)
                or not item.port_id
                or not isinstance(item.path, Path)
                or (item.frame_range is not None and not isinstance(item.frame_range, FrameRange))
                or type(item.allow_external) is not bool
                or item.allow_external
                for item in submission.outputs
            ):
                raise TypeError(
                    "outputs 必须是 attempt 内的 tuple[ProducedOutput, ...]，且字段类型有效"
                )
            media_summary = self._strict_summary_mapping(
                submission.media_summary,
                field_name="media_summary",
            )
            validation_summary = self._strict_summary_mapping(
                submission.validation_summary,
                field_name="validation_summary",
            )
        except (RunnerCancelled, RunnerInterrupted, asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (SystemExit, GeneratorExit, Exception) as error:
            raise RunnerError(
                "E_RUNNER_SUBMISSION_INVALID",
                RunnerFailureReason.VALIDATOR_FAILED,
                str(error) or "人工 submission 字段无效",
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            ) from error
        return ManualSubmission(
            outputs=submission.outputs,
            media_summary=media_summary,
            validation_summary=validation_summary,
        )

    def _run_command(
        self,
        executor: CommandExecutorSpec,
        context: PythonAdapterContext,
    ) -> int:
        argv = self._expand_argv(executor.argv, context)
        if "\x00" in executor.executable or any("\x00" in token for token in argv):
            raise RunnerError(
                "E_RUNNER_COMMAND_NUL",
                RunnerFailureReason.CONFIGURATION,
                "command executable 与 argv 不得包含 NUL",
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            )
        try:
            with (
                context.stdout_log_path.open("wb") as stdout_stream,
                context.stderr_log_path.open("wb") as stderr_stream,
            ):
                completed = subprocess.run(
                    [executor.executable, *argv],
                    cwd=context.work_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    creationflags=background_creation_flags(),
                    check=False,
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except (OSError, ValueError) as error:
            raise RunnerError(
                "E_RUNNER_PROCESS_START_FAILED",
                RunnerFailureReason.PROCESS_FAILED,
                str(error),
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            ) from error
        if completed.returncode != 0:
            raise RunnerError(
                "E_RUNNER_PROCESS_EXIT_NONZERO",
                RunnerFailureReason.PROCESS_FAILED,
                f"受控进程退出码为 {completed.returncode}",
                exit_code=completed.returncode,
                stdout_log_path=context.stdout_log_path,
                stderr_log_path=context.stderr_log_path,
            )
        return completed.returncode

    def _expand_argv(
        self,
        argv: tuple[str, ...],
        context: PythonAdapterContext,
    ) -> list[str]:
        inputs: dict[str, list[RunnerInput]] = {}
        for item in context.inputs:
            inputs.setdefault(item.port_id, []).append(item)
        outputs = {item.port_id: item.path for item in context.outputs}
        cardinalities = {item.port_id: item.cardinality for item in context.definition.input_ports}

        expanded: list[str] = []
        for token in argv:
            # 只有完整已知 placeholder 才解释；双层花括号允许把该完整 token 原样传给工具。
            if token.startswith("{{") and token.endswith("}}"):
                literal = token[1:-1]
                if _PLACEHOLDER.fullmatch(literal) is not None:
                    expanded.append(literal)
                    continue
            match = _PLACEHOLDER.fullmatch(token)
            if match is None:
                expanded.append(token)
                continue
            kind, name = match.group("kind"), match.group("name")
            if kind == "workdir":
                if name is not None:
                    raise self._configuration_error(
                        "E_RUNNER_PLACEHOLDER_INVALID", "workdir 不接受名称"
                    )
                expanded.append(str(context.work_dir))
            elif kind == "input":
                values = inputs.get(cast(str, name), [])
                if cardinalities.get(cast(str, name)) is not Cardinality.ONE or len(values) != 1:
                    raise self._configuration_error(
                        "E_RUNNER_PLACEHOLDER_INPUT_INVALID",
                        f"{{input:{name}}} 需要一个已绑定 one input",
                    )
                expanded.append(str(values[0].path))
            elif kind == "inputs":
                values = inputs.get(cast(str, name), [])
                if cardinalities.get(cast(str, name)) is not Cardinality.ORDERED_MANY:
                    raise self._configuration_error(
                        "E_RUNNER_PLACEHOLDER_INPUTS_INVALID",
                        f"{{inputs:{name}}} 只适用于 ordered_many input",
                    )
                expanded.extend(str(item.path) for item in values)
            elif kind == "output":
                output = outputs.get(cast(str, name))
                if output is None:
                    raise self._configuration_error(
                        "E_RUNNER_PLACEHOLDER_OUTPUT_INVALID",
                        f"未知 output port {name!r}",
                    )
                expanded.append(str(output))
            else:
                if name not in context.node.parameters:
                    raise self._configuration_error(
                        "E_RUNNER_PLACEHOLDER_PARAMETER_INVALID",
                        f"未知 parameter {name!r}",
                    )
                expanded.append(self._scalar_parameter(context.node.parameters[cast(str, name)]))
        return expanded

    @staticmethod
    def _scalar_parameter(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            return str(value)
        raise RunnerError(
            "E_RUNNER_PARAMETER_NOT_SCALAR",
            RunnerFailureReason.CONFIGURATION,
            "command placeholder 只支持 string/number/boolean 标量参数",
        )

    def _resolve_produced_outputs(
        self,
        produced: tuple[ProducedOutput, ...],
        targets: tuple[OutputTarget, ...],
        work_dir: Path,
    ) -> tuple[OutputTarget, ...]:
        if not produced:
            return targets
        expected = {item.port_id: item for item in targets}
        actual: dict[str, tuple[Path, FrameRange | None, bool]] = {}
        for item in produced:
            if item.port_id not in expected or item.port_id in actual:
                raise self._configuration_error(
                    "E_RUNNER_OUTPUT_BINDING_INVALID",
                    f"输出 {item.port_id!r} 未声明或重复",
                )
            path = Path(item.path)
            if item.allow_external:
                if not path.is_absolute():
                    raise RunnerError(
                        "E_RUNNER_EXTERNAL_PATH_INVALID",
                        RunnerFailureReason.PATH_INVALID,
                        f"外部 adapter output 必须是绝对路径：{path}",
                    )
                try:
                    resolved = path.resolve(strict=False)
                except OSError as error:
                    raise RunnerError(
                        "E_RUNNER_EXTERNAL_PATH_INVALID",
                        RunnerFailureReason.PATH_INVALID,
                        f"无法解析外部 adapter output：{path}",
                    ) from error
            elif path.is_absolute():
                try:
                    resolved = path.resolve(strict=False)
                    resolved.relative_to(work_dir.resolve(strict=True))
                except (OSError, ValueError) as error:
                    raise RunnerError(
                        "E_RUNNER_PATH_ESCAPE",
                        RunnerFailureReason.PATH_INVALID,
                        f"adapter output 逃逸 attempt：{path}",
                    ) from error
            else:
                resolved = self._resolve_inside(work_dir, path)
            actual[item.port_id] = (resolved, item.frame_range, item.allow_external)
        if set(actual) != set(expected):
            raise self._configuration_error(
                "E_RUNNER_OUTPUT_BINDING_INCOMPLETE", "adapter 必须提交全部声明 output"
            )
        return tuple(
            OutputTarget(
                item.port_id,
                item.kind,
                actual[item.port_id][0],
                frame_range=actual[item.port_id][1],
                allow_external=actual[item.port_id][2],
            )
            for item in targets
        )

    def _validate_and_build_result(
        self,
        request: NodeExecutionRequest,
        layout: _AttemptLayout,
        outputs: tuple[OutputTarget, ...],
        *,
        exit_code: int | None,
        media_summary: Mapping[str, object],
        adapter_validation_summary: Mapping[str, object],
        producer_metadata: Mapping[str, Mapping[str, object]],
    ) -> RunnerResult:
        validated, node_validation = self._validate_outputs(
            request,
            layout,
            outputs,
            exit_code=exit_code,
            producer_metadata=producer_metadata,
        )
        artifacts = tuple(
            RunnerArtifact(
                artifact_id=str(uuid4()),
                kind=item.kind,
                path=item.path,
                producer_node_run_id=request.node_run_id,
                producer_port_id=item.port_id,
                ordinal=None,
                frame_range=item.frame_range,
                media_info=cast(Mapping[str, object], _plain_json(item.media_info)),
                size=item.size,
                mtime_ns=item.mtime_ns,
            )
            for item in validated
        )
        output_media = {
            item.port_id: cast(dict[str, object], _plain_json(item.media_info))
            for item in validated
        }
        validation: dict[str, object] = {
            "default": {
                item.port_id: {
                    "exists": True,
                    "nonempty": True,
                    "probe": item.kind in _MEDIA_TYPES,
                }
                for item in validated
            },
            "adapter": dict(adapter_validation_summary),
        }
        if node_validation is not None:
            validation["node"] = dict(node_validation.summary)
            validation["warnings"] = list(node_validation.warnings)
        return RunnerResult(
            result_id=str(uuid4()),
            node_run_id=request.node_run_id,
            attempt=request.attempt,
            artifacts=artifacts,
            work_dir=layout.work_dir,
            stdout_log_path=layout.stdout_log_path,
            stderr_log_path=layout.stderr_log_path,
            exit_code=exit_code,
            media_summary={"adapter": dict(media_summary), "outputs": output_media},
            validation_summary=validation,
        )

    def _validate_outputs(
        self,
        request: NodeExecutionRequest,
        layout: _AttemptLayout,
        outputs: tuple[OutputTarget, ...],
        *,
        exit_code: int | None,
        producer_metadata: Mapping[str, Mapping[str, object]],
    ) -> tuple[tuple[ValidatedOutput, ...], NodeValidatorResult | None]:
        """执行不产生结果 identity 的共享输出验证路径。"""

        validated: list[ValidatedOutput] = []
        for output in outputs:
            try:
                resolved = output.path.resolve(strict=True)
                if not output.allow_external:
                    resolved.relative_to(layout.work_dir.resolve(strict=True))
                stat = resolved.stat()
            except (OSError, ValueError) as error:
                raise RunnerError(
                    "E_RUNNER_OUTPUT_MISSING",
                    RunnerFailureReason.OUTPUT_INVALID,
                    f"输出不存在或逃逸 attempt：{output.path}",
                    exit_code=exit_code,
                    stdout_log_path=layout.stdout_log_path,
                    stderr_log_path=layout.stderr_log_path,
                ) from error
            if not resolved.is_file() or stat.st_size <= 0:
                raise RunnerError(
                    "E_RUNNER_OUTPUT_EMPTY",
                    RunnerFailureReason.OUTPUT_INVALID,
                    f"输出不是非空文件：{resolved}",
                    exit_code=exit_code,
                    stdout_log_path=layout.stdout_log_path,
                    stderr_log_path=layout.stderr_log_path,
                )
            media_info: Mapping[str, object] = MappingProxyType({})
            if output.kind in _MEDIA_TYPES:
                try:
                    media_info = self._strict_readonly_mapping(
                        self._media_probe(resolved, output.kind),
                        field_name=f"probe.{output.port_id}",
                    )
                except RunnerError:
                    raise
                except (SystemExit, GeneratorExit) as error:
                    raise RunnerError(
                        "E_RUNNER_PROBE_FAILED",
                        RunnerFailureReason.PROBE_FAILED,
                        str(error) or "媒体 probe 非正常终止",
                        exit_code=exit_code,
                        stdout_log_path=layout.stdout_log_path,
                        stderr_log_path=layout.stderr_log_path,
                    ) from error
                except Exception as error:
                    raise RunnerError(
                        "E_RUNNER_PROBE_FAILED",
                        RunnerFailureReason.PROBE_FAILED,
                        str(error),
                        exit_code=exit_code,
                        stdout_log_path=layout.stdout_log_path,
                        stderr_log_path=layout.stderr_log_path,
                    ) from error
            validated.append(
                ValidatedOutput(
                    port_id=output.port_id,
                    kind=output.kind,
                    path=resolved,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    media_info=media_info,
                    producer_metadata=producer_metadata.get(output.port_id, MappingProxyType({})),
                    frame_range=output.frame_range,
                )
            )

        values = tuple(validated)
        node_validation = self._run_node_validator(request, layout, values)
        if node_validation is not None:
            values = self._merge_media_info_extensions(values, node_validation)
        return values, node_validation

    def _run_node_validator(
        self,
        request: NodeExecutionRequest,
        layout: _AttemptLayout,
        outputs: tuple[ValidatedOutput, ...],
    ) -> NodeValidatorResult | None:
        spec = request.definition.validator
        if spec is None:
            return None
        validator = self._validators.get(spec.adapter)
        if validator is None:
            raise self._configuration_error(
                "E_RUNNER_VALIDATOR_UNKNOWN", f"未注册 validator {spec.adapter!r}"
            )
        try:
            result = validator(
                NodeValidatorContext(request=request, work_dir=layout.work_dir, outputs=outputs)
            )
        except (RunnerCancelled, RunnerInterrupted, asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (SystemExit, GeneratorExit) as error:
            raise RunnerError(
                "E_RUNNER_VALIDATOR_FAILED",
                RunnerFailureReason.VALIDATOR_FAILED,
                str(error) or "节点 validator 非正常终止",
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            ) from error
        except Exception as error:
            raise RunnerError(
                "E_RUNNER_VALIDATOR_FAILED",
                RunnerFailureReason.VALIDATOR_FAILED,
                str(error),
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            ) from error
        if not isinstance(result, NodeValidatorResult):
            raise RunnerError(
                "E_RUNNER_VALIDATOR_RESULT_INVALID",
                RunnerFailureReason.VALIDATOR_FAILED,
                "validator 必须返回 NodeValidatorResult",
            )
        try:
            if type(result.passed) is not bool:
                raise TypeError("passed 必须是 bool")
            summary = self._strict_summary_mapping(result.summary, field_name="summary")
            if not isinstance(result.warnings, tuple) or any(
                not isinstance(item, str) for item in result.warnings
            ):
                raise TypeError("warnings 必须是 tuple[str, ...]")
            if result.message is not None and not isinstance(result.message, str):
                raise TypeError("message 必须是 string 或 None")
            media_info_extensions = self._strict_media_info_extensions(
                result.media_info_extensions,
                outputs=outputs,
            )
        except (SystemExit, GeneratorExit, Exception) as error:
            raise RunnerError(
                "E_RUNNER_VALIDATOR_RESULT_INVALID",
                RunnerFailureReason.VALIDATOR_FAILED,
                str(error),
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            ) from error
        validated_result = NodeValidatorResult(
            passed=result.passed,
            summary=summary,
            warnings=result.warnings,
            message=result.message,
            media_info_extensions=media_info_extensions,
        )
        if not validated_result.passed:
            raise RunnerError(
                "E_RUNNER_VALIDATION_REJECTED",
                RunnerFailureReason.VALIDATOR_FAILED,
                validated_result.message or "节点轻量 validator 未通过",
                stdout_log_path=layout.stdout_log_path,
                stderr_log_path=layout.stderr_log_path,
            )
        return validated_result

    @staticmethod
    def _merge_media_info_extensions(
        outputs: tuple[ValidatedOutput, ...],
        result: NodeValidatorResult,
    ) -> tuple[ValidatedOutput, ...]:
        """只在 validator 已整体通过后合并已校验 namespace extension。"""

        merged: list[ValidatedOutput] = []
        for output in outputs:
            media_info = dict(output.media_info)
            extensions = result.media_info_extensions.get(output.port_id, {})
            media_info.update(extensions)
            merged.append(
                replace(
                    output,
                    media_info=cast(Mapping[str, object], _readonly_json(media_info)),
                )
            )
        return tuple(merged)

    @classmethod
    def _strict_producer_metadata(
        cls,
        value: Mapping[str, Mapping[str, object]],
        *,
        declared_ports: set[str],
    ) -> Mapping[str, Mapping[str, object]]:
        """严格收敛 adapter 的 per-port 原始事实，不解释任何业务字段。"""

        if not isinstance(value, Mapping):
            raise TypeError("producer_metadata 必须是 mapping")
        normalized: dict[str, Mapping[str, object]] = {}
        for port_id, metadata in value.items():
            if type(port_id) is not str or not port_id:
                raise TypeError("producer_metadata 的 port key 必须是非空 string")
            if port_id in normalized:
                raise TypeError(f"producer_metadata 的 port {port_id!r} 重复")
            if port_id not in declared_ports:
                raise TypeError(f"producer_metadata 引用未知 output port {port_id!r}")
            normalized[port_id] = cls._strict_readonly_mapping(
                metadata,
                field_name=f"producer_metadata.{port_id}",
            )
        return cast(
            Mapping[str, Mapping[str, object]],
            MappingProxyType(normalized),
        )

    @classmethod
    def _strict_media_info_extensions(
        cls,
        value: Mapping[str, Mapping[str, Mapping[str, object]]],
        *,
        outputs: tuple[ValidatedOutput, ...],
    ) -> Mapping[str, Mapping[str, Mapping[str, object]]]:
        """验证 validator extension 的 port、namespace、JSON 与不可覆盖边界。"""

        if not isinstance(value, Mapping):
            raise TypeError("media_info_extensions 必须是 mapping")
        outputs_by_port = {item.port_id: item for item in outputs}
        normalized: dict[str, Mapping[str, Mapping[str, object]]] = {}
        for port_id, namespaces in value.items():
            if type(port_id) is not str or not port_id:
                raise TypeError("media_info_extensions 的 port key 必须是非空 string")
            if port_id in normalized:
                raise TypeError(f"media_info_extensions 的 port {port_id!r} 重复")
            output = outputs_by_port.get(port_id)
            if output is None:
                raise TypeError(f"media_info_extensions 引用未知 output port {port_id!r}")
            if not isinstance(namespaces, Mapping):
                raise TypeError(f"media_info_extensions.{port_id} 必须是 mapping")
            normalized_namespaces: dict[str, Mapping[str, object]] = {}
            for namespace, extension in namespaces.items():
                if type(namespace) is not str or _MEDIA_INFO_NAMESPACE.fullmatch(namespace) is None:
                    raise TypeError(
                        f"media_info_extensions.{port_id} namespace 不是稳定点分 identifier"
                    )
                if namespace in normalized_namespaces:
                    raise TypeError(f"media_info_extensions.{port_id} namespace {namespace!r} 重复")
                if namespace in output.media_info:
                    raise TypeError(
                        f"media_info_extensions.{port_id} 不得覆盖既有 namespace {namespace!r}"
                    )
                normalized_namespaces[namespace] = cls._strict_readonly_mapping(
                    extension,
                    field_name=f"media_info_extensions.{port_id}.{namespace}",
                )
            normalized[port_id] = cast(
                Mapping[str, Mapping[str, object]],
                MappingProxyType(normalized_namespaces),
            )
        return cast(
            Mapping[str, Mapping[str, Mapping[str, object]]],
            MappingProxyType(normalized),
        )

    @classmethod
    def _strict_readonly_mapping(
        cls,
        value: Mapping[str, object],
        *,
        field_name: str,
    ) -> Mapping[str, object]:
        normalized = cls._strict_summary_mapping(value, field_name=field_name)
        return cast(Mapping[str, object], _readonly_json(normalized))

    @classmethod
    def _strict_summary_mapping(
        cls,
        value: Mapping[str, object],
        *,
        field_name: str,
    ) -> dict[str, object]:
        """把插件摘要递归收敛为普通 JSON object，拒绝宽松运行时值。"""

        if not isinstance(value, Mapping):
            raise TypeError(f"{field_name} 必须是 mapping")
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{field_name} 的键必须是 string")
            if key in result:
                raise TypeError(f"{field_name} 的键 {key!r} 重复")
            result[key] = cls._strict_json_value(item, field_name=f"{field_name}.{key}")
        return result

    @classmethod
    def _strict_json_value(cls, value: object, *, field_name: str) -> object:
        if value is None or type(value) in {str, bool, int}:
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise TypeError(f"{field_name} 不得是 NaN 或 Infinity")
            return value
        if isinstance(value, Mapping):
            return cls._strict_summary_mapping(value, field_name=field_name)
        if isinstance(value, list | tuple):
            return [
                cls._strict_json_value(item, field_name=f"{field_name}[{index}]")
                for index, item in enumerate(value)
            ]
        raise TypeError(f"{field_name} 必须是 JSON value")

    def _assert_handoff_matches(
        self,
        request: NodeExecutionRequest,
        handoff: ManualHandoff,
        layout: _AttemptLayout,
        targets: tuple[OutputTarget, ...],
        ordered_inputs: tuple[RunnerInput, ...],
        instructions: str | None,
    ) -> None:
        expected_inputs = tuple(
            HandoffInput(
                item.port_id,
                item.artifact_id,
                item.kind,
                str(item.path),
                item.ordinal,
            )
            for item in ordered_inputs
        )
        expected_outputs = tuple(
            HandoffOutput(item.port_id, item.kind, str(item.path)) for item in targets
        )
        if (
            handoff.schema_version != _HANDOFF_SCHEMA_VERSION
            or handoff.node_run_id != request.node_run_id
            or handoff.attempt != request.attempt
            or handoff.type_id != request.definition.type_id
            or handoff.definition_version != request.definition.version
            or Path(handoff.work_dir).resolve(strict=False) != layout.work_dir
            or handoff.inputs != expected_inputs
            or handoff.outputs != expected_outputs
            or handoff.instructions != instructions
        ):
            raise self._configuration_error(
                "E_RUNNER_HANDOFF_STALE", "handoff 与当前 NodeRun attempt 不一致"
            )

    def _build_ffprobe(self, executable: str) -> MediaProbe:
        if not executable.strip():
            raise self._configuration_error(
                "E_RUNNER_FFPROBE_INVALID", "ffprobe executable 不得为空"
            )

        def probe(path: Path, kind: str) -> Mapping[str, object]:
            try:
                completed = subprocess.run(
                    [
                        executable,
                        "-v",
                        "error",
                        "-show_streams",
                        "-show_format",
                        "-of",
                        "json",
                        str(path),
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    shell=False,
                    creationflags=background_creation_flags(),
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RunnerError(
                    "E_RUNNER_PROBE_FAILED", RunnerFailureReason.PROBE_FAILED, str(error)
                ) from error
            if completed.returncode != 0:
                detail = completed.stderr.strip()[:1000]
                raise RunnerError(
                    "E_RUNNER_PROBE_FAILED",
                    RunnerFailureReason.PROBE_FAILED,
                    detail or f"ffprobe 退出码为 {completed.returncode}",
                    exit_code=completed.returncode,
                )
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise RunnerError(
                    "E_RUNNER_PROBE_INVALID_JSON",
                    RunnerFailureReason.PROBE_FAILED,
                    str(error),
                ) from error
            if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
                raise RunnerError(
                    "E_RUNNER_PROBE_STREAM_MISSING",
                    RunnerFailureReason.PROBE_FAILED,
                    "ffprobe 未返回 streams array",
                )
            streams = payload["streams"]
            expected_stream = {
                "VideoFile": "video",
                "AudioFile": "audio",
            }.get(kind)
            if not streams or (
                expected_stream is not None
                and not any(
                    isinstance(stream, dict) and stream.get("codec_type") == expected_stream
                    for stream in streams
                )
            ):
                raise RunnerError(
                    "E_RUNNER_PROBE_STREAM_MISSING",
                    RunnerFailureReason.PROBE_FAILED,
                    f"输出不包含声明的 {kind} 媒体流",
                )
            return cast(Mapping[str, object], payload)

        return probe

    @staticmethod
    def _configuration_error(code: str, message: str) -> RunnerError:
        return RunnerError(code, RunnerFailureReason.CONFIGURATION, message)

    @staticmethod
    def _control_error(
        code: str,
        reason: RunnerFailureReason,
        message: str,
        layout: _AttemptLayout,
    ) -> RunnerError:
        return RunnerError(
            code,
            reason,
            message,
            stdout_log_path=layout.stdout_log_path,
            stderr_log_path=layout.stderr_log_path,
        )
