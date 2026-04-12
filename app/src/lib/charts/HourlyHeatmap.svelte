<script lang="ts">
	import { BarChart } from 'layerchart';
	import { scaleBand } from 'd3-scale';
	import type { HourlyPoint } from '../types.js';

	const { data }: { data: HourlyPoint[] } = $props();

	const HOUR_LABELS: Record<number, string> = {
		0: '12am',
		3: '3am',
		6: '6am',
		9: '9am',
		12: '12pm',
		15: '3pm',
		18: '6pm',
		21: '9pm'
	};

	function scoreColor(score: number): string {
		if (score < 1.5) return 'var(--color-sentiment-1)';
		if (score < 2.5) return 'var(--color-sentiment-2)';
		if (score < 3.0) return 'var(--color-sentiment-3)';
		if (score < 4.0) return 'var(--color-sentiment-4)';
		return 'var(--color-sentiment-5)';
	}

	const chartData = $derived(
		data.map((d) => ({
			hour: String(d.hour),
			avg_score: d.avg_score,
			color: scoreColor(d.avg_score)
		}))
	);
</script>

<div class="h-72">
	<BarChart
		data={chartData}
		x="hour"
		xScale={scaleBand().padding(0.2)}
		series={[{ key: 'avg_score', color: 'var(--color-accent)' }]}
		yDomain={[0, 5]}
		axis="x"
		grid={false}
		rule={false}
		props={{
			bars: {
				rounded: 'top',
				radius: 3,
				strokeWidth: 0
			},
			xAxis: {
				format: (v) => HOUR_LABELS[Number(v)] ?? '',
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
