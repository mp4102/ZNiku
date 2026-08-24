/** ZNIKU Studio 唯一产品入口；不再提供 Formal、Plan、Monitor 或 GUI-0 双轨工作区。 */

import { ReactFlowProvider } from '@xyflow/react'
import { StudioWorkspace, type StudioWorkspaceProps } from './studio/StudioWorkspace'

export type AppProps = StudioWorkspaceProps

export function App(props: AppProps) {
  return (
    <ReactFlowProvider>
      <StudioWorkspace {...props} />
    </ReactFlowProvider>
  )
}
