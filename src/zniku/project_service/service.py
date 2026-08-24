"""实现单用户本地 Studio 的 Project Service application facade。

Facade 在一次 Project session 内只构造一个 ``RuntimeService``，因此应用重启时的 interrupted 恢复只
发生一次。运行命令在受控后台线程中推进，HTTP GET 可以同时从 SQLite authority 读取进度；这只是 host
操作状态，不增加领域状态机、队列、checkpoint 或 resume。所有 mutation 串行，忙碌时失败关闭。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from time import monotonic
from typing import Final

from pydantic import ValidationError

from zniku.graph import NodeDefinition
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.runtime import (
    Artifact,
    ArtifactQuickProbe,
    NodeRun,
    PythonAdapter,
    Run,
    RunState,
    RuntimeConflictError,
    RuntimeNotFoundError,
    RuntimeRepositoryError,
    RuntimeService,
    RuntimeServiceError,
)
from zniku.runtime.runner import MediaProbe, NodeValidator

from .models import (
    ActiveProjectOperation,
    CreateProjectCommand,
    NodeLogProjection,
    OpenProjectCommand,
    ProjectServiceEnvelope,
    ProjectServiceFailure,
    RerunFromHereCommand,
    RunAllCommand,
    RunToCommand,
    SaveProjectCommand,
    SubmitExternalCommand,
    parse_project_service_command,
)

_LOG_TAIL_LIMIT: Final = 128 * 1024


class ProjectServiceError(RuntimeError):
    """表示 Project Service 可安全返回给本地 Studio 的稳定失败。"""

    def __init__(self, code: str, message: str, *, http_status: int = 422) -> None:
        self.code = code
        self.message = message[:4096] or "Project Service 失败"
        self.http_status = http_status
        super().__init__(f"{code}: {self.message}")


class ProjectServiceApplication:
    """持有一个当前 Project session，并把 Studio intent 委托给正式领域服务。"""

    def __init__(
        self,
        *,
        work_root: str | Path,
        definition_catalog: Iterable[NodeDefinition] = (),
        python_adapters: Mapping[str, PythonAdapter] | None = None,
        validators: Mapping[str, NodeValidator] | None = None,
        media_probe: MediaProbe | None = None,
        artifact_quick_probe: ArtifactQuickProbe | None = None,
    ) -> None:
        root = Path(work_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._work_root = root.resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_WORK_ROOT", str(error), http_status=400
            ) from error
        if not self._work_root.is_dir():
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_WORK_ROOT", "work_root 必须是目录", http_status=400
            )

        catalog = tuple(definition_catalog)
        if any(not isinstance(item, NodeDefinition) for item in catalog):
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_DEFINITION_CATALOG",
                "definition_catalog 只能包含 NodeDefinition",
                http_status=500,
            )
        keys = tuple((item.type_id, item.version) for item in catalog)
        if len(keys) != len(set(keys)):
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_DEFINITION_CATALOG",
                "definition_catalog 不得重复 type_id/version",
                http_status=500,
            )

        self._definition_catalog = catalog
        self._python_adapters = dict(python_adapters or {})
        self._validators = dict(validators or {})
        self._media_probe = media_probe
        self._artifact_quick_probe = artifact_quick_probe
        self._state = threading.Condition(threading.RLock())
        self._store: ProjectStore | None = None
        self._runtime: RuntimeService | None = None
        self._active_run_id: str | None = None
        self._active_operation: ActiveProjectOperation | None = None
        self._worker: threading.Thread | None = None
        self._last_error: ProjectServiceFailure | None = None

    @property
    def work_root(self) -> Path:
        """返回进程启动时固定的 attempt 根；command 不能替换它。"""

        return self._work_root

    def inspect(self) -> ProjectServiceEnvelope:
        """从当前 ``.zniku`` 重新组装 Project/Runtime 读模型。"""

        with self._state:
            store = self._store
            runtime = self._runtime
            active_run_id = self._active_run_id
            active_operation = self._active_operation
            error = self._last_error

        if store is None or runtime is None:
            return ProjectServiceEnvelope(error=error)

        try:
            snapshot = store.load()
            repository = runtime.repository
            runs = repository.list_runs()
            run_ids = {run.run_id for run in runs}
            if active_run_id not in run_ids:
                active_run_id = runs[-1].run_id if runs else None
            latest = repository.list_latest()
            artifacts = self._collect_artifacts(runs, runtime)
            logs = tuple(
                self._project_logs(node_run)
                for run in runs
                for node_run in run.node_runs
                if node_run.log_path is not None
            )
            return ProjectServiceEnvelope(
                project_path=str(store.path),
                snapshot=snapshot,
                runs=runs,
                active_run_id=active_run_id,
                active_operation=active_operation,
                latest_results=latest,
                artifacts=artifacts,
                logs=logs,
                error=error,
            )
        except (ProjectStoreError, RuntimeRepositoryError, ValidationError) as failure:
            raise self._translate_failure(failure) from failure

    def command(self, payload: object) -> ProjectServiceEnvelope:
        """严格解析并执行一个 Studio command；返回 fresh envelope。"""

        try:
            command = parse_project_service_command(payload)
        except ValidationError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_COMMAND_INVALID", str(error), http_status=422
            ) from error

        with self._state:
            self._assert_idle()
            self._last_error = None
            try:
                if isinstance(command, OpenProjectCommand):
                    self._open(command)
                elif isinstance(command, CreateProjectCommand):
                    self._create(command)
                elif isinstance(command, SaveProjectCommand):
                    self._save(command)
                elif isinstance(command, RunAllCommand | RunToCommand):
                    self._start_run(command)
                elif isinstance(command, RerunFromHereCommand):
                    self._rerun(command)
                elif isinstance(command, SubmitExternalCommand):
                    self._submit_external(command)
                else:  # pragma: no cover - discriminated union 的防御性封闭分支
                    raise ProjectServiceError(
                        "E_PROJECT_SERVICE_OPERATION", "未知 operation", http_status=422
                    )
            except (ProjectStoreError, RuntimeRepositoryError, RuntimeServiceError) as failure:
                translated = self._translate_failure(failure)
                self._last_error = ProjectServiceFailure(
                    code=translated.code, message=translated.message
                )
                raise translated from failure
        return self.inspect()

    def wait_until_idle(self, *, timeout: float = 10.0) -> bool:
        """供测试与受控 launcher 等待后台 command；产品 UI 应轮询 ``inspect``。"""

        deadline = monotonic() + timeout
        with self._state:
            while self._active_operation is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._state.wait(remaining)
            return True

    def _open(self, command: OpenProjectCommand) -> None:
        path = self._resolve_open_path(command.path)
        store = ProjectStore.open(path)
        runtime = self._runtime_for(store)
        runs = runtime.repository.list_runs()
        self._store = store
        self._runtime = runtime
        self._active_run_id = runs[-1].run_id if runs else None

    def _create(self, command: CreateProjectCommand) -> None:
        path = self._resolve_create_path(command.path)
        try:
            project = Project.model_validate(
                {
                    "project_id": command.project_id,
                    "name": command.name,
                    "graph": {"nodes": [], "edges": []},
                },
                strict=True,
            )
        except ValidationError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PROJECT_INVALID", str(error), http_status=422
            ) from error
        store = ProjectStore.create(path, project, self._definition_catalog)
        self._store = store
        self._runtime = self._runtime_for(store)
        self._active_run_id = None

    def _save(self, command: SaveProjectCommand) -> None:
        store, _ = self._require_session()
        snapshot = store.load()
        store.save(command.project, snapshot.definitions)

    def _start_run(self, command: RunAllCommand | RunToCommand) -> None:
        _, runtime = self._require_session()
        selected = () if isinstance(command, RunAllCommand) else (command.node_id,)
        run = runtime.create_run(selected_targets=selected)
        operation: ActiveProjectOperation = (
            "run_all" if isinstance(command, RunAllCommand) else "run_to"
        )
        self._begin_worker(operation, run.run_id, lambda: runtime.run_until_blocked(run.run_id))

    def _rerun(self, command: RerunFromHereCommand) -> None:
        store, runtime = self._require_session()
        run = runtime.repository.get_run(command.run_id)
        if command.node_id not in {item.node_id for item in run.node_runs}:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_RERUN_NODE_OUTSIDE_RUN",
                f"节点 {command.node_id!r} 不属于引用 Run 的执行闭包",
                http_status=409,
            )
        current = store.load()
        snapshot_still_current = (
            run.graph_snapshot == current.project.graph
            and run.definitions_snapshot == current.definitions
        )
        if run.state is RunState.RUNNING and snapshot_still_current:
            run_id = run.run_id
            self._begin_worker(
                "rerun_from_here",
                run_id,
                lambda: runtime.rerun_from_start(run_id, command.node_id),
            )
            return
        replacement = runtime.create_rerun_run(command.node_id)
        run_id = replacement.run_id
        self._begin_worker("rerun_from_here", run_id, lambda: runtime.run_until_blocked(run_id))

    def _submit_external(self, command: SubmitExternalCommand) -> None:
        _, runtime = self._require_session()
        node_run = runtime.repository.get_node_run(command.node_run_id)
        self._begin_worker(
            "submit_external",
            node_run.run_id,
            lambda: runtime.submit_external(command.node_run_id),
        )

    def _begin_worker(
        self,
        operation: ActiveProjectOperation,
        run_id: str,
        work: Callable[[], object],
    ) -> None:
        self._active_run_id = run_id
        self._active_operation = operation

        def target() -> None:
            failure: ProjectServiceFailure | None = None
            try:
                work()
            except (ProjectStoreError, RuntimeRepositoryError, RuntimeServiceError) as error:
                translated = self._translate_failure(error)
                failure = ProjectServiceFailure(code=translated.code, message=translated.message)
            except Exception as error:
                failure = ProjectServiceFailure(
                    code="E_PROJECT_SERVICE_BACKGROUND",
                    message=(str(error) or type(error).__name__)[:4096],
                )
            finally:
                with self._state:
                    self._last_error = failure
                    self._active_operation = None
                    self._worker = None
                    self._state.notify_all()

        worker = threading.Thread(
            target=target,
            name=f"zniku-{operation}-{run_id[:8]}",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _runtime_for(self, store: ProjectStore) -> RuntimeService:
        return RuntimeService(
            store,
            self._work_root,
            python_adapters=self._python_adapters,
            validators=self._validators,
            media_probe=self._media_probe,
            artifact_quick_probe=self._artifact_quick_probe,
        )

    def _require_session(self) -> tuple[ProjectStore, RuntimeService]:
        if self._store is None or self._runtime is None:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_NO_PROJECT", "尚未打开 .zniku Project", http_status=409
            )
        return self._store, self._runtime

    def _assert_idle(self) -> None:
        if self._active_operation is not None:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_BUSY",
                f"Project Service 正在执行 {self._active_operation}",
                http_status=409,
            )

    @staticmethod
    def _resolve_open_path(value: str) -> Path:
        if "\x00" in value:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", "Project 路径不得包含 NUL", http_status=400
            )
        try:
            return Path(value).resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", str(error), http_status=400
            ) from error

    @staticmethod
    def _resolve_create_path(value: str) -> Path:
        if "\x00" in value:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", "Project 路径不得包含 NUL", http_status=400
            )
        raw = Path(value)
        try:
            parent = raw.parent.resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", str(error), http_status=400
            ) from error
        return parent / raw.name

    def _project_logs(self, node_run: NodeRun) -> NodeLogProjection:
        stdout, stdout_truncated, stdout_available = self._read_log(node_run, "stdout.log")
        stderr, stderr_truncated, stderr_available = self._read_log(node_run, "stderr.log")
        return NodeLogProjection(
            node_run_id=node_run.node_run_id,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_available=stdout_available,
            stderr_available=stderr_available,
        )

    def _read_log(self, node_run: NodeRun, filename: str) -> tuple[str, bool, bool]:
        try:
            root = self._work_root.resolve(strict=True)
            work_dir = Path(node_run.work_dir).resolve(strict=True)
            work_dir.relative_to(root)
            target = (work_dir / "logs" / filename).resolve(strict=True)
            target.relative_to(work_dir)
            if not target.is_file():
                return "", False, False
            size = target.stat().st_size
            with target.open("rb") as stream:
                if size > _LOG_TAIL_LIMIT:
                    stream.seek(-_LOG_TAIL_LIMIT, 2)
                payload = stream.read(_LOG_TAIL_LIMIT)
            return payload.decode("utf-8", errors="replace"), size > _LOG_TAIL_LIMIT, True
        except (OSError, ValueError):
            return "", False, False

    @staticmethod
    def _collect_artifacts(runs: tuple[Run, ...], runtime: RuntimeService) -> tuple[Artifact, ...]:
        collected: list[Artifact] = []
        seen: set[str] = set()
        for run in runs:
            for node_run in run.node_runs:
                for artifact_id in node_run.output_artifact_ids:
                    if artifact_id in seen:
                        continue
                    collected.append(runtime.repository.get_artifact(artifact_id))
                    seen.add(artifact_id)
        return tuple(collected)

    @staticmethod
    def _translate_failure(error: Exception) -> ProjectServiceError:
        code = getattr(error, "code", "E_PROJECT_SERVICE_FAILURE")
        if isinstance(error, RuntimeNotFoundError):
            status = 404
        elif isinstance(error, RuntimeConflictError | RuntimeServiceError):
            status = 409
        elif isinstance(error, ProjectStoreError):
            status = 422
        else:
            status = 500
        return ProjectServiceError(str(code), str(error), http_status=status)


__all__ = ["ProjectServiceApplication", "ProjectServiceError"]
