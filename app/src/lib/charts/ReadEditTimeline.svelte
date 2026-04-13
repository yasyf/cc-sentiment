<script lang="ts">
	import { Line } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import 'chartjs-adapter-date-fns';
	import type { TimelinePoint } from '$lib/types.js';
	import { GRID, TICK, TOOLTIP } from '$lib/chart-theme.js';

	Chart.register(LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, annotationPlugin);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const sorted = $derived(
		raw
			.filter((d) => d.avg_read_edit_ratio != null)
			.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
	);

	const chartData = $derived({
		labels: sorted.map((d) => d.time),
		datasets: [{
			data: sorted.map((d) => d.avg_read_edit_ratio),
			borderColor: '#6366f1',
			backgroundColor: 'rgba(99, 102, 241, 0.08)',
			fill: true,
			tension: 0.3,
			pointRadius: 2,
			pointHoverRadius: 5,
			pointBackgroundColor: sorted.map((d) => {
				const v = d.avg_read_edit_ratio ?? 0;
				if (v >= 4) return '#16a34a';
				if (v >= 2) return '#ca8a04';
				return '#dc2626';
			}),
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
				time: { unit: 'day' as const, tooltipFormat: 'MMM d, yyyy HH:mm' },
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 }, maxTicksLimit: 8 },
				border: { display: false }
			},
			y: {
				min: 0,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 } },
				border: { display: false },
				title: { display: true, text: 'read:edit ratio', color: TICK, font: { size: 10 } }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { y: number | null } }) =>
						`${(ctx.parsed.y ?? 0).toFixed(1)} reads per edit`
				}
			},
			annotation: {
				annotations: {
					healthy: {
						type: 'box' as const,
						yMin: 4,
						yMax: 20,
						backgroundColor: 'rgba(22, 163, 74, 0.04)',
						borderWidth: 0,
					},
					warning: {
						type: 'box' as const,
						yMin: 2,
						yMax: 4,
						backgroundColor: 'rgba(202, 138, 4, 0.04)',
						borderWidth: 0,
					},
					danger: {
						type: 'box' as const,
						yMin: 0,
						yMax: 2,
						backgroundColor: 'rgba(220, 38, 38, 0.04)',
						borderWidth: 0,
					}
				}
			}
		}
	};
</script>

<div class="h-48 w-full">
	<Line data={chartData} options={chartOptions} />
</div>
