"""实现只读 Installed Engine authority 与 SDK conformance gate。

Catalog 只接受进程启动时由受信组装根注入的 package，不扫描任意目录、不解释 manifest 中的字符串，
也不提供下载或安装副作用。Engine 输出必须重新通过 Phase 1 合同校验后才可返回给未来 Runtime。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from zniku.contracts import (
    Cardinality,
    ContractViolation,
    EngineBinding,
    EngineManifest,
    ExecutionMode,
    validate_stage_run_bindings,
)

from .models import (
    ArtifactValue,
    EngineInvocationRequest,
    EngineInvocationResult,
    EnginePackageDescriptor,
    InstalledEngineRecord,
    InstalledEngineState,
    artifact_value_id,
)


class EngineAdapter(Protocol):
    """受信 Engine adapter 的最小 Python 调用面。"""

    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        """处理已验证输入并返回尚未发布的候选输出。"""


@dataclass(frozen=True, slots=True)
class EnginePackage:
    """可信组装根持有的 manifest、descriptor 与 adapter 绑定。"""

    descriptor: EnginePackageDescriptor
    manifest: EngineManifest
    adapter: EngineAdapter

    def __post_init__(self) -> None:
        if self.descriptor.engine != EngineBinding.from_manifest(self.manifest):
            raise ContractViolation(
                "E_ENGINE_PACKAGE_MANIFEST_MISMATCH",
                "Engine package descriptor 与 manifest identity 不一致",
            )


def _binding_key(binding: EngineBinding) -> tuple[str, str, str]:
    return (binding.engine_id, binding.engine_version, binding.manifest_digest)


class InstalledEngineCatalog:
    """按精确 EngineBinding 解析受信 package 的只读 authority。"""

    def __init__(
        self,
        packages: Iterable[EnginePackage],
        *,
        disabled_package_ids: Iterable[str] = (),
    ) -> None:
        disabled = frozenset(disabled_package_ids)
        by_binding: dict[tuple[str, str, str], EnginePackage] = {}
        by_package_id: dict[str, EnginePackage] = {}
        records: list[InstalledEngineRecord] = []
        for package in packages:
            package_id = package.descriptor.package_id
            if package_id in by_package_id:
                raise ContractViolation(
                    "E_ENGINE_PACKAGE_ID_DUPLICATE", f"package_id 重复：{package_id}"
                )
            key = _binding_key(package.descriptor.engine)
            if key in by_binding:
                raise ContractViolation(
                    "E_ENGINE_BINDING_DUPLICATE",
                    "精确 EngineBinding 被多个 package 提供："
                    f"{package.descriptor.engine.engine_id}",
                )
            by_package_id[package_id] = package
            by_binding[key] = package
            records.append(
                InstalledEngineRecord(
                    descriptor=package.descriptor,
                    state=(
                        InstalledEngineState.DISABLED
                        if package_id in disabled
                        else InstalledEngineState.ENABLED
                    ),
                )
            )
        unknown_disabled = disabled - set(by_package_id)
        if unknown_disabled:
            raise ContractViolation(
                "E_ENGINE_DISABLED_PACKAGE_UNKNOWN",
                f"disabled package 不存在：{', '.join(sorted(unknown_disabled))}",
            )
        self._by_binding = by_binding
        self._records = tuple(sorted(records, key=lambda item: item.descriptor.package_id))

    def records(self) -> tuple[InstalledEngineRecord, ...]:
        return self._records

    def resolve(self, binding: EngineBinding) -> EngineManifest | None:
        """为 Compiler 提供精确、只读的 ManifestCatalog 兼容接口。"""

        package = self._by_binding.get(_binding_key(binding))
        if package is None or not self._enabled(package.descriptor.package_id):
            return None
        return package.manifest

    def require_package(self, binding: EngineBinding) -> EnginePackage:
        package = self._by_binding.get(_binding_key(binding))
        if package is None:
            raise ContractViolation("E_ENGINE_NOT_INSTALLED", "精确 Engine package 未安装")
        if not self._enabled(package.descriptor.package_id):
            raise ContractViolation("E_ENGINE_DISABLED", "精确 Engine package 已禁用")
        return package

    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        """执行 automatic adapter，并对输入和候选输出执行完整 conformance gate。"""

        package = self.require_package(request.stage_run.engine)
        manifest = package.manifest
        if manifest.execution_mode is not ExecutionMode.AUTOMATIC:
            raise ContractViolation(
                "E_ENGINE_MANUAL_INVOCATION_FORBIDDEN",
                "manual_external Engine 不能通过 automatic SDK invoke 调用",
            )

        input_authority = {
            artifact_value_id(value): (
                value.artifact if isinstance(value, ArtifactValue) else value.artifact_set
            )
            for value in request.inputs
        }
        validate_stage_run_bindings(request.stage_run, manifest, input_authority)

        result = package.adapter.invoke(request)
        if result.invocation_id != request.invocation_id:
            raise ContractViolation(
                "E_ENGINE_RESULT_INVOCATION_MISMATCH", "Engine result invocation_id 不匹配"
            )
        if result.engine != request.stage_run.engine:
            raise ContractViolation(
                "E_ENGINE_RESULT_BINDING_MISMATCH", "Engine result identity 不匹配"
            )

        required_outputs = {
            contract.port.port_id
            for contract in manifest.outputs
            if contract.port.cardinality is not Cardinality.OPTIONAL
        }
        actual_outputs = {value.port_id for value in result.outputs}
        missing = required_outputs - actual_outputs
        if missing:
            raise ContractViolation(
                "E_ENGINE_RESULT_OUTPUT_MISSING",
                f"候选输出缺少必需端口：{', '.join(sorted(missing))}",
            )

        output_authority = {
            artifact_value_id(value): (
                value.artifact if isinstance(value, ArtifactValue) else value.artifact_set
            )
            for value in result.outputs
        }
        if set(input_authority) & set(output_authority):
            raise ContractViolation(
                "E_ENGINE_RESULT_NO_REPLACE", "Engine 候选输出不得复用输入 authority ID"
            )
        stage_with_outputs = request.stage_run.model_copy(
            update={"outputs": tuple(value.binding() for value in result.outputs)}
        )
        validate_stage_run_bindings(
            stage_with_outputs,
            manifest,
            {**input_authority, **output_authority},
        )
        return result

    def _enabled(self, package_id: str) -> bool:
        return any(
            record.descriptor.package_id == package_id
            and record.state is InstalledEngineState.ENABLED
            for record in self._records
        )
