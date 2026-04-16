<script lang="ts">
	import { Line } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip } from 'chart.js';
	import 'chartjs-adapter-luxon';
	import type { TimelinePoint } from '$lib/types.js';
	import { GRID, TICK, TOOLTIP, ACCENT, ACCENT_LIGHT } from '$lib/chart-theme.js';

	Chart.register(LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const sorted = $derived(
		raw
			.filter((d) => d.avg_tool_calls_per_turn != null)
			.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
	);

	const chartData = $derived({
		labels: sorted.map((d) => d.time),
		datasets: [{
			data: sorted.map((d) => d.avg_tool_calls_per_turn),
			borderColor: ACCENT,
			backgroundColor: ACCENT_LIGHT,
			fill: true,
			tension: 0.3,
			pointRadius: 2,
			pointHoverRadius: 5,
			pointBackgroundColor: ACCENT,
			pointBorderColor: 'transparent',
			borderWidth: 1.5,
			cubicInterpolationMode: 'monotone' as const
		}]
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'day' as const, tooltipFormat: 'LLL d, yyyy HH:mm ZZZZ' },
				adapters: { date: { zone: 'America/Los_Angeles' } },
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 }, maxTicksLimit: 8 },
				border: { display: false }
			},
			y: {
				min: 0,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 } },
				border: { display: false },
				title: { display: true, text: 'tool calls per turn', color: TICK, font: { size: 10 } }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { y: number | null } }) =>
						`${(ctx.parsed.y ?? 0).toFixed(2)} tool calls per turn`
				}
			}
		}
	};
</script>

<div class="h-48 w-full">
	<Line data={chartData} options={chartOptions} />
</div>
