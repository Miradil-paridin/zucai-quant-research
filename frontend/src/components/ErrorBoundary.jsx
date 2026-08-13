import { Component } from 'react'
import { CircleAlert, RefreshCw } from 'lucide-react'

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    console.error('Research dashboard render error', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-state">
          <span><CircleAlert size={21} /></span>
          <h2>页面暂时无法显示</h2>
          <p>界面模块发生了运行时错误，研究数据没有被修改。</p>
          <button onClick={() => window.location.reload()}><RefreshCw size={15} /> 重新载入</button>
        </div>
      )
    }
    return this.props.children
  }
}

