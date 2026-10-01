/**
 * 壳层开合状态（ZCode 工作台同语义：isSidebarVisible / toggleSidebar）。
 *
 * 单独成文件避免 App.tsx ↔ 组件的循环导入：App 提供值，
 * ChatArea / WelcomePage 等任意层级的组件直接消费，免逐层钻 props。
 */

import { createContext, useContext } from 'react'

export interface ShellState {
  /** 左侧会话侧栏是否展开 */
  sidebarOpen: boolean
  /** 切换左栏开合（AppShell 负责持久化到 localStorage） */
  toggleSidebar: () => void
}

export const ShellContext = createContext<ShellState>({
  sidebarOpen: true,
  toggleSidebar: () => {}, // 默认空实现：万一漏包 Provider 也不崩
})

export function useShell(): ShellState {
  return useContext(ShellContext)
}
