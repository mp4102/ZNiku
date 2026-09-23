"""融合编码候选的独立 wire；显式展开普通图，旧请求接受集合保持原样。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.template import Av27TemplateError
from zniku.chapter_batch.fused_template import build_fused
from zniku.chapter_overlap import ChapterPlanningError
from zniku.chapter_overlap.context import ContextPlanningError
from zniku.graph import GraphValidationError
from zniku.project import ProjectStoreError
from zniku.runtime import RuntimeRepositoryError

from .models import StatusEnvelope
from .source_admitted import (
    SourceAdmittedError,
    SourceAdmittedFullEnvelope,
    SourceAdmittedFullRequest,
)
from .source_admitted_application import full

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

PREFIX = "/api/studio/templates/chapter-batch-fused"
PREVIEW_ROUTE = PREFIX + "/full-preview"
EXPAND_ROUTE = PREFIX + "/expand"
ROUTES = frozenset({PREVIEW_ROUTE, EXPAND_ROUTE})


class FusedFullRequest(SourceAdmittedFullRequest):
    """新模式不能偷渡进旧0.3.5请求；独立版本与严格可选导出意图。"""

    contract_version: Literal["0.3.6"]  # type: ignore[assignment]
    export_cropped_chapters: bool = False


class FusedFullEnvelope(SourceAdmittedFullEnvelope):
    contract_version: Literal["0.3.6"] = "0.3.6"  # type: ignore[assignment]
    profile_version: Literal["0.3.6"] = "0.3.6"  # type: ignore[assignment]
    profile_id: Literal["zniku.source-admitted.chapter-batch-fused"] = (
        "zniku.source-admitted.chapter-batch-fused"  # type: ignore[assignment]
    )
    export_cropped_chapters: bool = False


def dispatch(app: ProjectServiceApplication, route: str, payload: object) -> BaseModel:
    """复用原分析/会话/CAS门禁，只在本次构造结果上选择新模板，不接管运行工程。"""
    from .service import ProjectServiceError

    try:
        if route not in ROUTES:
            raise SourceAdmittedError("E_FUSED_ROUTE", "未知融合候选路由", http_status=404)
        request = FusedFullRequest.model_validate(payload, strict=True)
        old_request = SourceAdmittedFullRequest.model_validate(
            {
                **request.model_dump(exclude={"contract_version", "export_cropped_chapters"}),
                "contract_version": "0.3.5",
            }
        )
        result = full(
            app,
            old_request,
            expand=route == EXPAND_ROUTE,
            _build_transform=lambda build: build_fused(
                build, export_cropped_chapters=request.export_cropped_chapters
            ),
        )
        if isinstance(result, StatusEnvelope):
            return result
        return FusedFullEnvelope.model_validate(
            {
                **result.model_dump(exclude={"contract_version", "profile_version", "profile_id"}),
                "contract_version": "0.3.6",
                "profile_version": "0.3.6",
                "profile_id": "zniku.source-admitted.chapter-batch-fused",
                "export_cropped_chapters": request.export_cropped_chapters,
            }
        )
    except SourceAdmittedError:
        raise
    except ProjectServiceError as error:
        raise SourceAdmittedError(
            error.code,
            error.message,
            http_status=error.http_status,
            related_run_ids=error.related_run_ids,
        ) from error
    except (ChapterPlanningError, ContextPlanningError) as error:
        field = "settings" if isinstance(error, ChapterPlanningError) else "fi_profile"
        raise SourceAdmittedError(
            error.code, error.message, field_path=("processing", field, *error.field_path)
        ) from error
    except (Av27TemplateError, Av27MediaError) as error:
        raise SourceAdmittedError(error.code, str(error)) from error
    except (ProjectStoreError, RuntimeRepositoryError, GraphValidationError, OSError) as error:
        failure = app._translate_failure(error)
        raise SourceAdmittedError(
            failure.code, failure.message, http_status=failure.http_status
        ) from error
    except (ValidationError, ValueError) as error:
        raise SourceAdmittedError("E_FUSED_REQUEST", str(error)[:1024]) from error
