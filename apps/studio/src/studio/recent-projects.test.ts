import { describe, expect, it } from 'vitest'
import { readRecentProjects, rememberRecentProject } from './recent-projects'

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}

describe('recent project local preference', () => {
  it('只保存显示字段，按时间排序并以 Windows path 不区分大小写去重', () => {
    const storage = new MemoryStorage()
    rememberRecentProject(
      { path: 'D:\\Work\\Movie.zniku', name: 'Movie', opened_at: '2026-09-04T01:00:00Z' },
      storage,
    )
    const result = rememberRecentProject(
      { path: 'd:\\work\\movie.zniku', name: 'Movie updated', opened_at: '2026-09-04T02:00:00Z' },
      storage,
    )

    expect(result).toEqual([
      {
        path: 'd:\\work\\movie.zniku',
        name: 'Movie updated',
        opened_at: '2026-09-04T02:00:00Z',
      },
    ])
    expect(storage.getItem('zniku.studio.recent-projects.v1')).not.toContain('project_id')
    expect(storage.getItem('zniku.studio.recent-projects.v1')).not.toContain('run_id')
  })

  it('未知字段、未知版本和损坏 JSON 都失败关闭为空偏好', () => {
    const storage = new MemoryStorage()
    storage.setItem('zniku.studio.recent-projects.v1', '{broken')
    expect(readRecentProjects(storage)).toEqual([])
    storage.setItem(
      'zniku.studio.recent-projects.v1',
      JSON.stringify({ version: 2, projects: [] }),
    )
    expect(readRecentProjects(storage)).toEqual([])
    storage.setItem(
      'zniku.studio.recent-projects.v1',
      JSON.stringify({
        version: 1,
        projects: [{ path: 'D:\\x.zniku', name: 'x', opened_at: new Date().toISOString(), token: 'no' }],
      }),
    )
    expect(readRecentProjects(storage)).toEqual([])
  })

  it('不把相对、非工程或越界路径从不可信偏好提交给 Project Service', () => {
    const storage = new MemoryStorage()
    for (const path of ['movie.zniku', '..\\movie.zniku', 'D:\\movie.mkv', 'D:\\x\\..\\movie.zniku']) {
      storage.setItem(
        'zniku.studio.recent-projects.v1',
        JSON.stringify({
          version: 1,
          projects: [{ path, name: 'Movie', opened_at: '2026-09-04T02:00:00Z' }],
        }),
      )
      expect(readRecentProjects(storage)).toEqual([])
    }

    expect(rememberRecentProject({ path: '/tmp/movie.zniku', name: 'Movie' }, storage)[0]?.path)
      .toBe('/tmp/movie.zniku')
    expect(rememberRecentProject({ path: '\\\\server\\share\\movie.zniku', name: 'Movie' }, storage)[0]?.path)
      .toBe('\\\\server\\share\\movie.zniku')
  })

  it('列表上限为八项', () => {
    const storage = new MemoryStorage()
    for (let index = 0; index < 10; index += 1) {
      rememberRecentProject(
        {
          path: `D:\\${index}.zniku`,
          name: `Project ${index}`,
          opened_at: `2026-09-04T${String(index).padStart(2, '0')}:00:00Z`,
        },
        storage,
      )
    }
    expect(readRecentProjects(storage)).toHaveLength(8)
    expect(readRecentProjects(storage)[0]?.name).toBe('Project 9')
  })
})
