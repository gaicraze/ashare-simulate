import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

interface KlineItem {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export default function KlineChart({
  kline,
  trades,
  nameMap,
  title = '沪深300 回测区间 K 线（▲买入 ▼卖出）',
}: {
  kline: KlineItem[]
  trades: any[]
  nameMap?: Record<string, string>
  title?: string
}) {
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (chartRef.current && !chartInst.current) {
      chartInst.current = echarts.init(chartRef.current)
    }
    return () => {
      chartInst.current?.dispose()
      chartInst.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartInst.current
    if (!chart || kline.length === 0) return

    const dates = kline.map((k) => k.date)
    const candles = kline.map((k) => [k.open, k.close, k.low, k.high])
    const dateIndex: Record<string, number> = {}
    dates.forEach((d, i) => (dateIndex[d] = i))

    // 从成交记录提取买卖点，标注到指数 K 线上
    const buyPoints: any[] = []
    const sellPoints: any[] = []
    for (const t of trades) {
      if (t.action !== 'buy' && t.action !== 'sell') continue
      const idx = dateIndex[t.date]
      if (idx === undefined) continue
      const bar = kline[idx]
      const base = t.action === 'buy' ? bar.low : bar.high
      const point = {
        coord: [idx, base],
        value: `${t.action === 'buy' ? '买' : '卖'} ${t.code}`,
        name: `${t.date} ${t.action === 'buy' ? '买入' : '卖出'} ${t.code}`,
      }
      if (t.action === 'buy') buyPoints.push(point)
      else sellPoints.push(point)
    }

    chart.setOption({
      title: { text: title, left: 'center', textStyle: { fontSize: 14 } },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: any) => {
          const p = params.find((x: any) => x.seriesType === 'candlestick')
          if (!p) return ''
          const d = kline[p.dataIndex]
          const lines = [
            `<b>${d.date}</b>`,
            `开 ${d.open}  收 ${d.close}`,
            `高 ${d.high}  低 ${d.low}`,
          ]
          // 该日期的买卖
          for (const t of trades) {
            if ((t.action === 'buy' || t.action === 'sell') && t.date === d.date) {
              const nm = nameMap?.[t.code] ? `${t.code} ${nameMap[t.code]}` : t.code
              lines.push(`${t.action === 'buy' ? '🔴买入' : '🟢卖出'} ${nm} @ ${t.price != null ? Number(t.price).toFixed(2) : '-'}`)
            }
          }
          return lines.join('<br/>')
        },
      },
      grid: { left: 60, right: 20, top: 45, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { formatter: (v: string) => v.slice(5) },
      },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'candlestick',
          data: candles,
          itemStyle: {
            color: '#e03131',
            color0: '#12b886',
            borderColor: '#e03131',
            borderColor0: '#12b886',
          },
        },
        {
          type: 'line',
          data: [],
          markPoint: {
            symbolSize: 40,
            label: { fontSize: 10, color: '#fff', fontWeight: 700 },
            data: [
              ...buyPoints.map((p) => ({
                ...p,
                symbol: 'triangle',
                symbolRotate: 0,
                itemStyle: { color: '#e03131' },
                label: { ...{ fontSize: 10, color: '#fff', fontWeight: 700 }, formatter: 'B' },
              })),
              ...sellPoints.map((p) => ({
                ...p,
                symbol: 'triangle',
                symbolRotate: 180,
                itemStyle: { color: '#12b886' },
                label: { ...{ fontSize: 10, color: '#fff', fontWeight: 700 }, formatter: 'S' },
              })),
            ],
          },
        },
      ],
    })
  }, [kline, trades, nameMap, title])

  return <div className="chart" ref={chartRef} style={{ height: 380 }} />
}
