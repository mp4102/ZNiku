"""把工程数据维护接到严格宿主 API，不允许浏览器直接指定待搬运路径。

目标来自本次原生目录选择，或明确恢复工程旁默认位置。已运行工程必须走短期预览与确认；
维护互斥期间不启动节点、不修改 Graph；删除仅限服务器预览绑定的内部中转，不能接收路径。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator

from zniku.project.storage import ProjectStorage, new_project_storage

from .host_bridge import (
    HostBridgeFailure,
    HostBridgeModel,
    HostBridgeSession,
    HostOpaqueId,
    HostRandomId,
    PickerSelectionReference,
)
from .storage import (
    StorageInspection,
    StorageMigrationManager,
    StorageMigrationPreview,
    inspect_storage,
)
from .storage_index import StorageIndexResult, export_storage_index
from .storage_paths import prepare_storage_location
from .storage_scratch import ScratchConfirmResult, ScratchManager, ScratchPreview

if TYPE_CHECKING:
    from zniku.project import ProjectStore

    from .service import ProjectServiceApplication


class StorageInspectRequest(HostBridgeModel):
    """只读检查必须绑定当前工程会话。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    project_session_id: HostRandomId


class StorageLocationRequest(StorageInspectRequest):
    """null 明确表示工程旁默认；非空句柄表示用户刚选择的数据父目录。"""

    expected_storage_revision: Annotated[int, Field(ge=0, le=9007199254740991)]
    selection_handle: HostOpaqueId | None


class StorageIndexRequest(StorageInspectRequest):
    """生成派生 HTML 时绑定 CAS；不改变图、运行或复用结果。"""

    expected_storage_revision: Annotated[int, Field(ge=0, le=9007199254740991)]


class StorageMigrationConfirmRequest(StorageInspectRequest):
    """确认不能修改预览已绑定的源位置、目标或工程。"""

    expected_storage_revision: Annotated[int, Field(ge=0, le=9007199254740991)]
    ticket_id: HostRandomId


class ScratchPreviewRequest(StorageIndexRequest):
    """显式按需统计，绑定当前工程版本，不在普通状态轮询中扫描。"""


