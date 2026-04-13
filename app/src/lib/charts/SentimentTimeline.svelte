<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, BarElement, LinearScale, TimeScale, Filler, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import 'chartjs-adapter-date-fns';
	import type { TimelinePoint } from '$lib/types.js';
	import { EVENTS } from '$lib/events.js';
	import { ACCENT, ACCENT_LIGHT, GRID, TICK, TOOLTIP } from '$lib/chart-theme.js';

	Chart.register(LineElement, PointElement, BarElement, LinearScale, TimeScale, Filler, Tooltip, Legend, BarController, LineController, annotationPlugin);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const sorted = $derived(raw.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()));

	const timeRange = $derived.by(() => {
		if (sorted.length === 0) return { min: 0, max: 0 };
		return {
			min: new Date(sorted[0].time).getTime(),
			max: new Date(sorted[sorted.length - 1].time).getTime()
		};
	});

	const eventAnnotations = $derived.by(() => {
		const annotations: Record<string, object> = {};
		for (const evt of EVENTS) {
			const t = new Date(evt.date).getTime();
			if (t >= timeRange.min && t <= timeRange.max) {
				annotations[`event-${evt.date}`] = {
					type: 'line',
					xMin: evt.date,
					xMax: evt.date,
					borderColor: evt.type === 'regression' ? 'rgba(220, 38, 38, 0.3)' : 'rgba(99, 102, 241, 0.3)',
					borderWidth: 1,
					borderDash: [4, 4],
					label: {
						display: true,
						content: evt.label,
						position: 'start',
						font: { size: 9 },
						color: '#a1a1aa',
						backgroundColor: 'rgba(255,255,255,0.8)',
						padding: 2
					}
				};
			}
		}
		return annotations;
	});

	const chartData = $derived({
		labels: sorted.map((d) => d.time),
		datasets: [
			{
				type: 'bar' as const,
				label: 'Sessions',
				data: sorted.map((d) => d.count),
				backgroundColor: 'rgba(99, 102, 241, 0.06)',
				hoverBackgroundColor: 'rgba(99, 102, 241, 0.12)',
				borderRadius: 2,
				yAxisID: 'volume',
				order: 2
			},
			{
				type: 'line' as const,
				label: 'Sentiment',
				data: sorted.map((d) => d.avg_score),
				borderColor: ACCENT,
				backgroundColor: ACCENT_LIGHT,
				fill: true,
				tension: 0.2,
				pointRadius: 1.5,
				pointHoverRadius: 5,
				pointBackgroundColor: ACCENT,
				pointBorderColor: 'transparent',
				borderWidth: 1.5,
				cubicInterpolationMode: 'monotone' as const,
				yAxisID: 'score',
				order: 1
			}
		]
	});

	const chartOptions = $derived({
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
			score: {
				position: 'left' as const,
				min: 1, max: 5,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 11 }, stepSize: 1 },
				border: { display: false },
				title: { display: true, text: 'sentiment', color: TICK, font: { size: 10 } }
			},
			volume: {
				position: 'right' as const,
				beginAtZero: true,
				grid: { drawOnChartArea: false },
				ticks: { color: TICK, font: { size: 10 } },
				border: { display: false },
				title: { display: true, text: 'sessions', color: TICK, font: { size: 10 } }
			}
		},
		plugins: {
			legend: {
				display: true,
				position: 'bottom' as const,
				labels: { color: '#71717a', font: { size: 10 }, boxWidth: 10, padding: 20, usePointStyle: true }
			},
			tooltip: {
				...TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { y: number | null }; dataIndex: number; dataset: { label?: string } }) => {
						if (ctx.dataset.label === 'Sessions') return `${ctx.parsed.y ?? 0} sessions`;
						return `${(ctx.parsed.y ?? 0).toFixed(2)} avg sentiment`;
					}
				}
			},
			annotation: { annotations: eventAnnotations }
		}
	});
</script>

<div class="h-80 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
