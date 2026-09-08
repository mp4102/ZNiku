"""把工程数据维护接到严格宿主 API，不允许浏览器直接指定待搬运路径。

目标来自本次原生目录选择，或明确恢复工程旁默认位置。已运行工程必须走短期预览与确认；
迁移互斥期间不启动节点、不修改 Graph，原位置始终保留。此模块不提供删除能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, ValidationError

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
from .storage_paths import prepare_storage_location

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


class StorageMigrationConfirmRequest(StorageInspectRequest):
    """确认不能修改预览已绑定的源位置、目标或工程。"""

    expected_storage_revision: Annotated[int, Field(ge=0, le=9007199254740991)]
    ticket_id: HostRandomId


def _target(
    store: ProjectStore, request: StorageLocationRequest, session: HostBridgeSession
) -> ProjectStorage:
    current = store.load_storage()
    root = None
    if request.selection_handle is not None:
        parent = session.resolve_path_reference(
            PickerSelectionReference(
                kind="picker_selection", selection_handle=request.selection_handle
            )
        )
        if not parent.is_dir():
            raise HostBridgeFailure("E_PROJECT_STORAGE_TARGET", "请选择数据父目录", http_status=422)
        root = parent / store.path.with_suffix(".data").name
    return new_project_storage(
        store.path,
        data_root=root,
        media_basename=None if current is None else current.media_basename,
    )


class StorageApi:
    """固定路由适配；大文件复制只由显式 confirm 调用一次，不自动重试。"""

    def __init__(self) -> None:
        self._migration = StorageMigrationManager()

    def invoke(
        self,
        action: str,
        payload: object,
        *,
        session: HostBridgeSession,
        application: ProjectServiceApplication,
    ) -> StorageInspection | StorageMigrationPreview:
        """同一 facade 互斥保护路径切换，并把严格模型错误转为稳定用户失败。"""

        try:
            if action == "inspect":
                inspect = StorageInspectRequest.model_validate(payload, strict=True)
                with application.storage_authority(inspect.project_session_id) as (store, legacy):
                    return inspect_storage(store, legacy)
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
            if action not in {"configure", "preview"}:
                raise HostBridgeFailure(
                    "E_PROJECT_STORAGE_ROUTE", "未知工程数据操作", http_status=404
                )
            request = StorageLocationRequest.model_validate(payload, strict=True)
            with application.storage_authority(
                request.project_session_id, modifying=action == "configure"
            ) as (store, legacy):
                target = _target(store, request, session)
                if action == "preview":
                    return self._migration.preview(
                        store,
                        legacy_root=legacy,
                        target=target,
                        project_session_id=request.project_session_id,
                        expected_storage_revision=request.expected_storage_revision,
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