class ScratchConfirmRequest(StorageIndexRequest):
    """一次性预览内的候选身份与不可恢复确认；没有任何 raw path 字段。"""

    ticket_id: HostRandomId
    candidate_ids: Annotated[tuple[HostRandomId, ...], Field(min_length=1, max_length=4096)]
    confirm_irreversible: Literal[True]

    @field_validator("candidate_ids", mode="before")
    @classmethod
    def normalize_ids(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("confirm_irreversible", mode="before")
    @classmethod
    def require_explicit_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("清理必须明确确认不可恢复")
        return value


def _target(
    store: ProjectStore,
    request: StorageLocationRequest,
    session: HostBridgeSession,
    *,
    existing: bool = False,
) -> ProjectStorage:
    current = store.load_storage()
    root = None
    if existing and request.selection_handle is None:
        raise HostBridgeFailure(
            "E_PROJECT_STORAGE_TARGET", "请选择已复制的 .data 目录本体", http_status=422
        )
    if request.selection_handle is not None:
        parent = session.resolve_path_reference(
            PickerSelectionReference(
                kind="picker_selection", selection_handle=request.selection_handle
            )
        )
        if not parent.is_dir():
            raise HostBridgeFailure("E_PROJECT_STORAGE_TARGET", "请选择数据父目录", http_status=422)
        root = parent if existing else parent / store.path.with_suffix(".data").name
    target = new_project_storage(
        store.path,
        data_root=root,
        media_basename=None if current is None else current.media_basename,
    )
    if current is not None and (
        current.data_id is not None or existing or target.data_root == current.data_root
    ):
        payload = target.model_dump(mode="python")
        payload["data_id"] = current.data_id
        target = ProjectStorage.model_validate(payload)
    return target


class StorageApi:
    """固定路由适配；大文件复制只由显式 confirm 调用一次，不自动重试。"""

    def __init__(self) -> None:
        self._migration = StorageMigrationManager()
        self._scratch = ScratchManager()

    def invoke(
        self,
        action: str,
        payload: object,
        *,
        session: HostBridgeSession,
        application: ProjectServiceApplication,
    ) -> (
        StorageInspection
        | StorageMigrationPreview
        | StorageIndexResult
        | ScratchPreview
        | ScratchConfirmResult
    ):
        """同一 facade 互斥保护路径切换，并把严格模型错误转为稳定用户失败。"""

        try:
            if action == "scratch-preview":
                scan = ScratchPreviewRequest.model_validate(payload, strict=True)
                with application.storage_authority(
                    scan.project_session_id, maintenance="scan_storage"
                ) as (store, legacy):
                    return self._scratch.preview(
                        store,
                        legacy_root=legacy,
                        project_session_id=scan.project_session_id,
                        expected_storage_revision=scan.expected_storage_revision,
                    )
            if action == "scratch-confirm":
                cleanup = ScratchConfirmRequest.model_validate(payload, strict=True)
                with application.storage_authority(
                    cleanup.project_session_id, maintenance="cleanup_storage"
                ) as (store, _legacy):
                    return self._scratch.confirm(
                        store,
                        project_session_id=cleanup.project_session_id,
                        expected_storage_revision=cleanup.expected_storage_revision,
                        ticket_id=cleanup.ticket_id,
                        candidate_ids=cleanup.candidate_ids,
                    )
            if action == "inspect":
                inspect = StorageInspectRequest.model_validate(payload, strict=True)
                with application.storage_authority(inspect.project_session_id) as (store, legacy):
                    return inspect_storage(store, legacy)
            if action == "index":
                index = StorageIndexRequest.model_validate(payload, strict=True)
                with application.storage_authority(index.project_session_id, modifying=True) as (
                    store,
                    legacy,
                ):
                    return export_storage_index(
                        store,
                        legacy_root=legacy,
                        project_session_id=index.project_session_id,
                        expected_storage_revision=index.expected_storage_revision,
                    )
            if action == "confirm":
                confirm = StorageMigrationConfirmRequest.model_validate(payload, strict=True)
                with application.storage_authority(confirm.project_session_id, modifying=True) as (
                    store,
                    _,
                ):
                    return self._migration.confirm(
                        store,
                        ticket_id=confirm.ticket_id,
                        project_session_id=confirm.project_session_id,
                        expected_storage_revision=confirm.expected_storage_revision,
                    )
            if action not in {"configure", "preview", "organize-preview", "restore-preview"}:
                raise HostBridgeFailure(
                    "E_PROJECT_STORAGE_ROUTE", "未知工程数据操作", http_status=404
                )
            request = StorageLocationRequest.model_validate(payload, strict=True)
            with application.storage_authority(
                request.project_session_id, modifying=action == "configure"
            ) as (store, legacy):
                target = _target(store, request, session, existing=action == "restore-preview")
                if action == "restore-preview":
                    return self._migration.preview_restore(
                        store,
                        legacy_root=legacy,
                        target=target,
                        project_session_id=request.project_session_id,
                        expected_storage_revision=request.expected_storage_revision,
                    )
                if action in {"preview", "organize-preview"}:
                    return self._migration.preview(
                        store,
                        legacy_root=legacy,
                        target=target,
                        project_session_id=request.project_session_id,
                        expected_storage_revision=request.expected_storage_revision,
                        organize=action == "organize-preview",
                    )
                inspection = inspect_storage(store, legacy)
                if inspection.attempt_count:
                    raise HostBridgeFailure(
                        "E_PROJECT_STORAGE_MIGRATION_REQUIRED",
                        "工程已有执行记录，请预览并明确确认迁移；不会直接改指针",
                        http_status=409,
                    )
                if store.load_authoring().storage_revision != request.expected_storage_revision:
                    raise HostBridgeFailure(
                        "E_PROJECT_STORAGE_CONFLICT",
                        "工程已变化，请重新载入后再更改位置",
                        http_status=409,
                    )
                prepare_storage_location(target, current=store.load_storage())
                store.configure_storage(
                    target, expected_storage_revision=request.expected_storage_revision
                )
                return inspect_storage(store, legacy)
        except ValidationError as error:
            raise HostBridgeFailure(
                "E_PROJECT_STORAGE_REQUEST", "工程数据请求字段或类型无效", http_status=422
            ) from error
        except OSError as error:
            raise HostBridgeFailure(
                "E_PROJECT_STORAGE_IO",
                "无法读取工程数据，请检查磁盘、权限和网络；不会自动重试",
                http_status=409,
            ) from error
        except ValueError as error:
            raise HostBridgeFailure(
                "E_PROJECT_STORAGE_PATH",
                "工程数据路径或文件身份无法核实，未执行清理",
                http_status=409,
            ) from error
