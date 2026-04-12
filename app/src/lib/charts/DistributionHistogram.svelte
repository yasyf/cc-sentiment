<script lang="ts">
	import { BarChart } from 'layerchart';
	import { scaleBand } from 'd3-scale';
	import type { DistributionPoint } from '../types.js';

	const { data }: { data: DistributionPoint[] } = $props();

	const SCORE_COLORS: Record<number, string> = {
		1: 'var(--color-sentiment-1)',
		2: 'var(--color-sentiment-2)',
		3: 'var(--color-sentiment-3)',
		4: 'var(--color-sentiment-4)',
		5: 'var(--color-sentiment-5)'
	};

	const totalCount = $derived(data.reduce((sum, d) => sum + d.count, 0));

	const chartData = $derived(
		data
			.toSorted((a, b) => a.score - b.score)
			.map((d) => ({
				score: String(d.score),
				count: d.count,
				pct: totalCount > 0 ? ((d.count / totalCount) * 100).toFixed(0) + '%' : '0%',
				color: SCORE_COLORS[d.score] ?? 'var(--color-accent)'
			}))
	);
</script>

<div class="h-72">
	<BarChart
		data={chartData}
		x="score"
		xScale={scaleBand().padding(0.3)}
		series={[{ key: 'count', color: 'var(--color-accent)' }]}
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
				class: 'text-text-muted'
			}
		}}
		tooltip={false}
		height={288}
	/>
</div>
