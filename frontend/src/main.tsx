import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* BrowserRouter：地址即状态（/ 欢迎页、/session/:id 会话视图），
        刷新/重开浏览器后按地址恢复会话回放，不再回到首页 */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
