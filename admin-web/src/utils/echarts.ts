import * as echarts from 'echarts/core'
import { GraphChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, GraphicComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// Keep the dashboard and knowledge-workspace chart surface explicit. Importing
// from `echarts` registers every chart type and makes the lazy chart chunk much
// larger than the product actually uses.
//
// GraphicComponent is required by dashboard empty-state `graphic` text blocks
// (see views/dashboard/index.vue); without it ECharts logs
// "GraphicComponent is not registered" to the console. (WP-0A-T02)
echarts.use([
  CanvasRenderer,
  GraphChart,
  GridComponent,
  LegendComponent,
  LineChart,
  PieChart,
  TooltipComponent,
  GraphicComponent,
])

export { echarts }
export type { ECharts } from 'echarts/core'
