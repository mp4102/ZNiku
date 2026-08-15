"""实现 ExecutionPlan 驱动、可持久恢复的本地真实媒体候选 Runtime。

Runtime 只按 planned subject、Core Operator kind 和注入的精确 Engine binding 分派；它不根据自然语言
或目录推断完成。每次副作用前先持久化 ``running``，成功后只有在媒体 full verification 与
no-replace publication 均通过时才原子记录 Evidence。重启会重新哈希 source 和全部正式 artifact，
并把没有 Evidence 的 ``running`` 一次性尝试恢复为 ``failed``。外部进程退出或残留文件不能伪造完成。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, JsonValue, SkipValidation, field_validator

from zniku.authoring import WorkflowSpec
from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractModel,
    ContractViolation,
    CoverageSpan,
    CoverageUnit,
    EngineManifest,
    MediaKind,
    Scope,
    Sha256Digest,
    StableId,
)
from zniku.validation import publish_file_no_replace
from zniku.workflow import CoreOperatorKind
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    ExecutionPlan,
    NodeRunState,
    PlannedNode,
    PlannedSubjectKind,
    PreflightResult,
    SourceBinding,
    WorkflowBindingSet,
    WorkflowRevision,
    compile_execution_plan,
    freeze_revision,
    preflight_workflow,
)

from .media import (
    DetailedMediaProbe,
    concat_video,
    decode_verify,
    demux_media,
    derive_acceptance_clip,
    encode_hevc_main10,
    extract_chapter,
    hash_audio_stream,
    hash_file,
    make_acceptance_fixture,
    mux_original_audio,
    probe_detailed,
)
from .profile import RealMediaWorkflowBundle, build_real_media_workflow

REAL_RUNTIME_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class MediaFileArtifact(ContractModel):
    """Runtime 工作根内一份已发布媒体的身份、相对位置与 full probe。"""

    artifact_id: StableId
    plan_node_id: StableId
    port_id: StableId
    scope: Scope
    scope_id: StableId
    media_kind: MediaKind
    relative_path: str = Field(min_length=1, max_length=500)
    probe: SkipValidation[DetailedMediaProbe]
    audio_ordinal: int | None = Field(default=None, ge=0, le=31)
    acceptance_fixture: bool = False

    @field_validator("relative_path")
    @classmethod
    def relative_only(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise ValueError("E_REAL_ARTIFACT_PATH: artifact 只能保存规范相对路径")
        return value

    @field_validator("probe", mode="before")
    @classmethod
    def normalize_probe(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return DetailedMediaProbe.from_data(cast(Mapping[str, JsonValue], value))
        return value


class RealStageEvidence(ContractModel):
    """一次 planned node 通过 full verification 后的直接输入/输出证明。"""

    evidence_id: StableId
    plan_node_id: StableId
    attempt: int = Field(ge=1)
    input_artifact_ids: tuple[StableId, ...]
    output_artifact_ids: tuple[StableId, ...]
    verification_mode: Literal["full"] = "full"
    verified: Literal[True] = True


class RealNodeRun(ContractModel):
    plan_node_id: StableId
    state: NodeRunState
    attempt: int = Field(ge=0)
    evidence_id: StableId | None = None


class RealManualHandoff(ContractModel):
    """人工 Engine 一次 attempt 的稳定文件交接；候选位置不代表完成。"""

    handoff_id: StableId
    plan_node_id: StableId
    attempt: int = Field(ge=1)
    engine_manifest_digest: Sha256Digest
    input_artifact_id: StableId
    input_digest: Sha256Digest
    input_relative_path: str
    candidate_relative_path: str
    expected_frame_count: int = Field(ge=1)
    expected_frame_rate: str
    fixture_operation: Literal["enhancement", "frame_interpolation"]

    @field_validator("input_relative_path", "candidate_relative_path")
    @classmethod
    def relative_only(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise ValueError("E_REAL_HANDOFF_PATH: handoff 只能保存规范相对路径")
        return value


class RealFullVerification(ContractModel):
    """唯一 Final 的可解码视频与逐路原始音频 bitstream 证明。"""

    verification_id: StableId
    final_artifact_id: StableId
    final_digest: Sha256Digest
    video_frame_count: int = Field(ge=1)
    video_frame_rate: str
    original_audio_hashes: tuple[Sha256Digest, ...]
    final_audio_hashes: tuple[Sha256Digest, ...]
    evidence_ids: tuple[StableId, ...]
    verified: Literal[True] = True


class RealRuntimeSnapshot(ContractModel):
    """可以脱离 Studio/Agent 恢复的 canonical Runtime authority。"""

    real_runtime_contract_version: Literal["0.1.0"]
    workflow_run_id: StableId
    revision_id: StableId
    execution_plan_digest: Sha256Digest
    source_reference_digest: Sha256Digest
    nodes: tuple[RealNodeRun, ...]
    artifacts: tuple[SkipValidation[MediaFileArtifact], ...]
    evidence: tuple[RealStageEvidence, ...]
    handoffs: tuple[RealManualHandoff, ...] = ()
    final_verification: RealFullVerification | None = None

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(
                MediaFileArtifact.from_data(cast(Mapping[str, JsonValue], item))
                if isinstance(item, Mapping)
                else item
                for item in value
            )
        return value


class RealInputBinding(ContractModel):
    """把一个 planned input port 精确绑定到展开后的 source planned node/port。"""

    plan_node_id: StableId
    input_port_id: StableId
    source_port_id: StableId
    source_plan_node_ids: tuple[StableId, ...]


class RealRunAuthority(ContractModel):
    """冻结候选 Run 所需的所有无副作用 authority。"""

    real_runtime_contract_version: Literal["0.1.0"]
    spec: SkipValidation[WorkflowSpec]
    bindings: SkipValidation[WorkflowBindingSet]
    preflight: SkipValidation[PreflightResult]
    plan: SkipValidation[ExecutionPlan]
    revision: SkipValidation[WorkflowRevision]
    source_artifact: SkipValidation[Artifact]
    chapter_plan: SkipValidation[ChapterPlan]
    reference_probe: SkipValidation[DetailedMediaProbe]
    input_bindings: tuple[RealInputBinding, ...]

    @field_validator(
        "spec",
        "bindings",
        "preflight",
        "plan",
        "revision",
        "source_artifact",
        "chapter_plan",
        "reference_probe",
        mode="before",
    )
    @classmethod
    def normalize_authority(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        types: dict[str, type[ContractModel]] = {
            "spec": WorkflowSpec,
            "bindings": WorkflowBindingSet,
            "preflight": PreflightResult,
            "plan": ExecutionPlan,
            "revision": WorkflowRevision,
            "source_artifact": Artifact,
            "chapter_plan": ChapterPlan,
            "reference_probe": DetailedMediaProbe,
        }
        return types[info.field_name].from_data(cast(Mapping[str, JsonValue], value))


EngineHandler = Callable[[PlannedNode, int], tuple[MediaFileArtifact, ...]]
OperatorHandler = Callable[[PlannedNode, int], tuple[MediaFileArtifact, ...]]


class RealMediaCandidateRuntime:
    """本地单进程真实媒体 Runtime；状态通过原子 JSON snapshot 持久化。"""

    def __init__(
        self,
        *,
        root: Path,
        reference: Path,
        bundle: RealMediaWorkflowBundle,
        authority: RealRunAuthority,
        snapshot: RealRuntimeSnapshot,
    ) -> None:
        self._root = root.resolve(strict=True)
        self._reference = reference.resolve(strict=True)
        self._bundle = bundle
        self._authority = authority
        self._snapshot = snapshot
        self._engine_handlers = self._build_engine_handlers()
        self._operator_handlers: dict[CoreOperatorKind, OperatorHandler] = {
            CoreOperatorKind.PARTITION: self._execute_partition,
            CoreOperatorKind.REDUCE: self._execute_reduce,
        }
        self._validate_authority()
        self._recover_running()
        self._derive_ready()

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        reference: Path,
        workflow_run_id: str = "run.real-media-acceptance.0.1.0",
        clip_start_seconds: int = 28,
        clip_duration_seconds: int = 4,
    ) -> RealMediaCandidateRuntime:
        """创建新候选 Run；工作根必须不存在，参考源只读打开。"""

        if root.exists():
            raise ContractViolation("E_REAL_RUN_ROOT_EXISTS", "新 Run 工作根已存在")
        reference_path = reference.resolve(strict=True)
        root.mkdir(parents=True)
        for name in ("source", "work", "artifacts", "handoffs", "final"):
            (root / name).mkdir()
        try:
            reference_probe = probe_detailed(reference_path)
            clip_path = root / "source" / "acceptance-28s-32s.mkv"
            clip_probe = derive_acceptance_clip(
                reference_path,
                clip_path,
                start_seconds=clip_start_seconds,
                duration_seconds=clip_duration_seconds,
            )
            video = clip_probe.video_streams[0]
            if video.frame_count is None or video.frame_count < 4:
                raise ContractViolation("E_REAL_CLIP_FRAMES", "验收片段帧数不足")
            split = video.frame_count // 2
            chapter_plan = ChapterPlan(
                chapter_plan_id="chapter_plan.real-media-acceptance",
                members=(
                    ChapterMemberBinding(
                        member_id="chapter.001",
                        scope_id="program.real.chapter.001",
                        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=split),
                    ),
                    ChapterMemberBinding(
                        member_id="chapter.002",
                        scope_id="program.real.chapter.002",
                        coverage=CoverageSpan(
                            unit=CoverageUnit.FRAME, start=split, end=video.frame_count
                        ),
                    ),
                ),
                coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=video.frame_count),
            )
            audio_ids = tuple(
                f"audio.stream.{ordinal:03d}"
                for ordinal in range(1, len(clip_probe.audio_streams) + 1)
            )
            source_artifact = Artifact(
                artifact_id="artifact.real-media-acceptance.source",
                artifact_type=ArtifactType.MEDIA,
                media_kind=MediaKind.PROGRAM_MEDIA,
                scope=Scope.PROGRAM,
                scope_id="program.real-media-acceptance",
                attributes={
                    "duration_frames": video.frame_count,
                    "audio_stream_ids": list(audio_ids),
                    "content_digest": clip_probe.content_digest,
                    "frame_rate": video.frame_rate,
                },
            )
            bundle = build_real_media_workflow()
            bindings = WorkflowBindingSet(
                binding_contract_version="0.1.0",
                sources=(
                    SourceBinding(
                        source_node_id="node.source",
                        artifact_id=source_artifact.artifact_id,
                        artifact_digest=source_artifact.sha256_digest(),
                    ),
                ),
                chapter_plan=chapter_plan,
            )
            preflight = preflight_workflow(
                bundle.spec,
                bindings,
                {source_artifact.artifact_id: source_artifact},
                bundle.compiler,
            )
            if not preflight.valid:
                raise ContractViolation("E_REAL_PREFLIGHT", ",".join(preflight.diagnostic_codes))
            plan = compile_execution_plan(bundle.spec, bindings, preflight)
            revision = freeze_revision(bundle.spec, bindings, plan)
            authority = RealRunAuthority(
                real_runtime_contract_version=REAL_RUNTIME_CONTRACT_VERSION,
                spec=bundle.spec,
                bindings=bindings,
                preflight=preflight,
                plan=plan,
                revision=revision,
                source_artifact=source_artifact,
                chapter_plan=chapter_plan,
                reference_probe=reference_probe,
                input_bindings=_build_input_bindings(bundle.spec, plan),
            )
            source_record = MediaFileArtifact(
                artifact_id=source_artifact.artifact_id,
                plan_node_id="plan.node.source",
                port_id="program",
                scope=Scope.PROGRAM,
                scope_id=source_artifact.scope_id,
                media_kind=MediaKind.PROGRAM_MEDIA,
                relative_path="source/acceptance-28s-32s.mkv",
                probe=clip_probe,
            )
            snapshot = RealRuntimeSnapshot(
                real_runtime_contract_version=REAL_RUNTIME_CONTRACT_VERSION,
                workflow_run_id=workflow_run_id,
                revision_id=revision.revision_id,
                execution_plan_digest=plan.sha256_digest(),
                source_reference_digest=reference_probe.content_digest,
                nodes=tuple(
                    RealNodeRun(
                        plan_node_id=node.plan_node_id,
                        state=NodeRunState.BLOCKED,
                        attempt=0,
                    )
                    for node in plan.nodes
                ),
                artifacts=(source_record,),
                evidence=(),
            )
            runtime = cls(
                root=root,
                reference=reference_path,
                bundle=bundle,
                authority=authority,
                snapshot=snapshot,
            )
            runtime._write_model("authority.json", authority)
            runtime._persist()
            return runtime
        except Exception:
            # 工作根是本次调用新建的隔离目录；失败时保留其中事实便于诊断，不删除用户数据。
            raise

    @classmethod
    def open(cls, *, root: Path, reference: Path) -> RealMediaCandidateRuntime:
        """从 canonical authority/snapshot 恢复同一 Run，并执行 fresh 文件核验。"""

        resolved = root.resolve(strict=True)
        authority = RealRunAuthority.from_json((resolved / "authority.json").read_text("utf-8"))
        snapshot = RealRuntimeSnapshot.from_json((resolved / "snapshot.json").read_text("utf-8"))
        return cls(
            root=resolved,
            reference=reference,
            bundle=build_real_media_workflow(),
            authority=authority,
            snapshot=snapshot,
        )

    @property
    def snapshot(self) -> RealRuntimeSnapshot:
        return self._snapshot

    @property
    def authority(self) -> RealRunAuthority:
        return self._authority

    @property
    def root(self) -> Path:
        return self._root

    def ready_nodes(self) -> tuple[str, ...]:
        return tuple(
            item.plan_node_id for item in self._snapshot.nodes if item.state is NodeRunState.READY
        )

    def manifest_for(self, plan_node_id: str) -> EngineManifest | None:
        """按冻结 binding 返回精确 Manifest；operator/source/final 返回 ``None``。"""

        return self._manifest(self._planned(plan_node_id))

    def execute(self, plan_node_id: str, *, inject_failure: bool = False) -> RealRuntimeSnapshot:
        """执行一个 ready 自动节点；失败不产生 Evidence，retry 必须显式调用。"""

        planned = self._planned(plan_node_id)
        manifest = self._manifest(planned)
        if manifest is not None and manifest.execution_mode.value == "manual_external":
            raise ContractViolation("E_REAL_MANUAL_REQUIRED", "人工 Engine 必须走 handoff")
        current = self._record(plan_node_id)
        if current.state is NodeRunState.COMPLETE:
            return self._snapshot
        if current.state is not NodeRunState.READY:
            raise ContractViolation("E_REAL_NODE_NOT_READY", "planned node 当前不可执行")
        attempt = current.attempt + 1
        self._replace_node(
            current.model_copy(update={"state": NodeRunState.RUNNING, "attempt": attempt})
        )
        self._persist()
        try:
            if inject_failure:
                raise ContractViolation("E_REAL_INJECTED_FAILURE", "验收故障注入")
            outputs = self._dispatch(planned, attempt)
            self._complete(planned, attempt, outputs)
        except Exception:
            self._replace_node(
                self._record(plan_node_id).model_copy(update={"state": NodeRunState.FAILED})
            )
            self._persist()
            raise
        return self._snapshot

    def retry(self, plan_node_id: str) -> RealRuntimeSnapshot:
        current = self._record(plan_node_id)
        if current.state is not NodeRunState.FAILED:
            raise ContractViolation("E_REAL_RETRY_FORBIDDEN", "只有 failed node 可以 retry")
        self._replace_node(current.model_copy(update={"state": NodeRunState.READY}))
        self._persist()
        return self._snapshot

    def prepare_manual(self, plan_node_id: str) -> RealManualHandoff:
        """为 ready 人工节点签发幂等 handoff，不改变节点完成状态。"""

        planned = self._planned(plan_node_id)
        manifest = self._manifest(planned)
        if manifest is None or manifest.execution_mode.value != "manual_external":
            raise ContractViolation("E_REAL_HANDOFF_MODE", "节点不是人工 Engine")
        current = self._record(plan_node_id)
        if current.state is not NodeRunState.READY:
            raise ContractViolation("E_REAL_HANDOFF_NOT_READY", "人工节点当前不可 handoff")
        attempt = current.attempt + 1
        existing = next(
            (
                item
                for item in self._snapshot.handoffs
                if item.plan_node_id == plan_node_id and item.attempt == attempt
            ),
            None,
        )
        if existing is not None:
            return existing
        input_artifact = self._manual_input(planned)
        input_video = input_artifact.probe.video_streams[0]
        is_interpolation = planned.stage_spec_id == "node.frame_interpolation"
        expected_count = cast(int, input_video.frame_count) * (2 if is_interpolation else 1)
        rate = Fraction(input_video.frame_rate or "0/0") * (2 if is_interpolation else 1)
        handoff_id = f"handoff.{self._snapshot.workflow_run_id}.{plan_node_id}.{attempt}"
        safe = plan_node_id.replace(".", "-")
        handoff = RealManualHandoff(
            handoff_id=handoff_id,
            plan_node_id=plan_node_id,
            attempt=attempt,
            engine_manifest_digest=manifest.sha256_digest(),
            input_artifact_id=input_artifact.artifact_id,
            input_digest=input_artifact.probe.content_digest,
            input_relative_path=input_artifact.relative_path,
            candidate_relative_path=f"handoffs/{safe}-attempt-{attempt}.candidate.mkv",
            expected_frame_count=expected_count,
            expected_frame_rate=f"{rate.numerator}/{rate.denominator}",
            fixture_operation="frame_interpolation" if is_interpolation else "enhancement",
        )
        self._snapshot = self._snapshot.model_copy(
            update={"handoffs": (*self._snapshot.handoffs, handoff)}
        )
        self._persist()
        return handoff

    def create_acceptance_fixture(self, handoff_id: str) -> RealManualHandoff:
        """仅为本候选生成明确标注的外部处理 fixture；不直接提交或推进状态。"""

        handoff = self._handoff(handoff_id)
        make_acceptance_fixture(
            self._resolve_relative(handoff.input_relative_path),
            self._resolve_relative(handoff.candidate_relative_path, must_exist=False),
            operation=handoff.fixture_operation,
        )
        return handoff

    def submit_manual(self, handoff_id: str) -> RealRuntimeSnapshot:
        """对候选做 fresh full verification 和 no-replace publication 后完成人工节点。"""

        handoff = self._handoff(handoff_id)
        planned = self._planned(handoff.plan_node_id)
        current = self._record(handoff.plan_node_id)
        if current.state is NodeRunState.COMPLETE:
            return self._snapshot
        if current.state is not NodeRunState.READY or current.attempt + 1 != handoff.attempt:
            raise ContractViolation("E_REAL_HANDOFF_STALE", "handoff attempt 已失效")
        input_artifact = self._artifact(handoff.input_artifact_id)
        if input_artifact.probe.content_digest != handoff.input_digest:
            raise ContractViolation("E_REAL_HANDOFF_INPUT_DRIFT", "handoff 输入 authority 漂移")
        self._verify_file(input_artifact)
        candidate_path = self._resolve_relative(handoff.candidate_relative_path)
        decode_verify(candidate_path)
        candidate_probe = probe_detailed(candidate_path)
        video = candidate_probe.video_streams
        actual_rate = Fraction(video[0].frame_rate or "0/0") if len(video) == 1 else Fraction(0)
        expected_rate = Fraction(handoff.expected_frame_rate)
        if (
            len(video) != 1
            or video[0].frame_count != handoff.expected_frame_count
            or abs(float(actual_rate / expected_rate) - 1.0) > 0.00001
            or candidate_probe.audio_streams
        ):
            raise ContractViolation("E_REAL_HANDOFF_VERIFY", "人工候选帧数、帧率或 stream 不符")
        self._replace_node(
            current.model_copy(update={"state": NodeRunState.RUNNING, "attempt": handoff.attempt})
        )
        self._persist()
        safe = planned.plan_node_id.replace(".", "-")
        target_name = f"{safe}-attempt-{handoff.attempt}.mkv"
        try:
            receipt = publish_file_no_replace(candidate_path, self._root / "artifacts", target_name)
            published = self._root / "artifacts" / receipt.target_name
            artifact = self._record_file(
                planned,
                handoff.attempt,
                published,
                MediaKind.VIDEO,
                acceptance_fixture=True,
                port_id="out",
            )
            self._complete(planned, handoff.attempt, (artifact,))
        except Exception:
            self._replace_node(
                self._record(planned.plan_node_id).model_copy(update={"state": NodeRunState.FAILED})
            )
            self._persist()
            raise
        return self._snapshot

    def _dispatch(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        if planned.subject_kind is PlannedSubjectKind.SOURCE:
            return ()
        if planned.subject_kind is PlannedSubjectKind.FINAL:
            return self._execute_final(planned, attempt)
        if planned.subject_kind is PlannedSubjectKind.OPERATOR:
            try:
                return self._operator_handlers[cast(CoreOperatorKind, planned.operator_kind)](
                    planned, attempt
                )
            except KeyError as error:
                raise ContractViolation("E_REAL_OPERATOR_UNAVAILABLE", "operator 未注册") from error
        binding = cast(Any, planned.engine)
        try:
            return self._engine_handlers[binding.manifest_digest](planned, attempt)
        except KeyError as error:
            raise ContractViolation(
                "E_REAL_ENGINE_UNAVAILABLE", "精确 Engine adapter 未注册"
            ) from error

    def _build_engine_handlers(self) -> dict[str, EngineHandler]:
        by_id = {manifest.engine_id: manifest for manifest in self._bundle.manifests}
        return {
            by_id["zniku.builtin.ffmpeg-demux"].sha256_digest(): self._execute_demux,
            by_id["zniku.builtin.ffmpeg-hevc-main10"].sha256_digest(): self._execute_encode,
            by_id["zniku.builtin.ffmpeg-mux"].sha256_digest(): self._execute_mux,
        }

    def _execute_demux(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        source = self._artifact("artifact.real-media-acceptance.source")
        work = self._attempt_dir(planned, attempt)
        video_candidate = work / "video.mkv"
        audio_candidates = tuple(
            work / f"audio-{ordinal:03d}.mka"
            for ordinal in range(1, len(source.probe.audio_streams) + 1)
        )
        demux_media(self._file(source), video_candidate, audio_candidates)
        candidates = (
            (video_candidate, MediaKind.VIDEO, None, "video_out"),
            *(
                (path, MediaKind.AUDIO, ordinal, "audio_out")
                for ordinal, path in enumerate(audio_candidates)
            ),
        )
        return self._publish_outputs(planned, attempt, candidates)

    def _execute_partition(
        self, planned: PlannedNode, attempt: int
    ) -> tuple[MediaFileArtifact, ...]:
        video = self._first_dependency(planned, MediaKind.VIDEO)
        work = self._attempt_dir(planned, attempt)
        candidates: list[tuple[Path, MediaKind, int | None, str]] = []
        for index, member in enumerate(self._authority.chapter_plan.members, start=1):
            target = work / f"chapter-{index:03d}.mkv"
            extract_chapter(
                self._file(video),
                target,
                start_frame=member.coverage.start,
                end_frame=member.coverage.end,
            )
            candidates.append((target, MediaKind.VIDEO, None, "out"))
        return self._publish_outputs(planned, attempt, tuple(candidates), chapter_scopes=True)

    def _execute_reduce(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        inputs = self._dependency_artifacts(planned)
        ordered = tuple(
            next(item for item in inputs if item.scope_id == member.scope_id)
            for member in self._authority.chapter_plan.members
        )
        work = self._attempt_dir(planned, attempt)
        candidate = work / "reduced.mkv"
        concat_video(tuple(self._file(item) for item in ordered), candidate)
        return self._publish_outputs(planned, attempt, ((candidate, MediaKind.VIDEO, None, "out"),))

    def _execute_encode(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        source = self._first_dependency(planned, MediaKind.VIDEO)
        candidate = self._attempt_dir(planned, attempt) / "encoded.mkv"
        encode_hevc_main10(self._file(source), candidate, crf=cast(int, planned.parameters["crf"]))
        outputs = self._publish_outputs(
            planned, attempt, ((candidate, MediaKind.VIDEO, None, "video_out"),)
        )
        video = outputs[0].probe.video_streams[0]
        if video.codec_name != "hevc" or video.pixel_format != "yuv420p10le":
            raise ContractViolation("E_REAL_ENCODE_CONTRACT", "Encode 未产生 HEVC Main10 输出")
        return outputs

    def _execute_mux(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        dependencies = self._dependency_artifacts(planned)
        video = next(item for item in dependencies if item.media_kind is MediaKind.VIDEO)
        audio = tuple(
            sorted(
                (item for item in dependencies if item.media_kind is MediaKind.AUDIO),
                key=lambda item: cast(int, item.audio_ordinal),
            )
        )
        candidate = self._attempt_dir(planned, attempt) / "muxed.mkv"
        mux_original_audio(self._file(video), tuple(self._file(item) for item in audio), candidate)
        decode_verify(candidate)
        for ordinal, source in enumerate(audio):
            if hash_audio_stream(self._file(source), 0) != hash_audio_stream(candidate, ordinal):
                raise ContractViolation("E_REAL_AUDIO_DRIFT", "Mux 后原始音频 bitstream 不一致")
        return self._publish_outputs(
            planned, attempt, ((candidate, MediaKind.PROGRAM_MEDIA, None, "program_out"),)
        )

    def _execute_final(self, planned: PlannedNode, attempt: int) -> tuple[MediaFileArtifact, ...]:
        source = self._first_dependency(planned, MediaKind.PROGRAM_MEDIA)
        receipt = publish_file_no_replace(
            self._file(source), self._root / "final", "ZNIKU-real-media-acceptance-final.mkv"
        )
        final_path = self._root / "final" / receipt.target_name
        decode_verify(final_path)
        artifact = self._record_file(
            planned,
            attempt,
            final_path,
            MediaKind.PROGRAM_MEDIA,
            artifact_suffix="final",
            port_id="program",
        )
        return (artifact,)

    def _publish_outputs(
        self,
        planned: PlannedNode,
        attempt: int,
        candidates: tuple[tuple[Path, MediaKind, int | None, str], ...],
        *,
        chapter_scopes: bool = False,
    ) -> tuple[MediaFileArtifact, ...]:
        outputs: list[MediaFileArtifact] = []
        for index, (candidate, kind, audio_ordinal, port_id) in enumerate(candidates, start=1):
            extension = candidate.suffix
            safe = planned.plan_node_id.replace(".", "-")
            target_name = f"{safe}-attempt-{attempt}-{index:03d}{extension}"
            receipt = publish_file_no_replace(candidate, self._root / "artifacts", target_name)
            scope_id = None
            if chapter_scopes:
                scope_id = self._authority.chapter_plan.members[index - 1].scope_id
            outputs.append(
                self._record_file(
                    planned,
                    attempt,
                    self._root / "artifacts" / receipt.target_name,
                    kind,
                    audio_ordinal=audio_ordinal,
                    port_id=port_id,
                    artifact_suffix=f"{index:03d}",
                    scope_id=scope_id,
                )
            )
        return tuple(outputs)

    def _record_file(
        self,
        planned: PlannedNode,
        attempt: int,
        path: Path,
        media_kind: MediaKind,
        *,
        audio_ordinal: int | None = None,
        acceptance_fixture: bool = False,
        artifact_suffix: str = "001",
        scope_id: str | None = None,
        port_id: str,
    ) -> MediaFileArtifact:
        probe = probe_detailed(path)
        expected_streams = (
            len(probe.video_streams) == 1 and not probe.audio_streams
            if media_kind is MediaKind.VIDEO
            else len(probe.audio_streams) == 1 and not probe.video_streams
            if media_kind is MediaKind.AUDIO
            else len(probe.video_streams) == 1 and bool(probe.audio_streams)
        )
        if not expected_streams:
            raise ContractViolation("E_REAL_OUTPUT_STREAMS", "输出 stream 形状不满足媒体合同")
        relative = path.relative_to(self._root).as_posix()
        return MediaFileArtifact(
            artifact_id=(
                f"artifact.{self._snapshot.workflow_run_id}.{planned.plan_node_id}."
                f"{attempt}.{artifact_suffix}"
            ),
            plan_node_id=planned.plan_node_id,
            port_id=port_id,
            scope=planned.scope,
            scope_id=scope_id or planned.scope_id,
            media_kind=media_kind,
            relative_path=relative,
            probe=probe,
            audio_ordinal=audio_ordinal,
            acceptance_fixture=acceptance_fixture,
        )

    def _complete(
        self, planned: PlannedNode, attempt: int, outputs: tuple[MediaFileArtifact, ...]
    ) -> None:
        inputs = self._dependency_artifacts(planned)
        evidence_id = f"evidence.{self._snapshot.workflow_run_id}.{planned.plan_node_id}.{attempt}"
        evidence = RealStageEvidence(
            evidence_id=evidence_id,
            plan_node_id=planned.plan_node_id,
            attempt=attempt,
            input_artifact_ids=tuple(item.artifact_id for item in inputs),
            output_artifact_ids=tuple(item.artifact_id for item in outputs),
            verified=True,
        )
        self._snapshot = self._snapshot.model_copy(
            update={
                "artifacts": (*self._snapshot.artifacts, *outputs),
                "evidence": (*self._snapshot.evidence, evidence),
            }
        )
        self._replace_node(
            self._record(planned.plan_node_id).model_copy(
                update={"state": NodeRunState.COMPLETE, "evidence_id": evidence_id}
            )
        )
        if planned.subject_kind is PlannedSubjectKind.FINAL:
            self._snapshot = self._snapshot.model_copy(
                update={"final_verification": self._verify_final(outputs[0], evidence_id)}
            )
        self._derive_ready()
        self._persist()

    def _verify_final(
        self, final: MediaFileArtifact, final_evidence_id: str
    ) -> RealFullVerification:
        if not all(item.state is NodeRunState.COMPLETE for item in self._snapshot.nodes):
            raise ContractViolation("E_REAL_FINAL_INCOMPLETE", "Final 前节点未全部 complete")
        original_audio = tuple(
            sorted(
                (
                    item
                    for item in self._snapshot.artifacts
                    if item.plan_node_id == "plan.node.demux" and item.media_kind is MediaKind.AUDIO
                ),
                key=lambda item: cast(int, item.audio_ordinal),
            )
        )
        original_hashes = tuple(hash_audio_stream(self._file(item), 0) for item in original_audio)
        final_hashes = tuple(
            hash_audio_stream(self._file(final), ordinal) for ordinal in range(len(original_audio))
        )
        if not original_hashes or original_hashes != final_hashes:
            raise ContractViolation("E_REAL_FINAL_AUDIO", "Final 原始音频证明不闭合")
        video = final.probe.video_streams[0]
        return RealFullVerification(
            verification_id=f"verification.{self._snapshot.workflow_run_id}.final",
            final_artifact_id=final.artifact_id,
            final_digest=final.probe.content_digest,
            video_frame_count=cast(int, video.frame_count),
            video_frame_rate=cast(str, video.frame_rate),
            original_audio_hashes=original_hashes,
            final_audio_hashes=final_hashes,
            evidence_ids=(
                *(item.evidence_id for item in self._snapshot.evidence),
                final_evidence_id,
            ),
            verified=True,
        )

    def _manual_input(self, planned: PlannedNode) -> MediaFileArtifact:
        candidates = tuple(
            item
            for item in self._dependency_artifacts(planned)
            if item.media_kind is MediaKind.VIDEO and item.scope_id == planned.scope_id
        )
        if len(candidates) != 1:
            raise ContractViolation("E_REAL_HANDOFF_INPUT", "人工节点直接输入不唯一")
        return candidates[0]

    def _dependency_artifacts(self, planned: PlannedNode) -> tuple[MediaFileArtifact, ...]:
        bindings = tuple(
            item
            for item in self._authority.input_bindings
            if item.plan_node_id == planned.plan_node_id
        )
        selected: list[MediaFileArtifact] = []
        for binding in bindings:
            candidates = tuple(
                item
                for item in self._snapshot.artifacts
                if item.plan_node_id in binding.source_plan_node_ids
                and item.port_id == binding.source_port_id
            )
            if planned.scope is Scope.CHAPTER:
                scoped = tuple(item for item in candidates if item.scope_id == planned.scope_id)
                if scoped:
                    candidates = scoped
            selected.extend(candidates)
        identities = tuple(item.artifact_id for item in selected)
        if len(identities) != len(set(identities)):
            raise ContractViolation(
                "E_REAL_INPUT_BINDING_DUPLICATE", "直接输入绑定产生重复 Artifact"
            )
        return tuple(selected)

    def _first_dependency(self, planned: PlannedNode, kind: MediaKind) -> MediaFileArtifact:
        candidates = tuple(
            item for item in self._dependency_artifacts(planned) if item.media_kind is kind
        )
        if len(candidates) != 1:
            raise ContractViolation("E_REAL_INPUT_CARDINALITY", "节点直接输入不唯一")
        return candidates[0]

    def _manifest(self, planned: PlannedNode) -> EngineManifest | None:
        if planned.engine is None:
            return None
        matches = tuple(
            manifest
            for manifest in self._bundle.manifests
            if manifest.engine_id == planned.engine.engine_id
            and manifest.engine_version == planned.engine.engine_version
            and manifest.sha256_digest() == planned.engine.manifest_digest
        )
        if len(matches) != 1:
            raise ContractViolation("E_REAL_MANIFEST_AUTHORITY", "Plan Engine binding 无法精确解析")
        return matches[0]

    def _attempt_dir(self, planned: PlannedNode, attempt: int) -> Path:
        path = self._root / "work" / f"{planned.plan_node_id.replace('.', '-')}-{attempt}"
        path.mkdir(exist_ok=True)
        return path

    def _planned(self, plan_node_id: str) -> PlannedNode:
        try:
            return next(
                item for item in self._authority.plan.nodes if item.plan_node_id == plan_node_id
            )
        except StopIteration as error:
            raise ContractViolation("E_REAL_NODE_UNKNOWN", "未知 planned node") from error

    def _record(self, plan_node_id: str) -> RealNodeRun:
        try:
            return next(item for item in self._snapshot.nodes if item.plan_node_id == plan_node_id)
        except StopIteration as error:
            raise ContractViolation("E_REAL_NODE_UNKNOWN", "snapshot 缺少 planned node") from error

    def _replace_node(self, replacement: RealNodeRun) -> None:
        self._snapshot = self._snapshot.model_copy(
            update={
                "nodes": tuple(
                    replacement if item.plan_node_id == replacement.plan_node_id else item
                    for item in self._snapshot.nodes
                )
            }
        )

    def _artifact(self, artifact_id: str) -> MediaFileArtifact:
        try:
            return next(
                item for item in self._snapshot.artifacts if item.artifact_id == artifact_id
            )
        except StopIteration as error:
            raise ContractViolation("E_REAL_ARTIFACT_UNKNOWN", "未知正式 Artifact") from error

    def _handoff(self, handoff_id: str) -> RealManualHandoff:
        try:
            return next(item for item in self._snapshot.handoffs if item.handoff_id == handoff_id)
        except StopIteration as error:
            raise ContractViolation("E_REAL_HANDOFF_UNKNOWN", "未知 handoff") from error

    def _file(self, artifact: MediaFileArtifact) -> Path:
        return self._resolve_relative(artifact.relative_path)

    def _resolve_relative(self, value: str, *, must_exist: bool = True) -> Path:
        candidate = (self._root / Path(value)).resolve(strict=must_exist)
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ContractViolation("E_REAL_PATH_ESCAPE", "相对路径逃逸工作根") from error
        return candidate

    def _derive_ready(self) -> None:
        completed = {
            item.plan_node_id
            for item in self._snapshot.nodes
            if item.state is NodeRunState.COMPLETE
        }
        for planned in self._authority.plan.nodes:
            record = self._record(planned.plan_node_id)
            if record.state in {
                NodeRunState.COMPLETE,
                NodeRunState.FAILED,
                NodeRunState.RUNNING,
            }:
                continue
            state = (
                NodeRunState.READY
                if set(planned.dependencies) <= completed
                else NodeRunState.BLOCKED
            )
            self._replace_node(record.model_copy(update={"state": state}))

    def _recover_running(self) -> None:
        changed = False
        for record in self._snapshot.nodes:
            if record.state is NodeRunState.RUNNING:
                self._replace_node(record.model_copy(update={"state": NodeRunState.FAILED}))
                changed = True
        if changed:
            self._persist()

    def _validate_authority(self) -> None:
        if (
            self._authority.revision.execution_plan_digest != self._authority.plan.sha256_digest()
            or self._snapshot.execution_plan_digest != self._authority.plan.sha256_digest()
            or self._snapshot.revision_id != self._authority.revision.revision_id
            or self._bundle.spec.sha256_digest() != self._authority.spec.sha256_digest()
        ):
            raise ContractViolation("E_REAL_AUTHORITY_MISMATCH", "冻结 authority digest 不闭合")
        _, reference_digest = hash_file(self._reference)
        if reference_digest != self._snapshot.source_reference_digest:
            raise ContractViolation("E_REAL_REFERENCE_DRIFT", "参考源 digest 已改变")
        ids = tuple(item.artifact_id for item in self._snapshot.artifacts)
        if len(ids) != len(set(ids)):
            raise ContractViolation("E_REAL_ARTIFACT_DUPLICATE", "snapshot Artifact identity 重复")
        for artifact in self._snapshot.artifacts:
            self._verify_file(artifact)

    def _verify_file(self, artifact: MediaFileArtifact) -> None:
        path = self._resolve_relative(artifact.relative_path)
        size, digest = hash_file(path)
        if size != artifact.probe.size_bytes or digest != artifact.probe.content_digest:
            raise ContractViolation("E_REAL_ARTIFACT_DRIFT", "正式 Artifact size/digest 漂移")

    def _write_model(self, name: str, model: ContractModel) -> None:
        target = self._root / name
        temporary = self._root / f".{name}.tmp"
        data = model.to_canonical_bytes()
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def _persist(self) -> None:
        self._write_model("snapshot.json", self._snapshot)


def _build_input_bindings(spec: WorkflowSpec, plan: ExecutionPlan) -> tuple[RealInputBinding, ...]:
    """从正式 Spec edge 和 chapter 展开结果冻结 planned port binding。"""

    result: list[RealInputBinding] = []
    for target in plan.nodes:
        incoming = tuple(edge for edge in spec.edges if edge.target.node_id == target.stage_spec_id)
        for edge in incoming:
            sources = tuple(
                item for item in plan.nodes if item.stage_spec_id == edge.source.node_id
            )
            if target.scope is Scope.CHAPTER:
                scoped = tuple(item for item in sources if item.scope_id == target.scope_id)
                if scoped:
                    sources = scoped
            if not sources:
                raise ContractViolation(
                    "E_REAL_INPUT_BINDING_EMPTY", "planned port 缺少展开 source"
                )
            source_ids = tuple(item.plan_node_id for item in sources)
            if not set(source_ids) <= set(target.dependencies):
                raise ContractViolation(
                    "E_REAL_INPUT_BINDING_DEPENDENCY", "port binding 越过 Plan dependency"
                )
            result.append(
                RealInputBinding(
                    plan_node_id=target.plan_node_id,
                    input_port_id=edge.target.port_id,
                    source_port_id=edge.source.port_id,
                    source_plan_node_ids=source_ids,
                )
            )
    return tuple(result)
