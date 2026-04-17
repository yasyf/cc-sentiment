<script lang="ts">
	import { Line } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import 'chartjs-adapter-luxon';
	import type { TimelinePoint } from '$lib/types.js';
	import { GRID, TICK, TOOLTIP, ACCENT, ACCENT_LIGHT } from '$lib/chart-theme.js';
	import { bucketByDayPart, dayBoundaryAnnotations } from '$lib/bucket.js';

	Chart.register(LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, annotationPlugin);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const DISPLAY_TZ = 'America/Los_Angeles';

	const buckets = $derived(
		bucketByDayPart(raw, DISPLAY_TZ).filter((b) => b.avg_tool_calls_per_turn != null)
	);

	const chartData = $derived({
		labels: buckets.map((d) => d.time),
		datasets: [{
			data: buckets.map((d) => d.avg_tool_calls_per_turn),
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

	const chartOptions = $derived({
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'day' as const, tooltipFormat: 'LLL d, yyyy' },
				adapters: { date: { zone: DISPLAY_TZ } },
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
					title: (items: { dataIndex: number }[]) => {
						const b = buckets[items[0]?.dataIndex];
						if (!b) return '';
						const dt = new Date(b.time).toLocaleDateString('en-US', {
							timeZone: DISPLAY_TZ, month: 'short', day: 'numeric'
						});
						return `${dt} · ${b.label}`;
					},
					label: (ctx: { parsed: { y: number | null } }) =>
						`${(ctx.parsed.y ?? 0).toFixed(2)} tool calls per turn`
				}
			},
			annotation: { annotations: dayBoundaryAnnotations(buckets, DISPLAY_TZ) }
		}
	});
</script>

<div class="h-48 w-full">
	<Line data={chartData} options={chartOptions} />
</div>
