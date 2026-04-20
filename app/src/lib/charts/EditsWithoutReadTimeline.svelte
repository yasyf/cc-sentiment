<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, BarController, LinearScale, TimeScale, Tooltip } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import 'chartjs-adapter-luxon';
	import { DateTime } from 'luxon';
	import type { TimelinePoint } from '$lib/types.js';
	import { chartTheme } from '$lib/chart-theme.js';
	import {
		bucketByDayPart,
		bucketByDay,
		smoothSeries,
		filterWindow,
		dayPartFor,
		DAY_PART_EMOJI
	} from '$lib/bucket.js';

	Chart.register(BarElement, BarController, LinearScale, TimeScale, Tooltip, annotationPlugin);

	type Range = 'week' | 'month';
	const { data: raw, range = 'week' }: { data: TimelinePoint[]; range?: Range } = $props();

	const DISPLAY_TZ = 'America/Los_Angeles';

	const buckets = $derived.by(() => {
		if (range === 'week') {
			const all = bucketByDayPart(raw, DISPLAY_TZ);
			const smoothed = smoothSeries(all, (b) => b.avg_edits_without_prior_read_ratio, { halfWindow: 1 });
			const merged = all.map((b, i) => ({ ...b, smoothed: smoothed[i] }));
			return filterWindow(merged, 7, DISPLAY_TZ);
		}
		const all = bucketByDay(raw, DISPLAY_TZ);
		const smoothed = smoothSeries(all, (b) => b.avg_edits_without_prior_read_ratio, { halfWindow: 2 });
		const merged = all.map((b, i) => ({ ...b, smoothed: smoothed[i], label: '' }));
		return filterWindow(merged, 30, DISPLAY_TZ);
	});

	function colorFor(v: number | null): string {
		if (v == null) return chartTheme.DISABLED_BAR;
		const pct = v * 100;
		if (pct <= 20) return chartTheme.SENTIMENT[4];
		if (pct <= 50) return chartTheme.SENTIMENT[3];
		return chartTheme.SENTIMENT[1];
	}

	const chartData = $derived({
		labels: buckets.map((d) => d.time),
		datasets: [{
			data: buckets.map((d) => (d.smoothed != null ? d.smoothed * 100 : null)),
			backgroundColor: buckets.map((d) => colorFor(d.smoothed)),
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
					color: chartTheme.TICK,
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
				max: 100,
				grid: { display: false },
				ticks: { color: chartTheme.TICK, font: { size: 11 }, callback: (v: number | string) => `${v}%` },
				border: { display: false },
				title: { display: true, text: 'edits without prior read', color: chartTheme.TICK, font: { size: 10 } }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...chartTheme.TOOLTIP,
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
						return `${v.toFixed(1)}% of edits had no prior read · ${b?.count ?? 0} sessions`;
					}
				}
			},
			annotation: {
				annotations: {
					healthy: {
						type: 'box' as const,
						yMin: 0,
						yMax: 20,
						backgroundColor: chartTheme.ZONE_GOOD,
						borderWidth: 0,
						drawTime: 'beforeDatasetsDraw' as const
					},
					warning: {
						type: 'box' as const,
						yMin: 20,
						yMax: 50,
						backgroundColor: chartTheme.ZONE_WARN,
						borderWidth: 0,
						drawTime: 'beforeDatasetsDraw' as const
					},
					danger: {
						type: 'box' as const,
						yMin: 50,
						yMax: 100,
						backgroundColor: chartTheme.ZONE_BAD,
						borderWidth: 0,
						drawTime: 'beforeDatasetsDraw' as const
					}
				}
			}
		}
	});
</script>

<div class="h-48 w-full">
	<Bar data={chartData} options={chartOptions} />
</div>
