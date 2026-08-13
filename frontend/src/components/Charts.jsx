import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const textColor = '#81928c'
const gridColor = 'rgba(136, 162, 151, 0.11)'

function EChart({ option, style, label }) {
  const elementRef = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    if (!elementRef.current) return undefined

    const chart = echarts.init(elementRef.current, null, { renderer: 'canvas' })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current)
    chartRef.current = chart

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true, lazyUpdate: true })
  }, [option])

  return <div ref={elementRef} style={style} role="img" aria-label={label} />
}

export function OddsDriftChart({ series }) {
  const labels = series.map((item) => item[0])
  const market = series.map((item) => Math.round(item[1] * 1000) / 10)
  const model = series.map((item) => Math.round(item[2] * 1000) / 10)
  const option = {
    animationDuration: 650,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#12211e',
      borderColor: '#263a34',
      textStyle: { color: '#edf7f2', fontFamily: 'Inter, sans-serif' },
      formatter: (params) => params.map((p) => `${p.marker}${p.seriesName} ${p.value}%`).join('<br/>'),
    },
    legend: { right: 0, top: 0, textStyle: { color: textColor, fontSize: 11 }, itemWidth: 14, itemHeight: 4 },
    grid: { left: 36, right: 12, top: 38, bottom: 24 },
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: false,
      axisLine: { lineStyle: { color: gridColor } },
      axisTick: { show: false },
      axisLabel: { color: textColor, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      min: (value) => Math.floor(value.min - 3),
      max: (value) => Math.ceil(value.max + 3),
      splitNumber: 3,
      axisLabel: { color: textColor, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed' } },
    },
    series: [
      {
        name: '市场概率',
        data: market,
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        lineStyle: { color: '#82918c', width: 2 },
        itemStyle: { color: '#82918c' },
      },
      {
        name: '模型概率',
        data: model,
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        lineStyle: { color: '#59d5d0', width: 2.5 },
        itemStyle: { color: '#59d5d0' },
        areaStyle: { color: 'rgba(89, 213, 208, 0.06)' },
      },
    ],
  }
  return <EChart option={option} style={{ height: 210, width: '100%' }} label="模型概率与市场概率赔率漂移图" />
}

export function EquityChart({ values }) {
  const option = {
    tooltip: { trigger: 'axis', backgroundColor: '#12211e', borderColor: '#263a34', textStyle: { color: '#edf7f2' } },
    grid: { left: 42, right: 18, top: 18, bottom: 28 },
    xAxis: {
      type: 'category',
      data: values.map((_, index) => `W${index + 1}`),
      boundaryGap: false,
      axisLine: { lineStyle: { color: gridColor } },
      axisTick: { show: false },
      axisLabel: { color: textColor, interval: 2 },
    },
    yAxis: {
      type: 'value',
      min: 95,
      axisLabel: { color: textColor },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed' } },
    },
    series: [{
      data: values,
      type: 'line',
      smooth: 0.25,
      symbol: 'none',
      lineStyle: { color: '#b7f36b', width: 2.5 },
      areaStyle: { color: 'rgba(183, 243, 107, 0.08)' },
    }],
  }
  return <EChart option={option} style={{ height: 320 }} label="累计权益曲线" />
}

export function EvaluationPathChart({ values = [] }) {
  const labels = values.map((item) => item[0])
  const model = values.map((item) => item[1])
  const market = values.map((item) => item[2])
  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#12211e',
      borderColor: '#263a34',
      textStyle: { color: '#edf7f2' },
      valueFormatter: (value) => Number(value).toFixed(4),
    },
    legend: {
      right: 0,
      top: 0,
      textStyle: { color: textColor, fontSize: 10 },
      itemWidth: 14,
      itemHeight: 4,
    },
    grid: { left: 48, right: 18, top: 42, bottom: 34 },
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: false,
      axisLine: { lineStyle: { color: gridColor } },
      axisTick: { show: false },
      axisLabel: { color: textColor, fontSize: 9, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: textColor, formatter: (value) => Number(value).toFixed(3) },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed' } },
    },
    series: [
      {
        name: '模型 Brier',
        data: model,
        type: 'line',
        smooth: 0.25,
        symbol: 'none',
        lineStyle: { color: '#59d5d0', width: 2.5 },
        itemStyle: { color: '#59d5d0' },
        areaStyle: { color: 'rgba(89, 213, 208, 0.06)' },
      },
      {
        name: '市场 Brier',
        data: market,
        type: 'line',
        smooth: 0.25,
        symbol: 'none',
        lineStyle: { color: '#82918c', width: 1.8, type: 'dashed' },
        itemStyle: { color: '#82918c' },
      },
    ],
  }
  return <EChart option={option} style={{ height: 320 }} label="时间外窗口模型与市场 Brier 评分路径" />
}

export function CalibrationChart({ values }) {
  const option = {
    tooltip: { trigger: 'axis', backgroundColor: '#12211e', borderColor: '#263a34', textStyle: { color: '#edf7f2' } },
    grid: { left: 45, right: 18, top: 20, bottom: 36 },
    xAxis: {
      type: 'value', min: 0, max: 1, interval: 0.2,
      name: '预测概率', nameTextStyle: { color: textColor },
      axisLabel: { color: textColor }, splitLine: { lineStyle: { color: gridColor } },
    },
    yAxis: {
      type: 'value', min: 0, max: 1, interval: 0.2,
      name: '实际频率', nameTextStyle: { color: textColor },
      axisLabel: { color: textColor }, splitLine: { lineStyle: { color: gridColor } },
    },
    series: [
      { data: [[0, 0], [1, 1]], type: 'line', symbol: 'none', lineStyle: { color: '#465a53', type: 'dashed', width: 1.5 } },
      { data: values, type: 'line', smooth: 0.2, symbolSize: 7, lineStyle: { color: '#59d5d0', width: 2.4 }, itemStyle: { color: '#59d5d0' } },
    ],
  }
  return <EChart option={option} style={{ height: 320 }} label="预测概率校准曲线" />
}

export function MiniTrend({ values, tone = 'cyan' }) {
  const color = tone === 'lime' ? '#b7f36b' : '#59d5d0'
  const option = {
    animation: false,
    grid: { left: 0, right: 0, top: 3, bottom: 3 },
    xAxis: { type: 'category', show: false, data: values.map((_, index) => index) },
    yAxis: { type: 'value', show: false, min: Math.min(...values) - 3, max: Math.max(...values) + 3 },
    series: [{ data: values, type: 'line', smooth: 0.35, symbol: 'none', lineStyle: { color, width: 2 }, areaStyle: { color: tone === 'lime' ? 'rgba(183,243,107,.05)' : 'rgba(89,213,208,.05)' } }],
  }
  return <EChart option={option} style={{ width: 110, height: 38 }} label="球员近七场状态趋势" />
}
