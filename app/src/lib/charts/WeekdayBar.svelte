<script lang="ts">
	import { BarChart } from 'layerchart';
	import { scaleBand } from 'd3-scale';
	import type { WeekdayPoint } from '../types.js';

	const { data }: { data: WeekdayPoint[] } = $props();

	const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

	function scoreColor(score: number): string {
		if (score < 1.5) return 'var(--color-sentiment-1)';
		if (score < 2.5) return 'var(--color-sentiment-2)';
		if (score < 3.0) return 'var(--color-sentiment-3)';
		if (score < 4.0) return 'var(--color-sentiment-4)';
		return 'var(--color-sentiment-5)';
	}

	const chartData = $derived(
		data
			.toSorted((a, b) => a.dow - b.dow)
			.map((d) => ({
				day: DAY_NAMES[d.dow],
				avg_score: d.avg_score,
				color: scoreColor(d.avg_score)
			}))
	);
</script>

<div class="h-72">
	<BarChart
		data={chartData}
		x="day"
		xScale={scaleBand().padding(0.3)}
		series={[{ key: 'avg_score', color: 'var(--color-accent)' }]}
		yDomain={[0, 5]}
		axis="x"
		grid={false}
		rule={false}
		props={{
			bars: {
				rounded: 'top',
				radius: 4,
				strokeWidth: 0
			},
			xAxis: {
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
