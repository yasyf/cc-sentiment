<script lang="ts">
	import { AreaChart } from 'layerchart';
	import { scaleTime } from 'd3-scale';
	import { curveMonotoneX } from 'd3-shape';
	import type { TimelinePoint } from '../types.js';

	const { data }: { data: TimelinePoint[] } = $props();

	const chartData = $derived(
		data.map((d) => ({
			date: new Date(d.time),
			avg_score: d.avg_score
		}))
	);
</script>

<div class="h-72">
	<AreaChart
		data={chartData}
		x="date"
		y="avg_score"
		xScale={scaleTime()}
		yDomain={[1, 5]}
		series={[
			{
				key: 'avg_score',
				color: 'var(--color-accent)',
				props: {
					fillOpacity: 0.15
				}
			}
		]}
		axis="x"
		grid={false}
		rule={false}
		props={{
			area: {
				curve: curveMonotoneX,
				line: { class: 'stroke-2' }
			},
			xAxis: {
				format: (v) => {
					if (v instanceof Date) {
						return v.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
					}
					return String(v);
				},
				class: 'text-text-muted'
			},
			yAxis: {
				format: (v) => (typeof v === 'number' ? v.toFixed(1) : String(v)),
				class: 'text-text-muted'
			}
		}}
		tooltip={false}
		height={288}
	/>
</div>
