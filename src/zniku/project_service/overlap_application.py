"""串行实现新候选的只读预览与一次性普通Graph展开，错误不留下半张工程。

复用facade同一会话锁、存储CAS和精确preparation binding。仅初始preparation允许模板展开；
已编辑或已展开的图由普通节点编辑器管理，绝不借向导覆盖用户自定义分支或历史产物。
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.template import Av27TemplateError
from zniku.chapter_overlap import ChapterPlanningError
from zniku.chapter_overlap.context import ContextPlanningError
from zniku.chapter_overlap.template import build_overlap
from zniku.graph import GraphValidationError
from zniku.project import ProjectStoreError
from zniku.runtime import RuntimeRepositoryError

from .chapter_overlap import (
    ChapterOverlapFullEnvelope,
    ChapterOverlapFullRequest,
    ChapterOverlapPreviewError,
    ChapterOverlapProcessingEnvelope,
    ChapterOverlapProcessingRequest,
)
from .models import StatusEnvelope

if TYPE_CHECKING:
    from .service import ProjectServiceApplication


def parse_overlap[M: BaseModel](model: type[M], payload: object) -> M:
    """严格返回第一个结构化字段位置，不在错误响应回显用户输入。"""

    try:
        return model.model_validate(payload, strict=True)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        location = tuple(
            part for part in first["loc"] if part not in {"average", "exact_frames", "exact_times"}
        )
        raise ChapterOverlapPreviewError(
            "E_OVERLAP_REQUEST_INVALID", first["msg"], field_path=location
        ) from error


def processing_preview(payload: object) -> ChapterOverlapProcessingEnvelope:
    """分析前只验证处理合同，不启动节点或猜测N/FPS。"""

    request = parse_overlap(ChapterOverlapProcessingRequest, payload)
    return ChapterOverlapProcessingEnvelope(processing=request.processing)


def full_overlap(
    app: ProjectServiceApplication,
    payload: object,
    *,
    expand: bool,
) -> ChapterOverlapFullEnvelope | StatusEnvelope:
    """同锁重验并展开；仅save成功后返回新status，不自动启动Run。"""

    from .service import ProjectServiceError

    request = parse_overlap(ChapterOverlapFullRequest, payload)
    with app._state:
        try:
            if app._desktop_closing:
                raise ProjectServiceError("E_DESKTOP_CLOSING", "应用正在关闭", http_status=409)
            app._assert_idle()
            app.assert_preview_session(request.project_session_id)
            store, _runtime = app._require_session()
            current = store.load_authoring()
            if current.storage_revision != request.expected_storage_revision:
                raise ProjectServiceError(
                    "E_PROJECT_STORAGE_CONFLICT", "工程已变更，请刷新后重试", http_status=409
                )
            preparation, binding = app._resolve_av27_preparation_run(
                request.preparation_run_id, overlap=True
            )
            if expand and current.snapshot.project.graph != preparation.project.graph:
                raise ProjectServiceError(
                    "E_OVERLAP_GRAPH_ALREADY_EXPANDED",
                    "当前图已展开或含自定义节点。请在节点图编辑，不会由向导覆盖现有流程。",
                    http_status=409,
                )
            for source in binding.sources:
                for artifact in (
                    source.source_media_artifact,
                    source.effective_video_artifact,
                    binding.admission_artifact,
                ):
                    try:
                        facts = Path(artifact.path).stat()
                    except OSError as error:
                        raise ProjectServiceError(
                            "E_OVERLAP_INPUT_CHANGED",
                            "分析输入已不可用，请重新分析",
                            http_status=409,
                        ) from error
                    if (
                        not stat.S_ISREG(facts.st_mode)
                        or facts.st_size != artifact.size
                        or facts.st_mtime_ns != artifact.mtime_ns
                    ):
                        raise ProjectServiceError(
                            "E_OVERLAP_INPUT_CHANGED", "分析输入已变化，请重新分析", http_status=409
                        )
            build = build_overlap(preparation, binding, request.processing, request.publication)
            if expand:
                store.save(
                    build.project,
                    build.definitions,
                    expected_storage_revision=request.expected_storage_revision,
                )
                app._last_error = None
                return app.inspect()
            return ChapterOverlapFullEnvelope(
                project_session_id=request.project_session_id,
                storage_revision=current.storage_revision,
                preparation_run_id=binding.preparation_run_id,
                processing=request.processing,
                plan=build.plan,
                contexts=build.contexts,
                publication=build.publication,
                node_count=len(build.project.graph.nodes),
                edge_count=len(build.project.graph.edges),
            )
        except ChapterOverlapPreviewError:
            raise
        except ChapterPlanningError as error:
            raise ChapterOverlapPreviewError(
                error.code, error.message, field_path=("processing", "settings", *error.field_path)
            ) from error
        except ContextPlanningError as error:
            raise ChapterOverlapPreviewError(
                error.code,
                error.message,
                field_path=("processing", "fi_profile", *error.field_path),
            ) from error
        except ProjectServiceError as error:
            raise ChapterOverlapPreviewError(
                error.code,
                error.message,
                http_status=error.http_status,
                related_run_ids=error.related_run_ids,
            ) from error
        except Av27TemplateError as error:
            raise ChapterOverlapPreviewError(
                error.code, error.message, field_path=("publication",)
            ) from error
        except Av27MediaError as error:
            raise ChapterOverlapPreviewError(error.code, str(error)) from error
        except GraphValidationError as error:
            raise ChapterOverlapPreviewError("E_OVERLAP_GRAPH_INVALID", str(error)) from error
        except (ProjectStoreError, RuntimeRepositoryError, ValidationError) as error:
            failure = app._translate_failure(error)
            raise ChapterOverlapPreviewError(
                failure.code, failure.message, http_status=failure.http_status
            ) from error
