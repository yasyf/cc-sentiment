<script lang="ts">
	import { Line } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip } from 'chart.js';
	import 'chartjs-adapter-date-fns';
	import type { TimelinePoint } from '$lib/types.js';
	import { ACCENT, ACCENT_LIGHT, GRID, TICK, TOOLTIP } from '$lib/chart-theme.js';

	Chart.register(LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const sorted = $derived(raw.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()));

	const chartData = $derived({
		labels: sorted.map((d) => d.time),
		datasets: [{
			data: sorted.map((d) => d.avg_score),
			borderColor: ACCENT,
			backgroundColor: ACCENT_LIGHT,
			fill: true,
			tension: 0.35,
			pointRadius: 2,
			pointHoverRadius: 5,
			pointBackgroundColor: ACCENT,
			pointBorderColor: 'transparent',
			borderWidth: 1.5
		}]
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'day' as const, tooltipFormat: 'MMM d, yyyy HH:mm' },
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 }, maxTicksLimit: 8 },
				border: { display: false }
			},
			y: {
				min: 1, max: 5,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 }, stepSize: 1 },
				border: { display: false }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { y: number | null }; dataIndex: number }) =>
						`${(ctx.parsed.y ?? 0).toFixed(2)} avg  ·  ${sorted[ctx.dataIndex]?.count ?? 0} records`
				}
			}
		}
	};
</script>

<div class="h-52 w-full">
	<Line data={chartData} options={chartOptions} />
</div>
