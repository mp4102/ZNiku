"""实现 Studio 与 Agent 共用的 Application Service 命令面。

服务持有 preparation、WorkflowRun 与 command idempotency authority；客户端只能提交稳定 ID 和正式领域
对象，不能提交 executable、shell 或 Engine 私有入口。运行状态始终由 `DefaultWorkflowRuntime` 派生。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypeVar, cast

from pydantic import JsonValue, SkipValidation, field_validator

from zniku.authoring import SpecValidationResult, WorkflowSpec
from zniku.contracts import Artifact, ContractModel, ContractViolation, StableId
from zniku.pipelines import (
    DefaultRunSnapshot,
    DefaultWorkflowBundle,
    DefaultWorkflowRuntime,
    ExternalOutputSubmission,
    FullVerificationRecord,
    ManualHandoff,
    build_default_workflow,
)
from zniku.workflow.execution import (
    ChapterPlan,
    ExecutionPlan,
    PreflightResult,
    SourceBinding,
    WorkflowBindingSet,
    WorkflowRevision,
    compile_execution_plan,
    freeze_revision,
    preflight_workflow,
)

APPLICATION_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
T = TypeVar("T")


class PreparedRun(ContractModel):
    """无媒体副作用的 compile/freeze 结果，由 Application Service 缓存。"""

    application_contract_version: Literal["0.1.0"]
    preparation_id: StableId
    source_artifact: SkipValidation[Artifact]
    chapter_plan: SkipValidation[ChapterPlan]
    bindings: SkipValidation[WorkflowBindingSet]
    preflight: SkipValidation[PreflightResult]
    plan: SkipValidation[ExecutionPlan]
    revision: SkipValidation[WorkflowRevision]

    @field_validator(
        "source_artifact",
        "chapter_plan",
        "bindings",
        "preflight",
        "plan",
        "revision",
        mode="before",
    )
    @classmethod
    def normalize_authority(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        types: dict[str, type[ContractModel]] = {
            "source_artifact": Artifact,
            "chapter_plan": ChapterPlan,
            "bindings": WorkflowBindingSet,
            "preflight": PreflightResult,
            "plan": ExecutionPlan,
            "revision": WorkflowRevision,
        }
        return types[info.field_name].from_data(cast(Mapping[str, JsonValue], value))


class RunSummary(ContractModel):
    """客户端可读取的最小运行摘要，不允许客户端据此写回状态。"""

    application_contract_version: Literal["0.1.0"]
    workflow_run_id: StableId
    revision_id: StableId
    snapshot_digest: str
    ready_node_ids: tuple[StableId, ...]
    final_verification_id: StableId | None = None


class ApplicationService:
    """默认工作流的单进程 application authority 与幂等命令路由。"""

    def __init__(self, bundle: DefaultWorkflowBundle | None = None) -> None:
        self._bundle = bundle or build_default_workflow()
        self._preparations: dict[str, PreparedRun] = {}
        self._runs: dict[str, tuple[str, DefaultWorkflowRuntime]] = {}
        self._commands: dict[str, tuple[str, object]] = {}

    @property
    def bundle(self) -> DefaultWorkflowBundle:
        return self._bundle

    def validate_workflow(self, spec: WorkflowSpec) -> SpecValidationResult:
        """复用唯一 Compiler；Agent/Studio 不拥有第二套图校验。"""

        return self._bundle.compiler.validate(spec)

    def prepare_default(self, source: Artifact, chapter_plan: ChapterPlan) -> PreparedRun:
        """完成 source binding、preflight、compile 和 freeze，但不启动 Run。"""

        bindings = WorkflowBindingSet(
            binding_contract_version="0.1.0",
            sources=(
                SourceBinding(
                    source_node_id="node.source",
                    artifact_id=source.artifact_id,
                    artifact_digest=source.sha256_digest(),
                ),
            ),
            chapter_plan=chapter_plan,
        )
        preflight = preflight_workflow(
            self._bundle.spec,
            bindings,
            {source.artifact_id: source},
            self._bundle.compiler,
        )
        if not preflight.valid:
            raise ContractViolation(
                "E_APPLICATION_PREFLIGHT_INVALID",
                "默认工作流 preflight 失败：" + ", ".join(preflight.diagnostic_codes),
            )
        plan = compile_execution_plan(self._bundle.spec, bindings, preflight)
        revision = freeze_revision(self._bundle.spec, bindings, plan)
        seed = f"{revision.revision_id}\0{source.sha256_digest()}\0{chapter_plan.sha256_digest()}"
        preparation_id = f"preparation.{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
        prepared = PreparedRun(
            application_contract_version=APPLICATION_CONTRACT_VERSION,
            preparation_id=preparation_id,
            source_artifact=source,
            chapter_plan=chapter_plan,
            bindings=bindings,
            preflight=preflight,
            plan=plan,
            revision=revision,
        )
        existing = self._preparations.get(preparation_id)
        if existing is not None and existing != prepared:
            raise ContractViolation("E_PREPARATION_ID_COLLISION", "preparation identity 冲突")
        self._preparations[preparation_id] = prepared
        return prepared

    def start_run(
        self, *, command_id: str, preparation_id: str, workflow_run_id: str
    ) -> DefaultRunSnapshot:
        fingerprint = self._fingerprint("start_run", preparation_id, workflow_run_id)

        def action() -> DefaultRunSnapshot:
            prepared = self._require_preparation(preparation_id)
            if workflow_run_id in self._runs:
                existing_preparation, runtime = self._runs[workflow_run_id]
                if existing_preparation != preparation_id:
                    raise ContractViolation(
                        "E_WORKFLOW_RUN_ID_COLLISION", "run ID 已绑定其他 revision"
                    )
                return runtime.snapshot
            runtime = DefaultWorkflowRuntime.start(
                plan=prepared.plan,
                revision=prepared.revision,
                manifests=self._bundle.manifests,
                source_artifact=prepared.source_artifact,
                chapter_plan=prepared.chapter_plan,
                workflow_run_id=workflow_run_id,
            )
            self._runs[workflow_run_id] = (preparation_id, runtime)
            return runtime.snapshot

        return self._idempotent(command_id, fingerprint, action, DefaultRunSnapshot)

    def inspect_run(self, workflow_run_id: str) -> DefaultRunSnapshot:
        return self._require_runtime(workflow_run_id).snapshot

    def summarize_run(self, workflow_run_id: str) -> RunSummary:
        snapshot = self.inspect_run(workflow_run_id)
        verification = snapshot.full_verification
        return RunSummary(
            application_contract_version=APPLICATION_CONTRACT_VERSION,
            workflow_run_id=workflow_run_id,
            revision_id=snapshot.runtime.revision_id,
            snapshot_digest=snapshot.sha256_digest(),
            ready_node_ids=self._require_runtime(workflow_run_id).ready_nodes(),
            final_verification_id=(
                verification.verification_id if verification is not None else None
            ),
        )

    def list_ready_nodes(self, workflow_run_id: str) -> tuple[str, ...]:
        return self._require_runtime(workflow_run_id).ready_nodes()

    def execute_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> DefaultRunSnapshot:
        fingerprint = self._fingerprint("execute_node", workflow_run_id, plan_node_id)
        return self._idempotent(
            command_id,
            fingerprint,
            lambda: self._require_runtime(workflow_run_id).execute_automatic(plan_node_id),
            DefaultRunSnapshot,
        )

    def prepare_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> ManualHandoff:
        fingerprint = self._fingerprint("prepare_node", workflow_run_id, plan_node_id)
        return self._idempotent(
            command_id,
            fingerprint,
            lambda: self._require_runtime(workflow_run_id).prepare_manual(plan_node_id),
            ManualHandoff,
        )

    def submit_external_output(
        self,
        *,
        command_id: str,
        workflow_run_id: str,
        submission: ExternalOutputSubmission,
    ) -> DefaultRunSnapshot:
        fingerprint = self._fingerprint(
            "submit_external_output", workflow_run_id, submission.sha256_digest()
        )
        return self._idempotent(
            command_id,
            fingerprint,
            lambda: self._require_runtime(workflow_run_id).submit_external_output(submission),
            DefaultRunSnapshot,
        )

    def retry_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> DefaultRunSnapshot:
        fingerprint = self._fingerprint("retry_node", workflow_run_id, plan_node_id)
        return self._idempotent(
            command_id,
            fingerprint,
            lambda: self._require_runtime(workflow_run_id).retry(plan_node_id),
            DefaultRunSnapshot,
        )

    def publish_final(self, workflow_run_id: str) -> FullVerificationRecord:
        """只返回 Runtime 已产生的唯一 Final 证明，不提供强制发布入口。"""

        verification = self.inspect_run(workflow_run_id).full_verification
        if verification is None:
            raise ContractViolation("E_FINAL_NOT_VERIFIED", "Final 尚未完成 full verification")
        return verification

    def _require_preparation(self, preparation_id: str) -> PreparedRun:
        try:
            return self._preparations[preparation_id]
        except KeyError as error:
            raise ContractViolation("E_PREPARATION_UNKNOWN", "未知 preparation") from error

    def _require_runtime(self, workflow_run_id: str) -> DefaultWorkflowRuntime:
        try:
            return self._runs[workflow_run_id][1]
        except KeyError as error:
            raise ContractViolation("E_WORKFLOW_RUN_UNKNOWN", "未知 WorkflowRun") from error

    def _idempotent(
        self,
        command_id: str,
        fingerprint: str,
        action: Callable[[], T],
        expected_type: type[T],
    ) -> T:
        existing = self._commands.get(command_id)
        if existing is not None:
            if existing[0] != fingerprint:
                raise ContractViolation(
                    "E_COMMAND_IDEMPOTENCY_CONFLICT", "command_id 已用于不同 payload"
                )
            if not isinstance(existing[1], expected_type):
                raise ContractViolation("E_COMMAND_RESULT_TYPE", "幂等命令结果类型不匹配")
            return existing[1]
        result = action()
        self._commands[command_id] = (fingerprint, result)
        return result

    @staticmethod
    def _fingerprint(*parts: str) -> str:
        payload = "\x1f".join(parts).encode()
        return hashlib.sha256(payload).hexdigest()
