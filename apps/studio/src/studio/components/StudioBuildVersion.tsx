/** 只标识当前前端构建，帮助验收时辨别旧标签；不是工程格式或服务健康证明。 */
import { version } from '../../../package.json'
import './studio-build-version.css'

export function StudioBuildVersion() {
  return <div className="studio-build-version" aria-label="前端构建版本">
    <span>ZNIKU Studio · v{version} 验收候选</span>
    <small>仅表示此页面的前端构建版本，不代表工程格式或服务连接状态。</small>
  </div>
}
