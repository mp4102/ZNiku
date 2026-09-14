/** 共用 HTTP 通道的普通工作源方法组；检查身份及 CAS，不复制工作流业务。 */
import { parseStatusEnvelope, StudioContractError, type StatusEnvelope } from './contracts'
import { preparedSourceProcessingMatches } from './prepared-source-contracts'
import * as work from './working-source-contracts'

export interface WorkingSourceGateway {
  create(request: work.WorkCreateRequest): Promise<StatusEnvelope>
  inspect(request: work.WorkViewRequest): Promise<work.WorkViewEnvelope>
  choose(request: work.WorkChooseRequest): Promise<StatusEnvelope>
  inspectOperation(request: work.WorkOperationRequest): Promise<work.WorkOperationEnvelope>
  cancel(request: work.WorkViewRequest): Promise<StatusEnvelope>
  cancelOperation(request: work.WorkOperationRequest): Promise<StatusEnvelope>
  processing(request: work.WorkProcessingRequest): Promise<work.WorkProcessingEnvelope>
  preview(request: work.WorkFullRequest): Promise<work.WorkFullEnvelope>
  expand(request: work.WorkFullRequest): Promise<StatusEnvelope>
}
type Transport = <T>(action: string, payload: unknown, parser: (value: unknown) => T) => Promise<T>
export function createWorkingSourceGateway(send: Transport): WorkingSourceGateway {
  const mutate = async (action: 'choose' | 'expand', payload: work.WorkChooseRequest | work.WorkFullRequest) => {
    const status = await send(action, payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id || status.storage_revision !== payload.expected_storage_revision + 1) throw new StudioContractError('工作源变更响应与当前工程或存储版本不一致')
    return status
  }
  const cancel = async (action: 'cancel' | 'operation-cancel', payload: work.WorkViewRequest | work.WorkOperationRequest) => {
    const status = await send(action, payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id) throw new StudioContractError('工作源停止响应不属于当前工程')
    return status
  }
  return {
    async create(request) {
      const payload = work.parseWorkCreateRequest(request), status = await send('create', payload, parseStatusEnvelope)
      if (!status.project_session_id || status.project_path !== payload.project_path || status.snapshot?.project.project_id !== payload.project_id || status.snapshot.project.name !== payload.project_name) throw new StudioContractError('工作源创建响应与请求的工程身份不一致')
      return status
    },
    async inspect(request) {
      const payload = work.parseWorkViewRequest(request), view = await send('view', payload, work.parseWorkViewEnvelope)
      if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id) throw new StudioContractError('工作源视图不属于请求的工程或运行记录')
      return view
    },
    choose: (request) => mutate('choose', work.parseWorkChooseRequest(request)),
    async inspectOperation(request) {
      const payload = work.parseWorkOperationRequest(request), view = await send('operation-view', payload, work.parseWorkOperationEnvelope)
      if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id || view.node_run_id !== payload.node_run_id) throw new StudioContractError('工作源操作视图不属于请求的工程、运行或节点 attempt')
      return view
    },
    cancel: (request) => cancel('cancel', work.parseWorkViewRequest(request)),
    cancelOperation: (request) => cancel('operation-cancel', work.parseWorkOperationRequest(request)),
    async processing(request) {
      const payload = work.parseWorkProcessingRequest(request), preview = await send('processing-preview', payload, work.parseWorkProcessingEnvelope)
      if (!preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源处理设置回显与请求不一致')
      return preview
    },
    async preview(request) {
      const payload = work.parseWorkFullRequest(request), preview = await send('full-preview', payload, work.parseWorkFullEnvelope)
      if (preview.project_session_id !== payload.project_session_id || preview.storage_revision !== payload.expected_storage_revision || preview.preparation_run_id !== payload.preparation_run_id || !preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源预览与工程、存储版本、检查记录或设置不一致')
      return preview
    },
    expand: (request) => mutate('expand', work.parseWorkFullRequest(request)),
  }
}
