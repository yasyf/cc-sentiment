<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, BarController, LinearScale, TimeScale, Tooltip } from 'chart.js';
	import 'chartjs-adapter-luxon';
	import { DateTime } from 'luxon';
	import type { TimelinePoint } from '$lib/types.js';
	import { TICK, TOOLTIP, ACCENT } from '$lib/chart-theme.js';
	import {
		bucketByDayPart,
		bucketByDay,
		smoothSeries,
		filterWindow,
		dayPartFor,
		DAY_PART_EMOJI
	} from '$lib/bucket.js';

	Chart.register(BarElement, BarController, LinearScale, TimeScale, Tooltip);

	type Range = 'week' | 'month';
	const { data: raw, range = 'week' }: { data: TimelinePoint[]; range?: Range } = $props();

	const DISPLAY_TZ = 'America/Los_Angeles';

	const buckets = $derived.by(() => {
		if (range === 'week') {
			const all = bucketByDayPart(raw, DISPLAY_TZ);
			const smoothed = smoothSeries(all, (b) => b.avg_tool_calls_per_turn, { halfWindow: 1 });
			const merged = all.map((b, i) => ({ ...b, smoothed: smoothed[i] }));
			return filterWindow(merged, 7, DISPLAY_TZ);
		}
		const all = bucketByDay(raw, DISPLAY_TZ);
		const smoothed = smoothSeries(all, (b) => b.avg_tool_calls_per_turn, { halfWindow: 2 });
		const merged = all.map((b, i) => ({ ...b, smoothed: smoothed[i], label: '' }));
		return filterWindow(merged, 30, DISPLAY_TZ);
	});

	const yMax = $derived.by(() => {
		const vals = buckets.map((b) => b.smoothed).filter((v): v is number => v != null);
		if (vals.length === 0) return 4;
		return Math.max(2, Math.ceil(Math.max(...vals) * 1.15));
	});

	const chartData = $derived({
		labels: buckets.map((d) => d.time),
		datasets: [{
			data: buckets.map((d) => d.smoothed),
			backgroundColor: buckets.map((d) => (d.smoothed != null ? ACCENT : 'rgba(161, 161, 170, 0.3)')),
			borderRadius: 2,
			borderSkipped: false as const,
			categoryPercentage: 0.9,
			barPercentage: 0.85
		}]
	});

	const chartOptions = $derived({
		responsive: true,
		maintainAspectRatio: false,
		animation: false as const,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				type: 'time' as const,
				time: {
					unit: (range === 'week' ? 'hour' : 'day') as 'hour' | 'day',
					tooltipFormat: range === 'week' ? 'LLL d, yyyy · h a' : 'LLL d, yyyy'
				},
				adapters: { date: { zone: DISPLAY_TZ } },
				grid: { display: false },
				ticks: {
					source: 'data' as const,
					autoSkip: true,
					maxRotation: 0,
					color: TICK,
					font: { size: range === 'week' ? 12 : 11 },
					callback: (value: number | string) => {
						const dt = DateTime.fromMillis(Number(value), { zone: DISPLAY_TZ });
						if (range === 'month') return dt.toFormat('LLL d');
						const part = dayPartFor(dt.hour);
						const emoji = DAY_PART_EMOJI[part.key];
						return part.key === 'late' ? [emoji, dt.toFormat('LLL d')] : emoji;
					}
				},
				border: { display: false }
			},
			y: {
				min: 0,
				max: yMax,
				grid: { display: false },
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
						return range === 'week' && b.label ? `${dt} · ${b.label}` : dt;
					},
					label: (ctx: { parsed: { y: number | null }; dataIndex: number }) => {
						const v = ctx.parsed.y;
						if (v == null) return 'no data';
						const b = buckets[ctx.dataIndex];
						return `${v.toFixed(2)} tool calls per turn · ${b?.count ?? 0} sessions`;
					}
				}
			}
		}
	});
</script>

<div class="h-48 w-full">
	<Bar data={chartData} options={chartOptions} />
</div>
