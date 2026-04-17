<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, BarElement, LinearScale, TimeScale, Filler, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import 'chartjs-adapter-luxon';
	import { DateTime } from 'luxon';
	import type { TimelinePoint } from '$lib/types.js';
	import { EVENTS } from '$lib/events.js';
	import { ACCENT, TICK, TOOLTIP, SENTIMENT_EMOJI, paddedRange } from '$lib/chart-theme.js';
	import {
		bucketByDayPart,
		bucketByDay,
		smoothSeries,
		filterWindow,
		dayBoundaryAnnotations,
		dayPartBandAnnotations,
		dayPartFor,
		DAY_PART_EMOJI
	} from '$lib/bucket.js';

	Chart.register(LineElement, PointElement, BarElement, LinearScale, TimeScale, Filler, Tooltip, Legend, BarController, LineController, annotationPlugin);

	type Range = 'week' | 'month';
	const { data: raw, range = 'week' }: { data: TimelinePoint[]; range?: Range } = $props();

	const DISPLAY_TZ = 'America/Los_Angeles';

	const buckets = $derived.by(() => {
		if (range === 'week') {
			const all = bucketByDayPart(raw, DISPLAY_TZ);
			const smoothed = smoothSeries(all, (b) => b.avg_score, { halfWindow: 1, priorWeight: 1.0, priorValue: 3 });
			const merged = all.map((b, i) => ({ ...b, smoothed_score: smoothed[i] }));
			return filterWindow(merged, 7, DISPLAY_TZ);
		}
		const all = bucketByDay(raw, DISPLAY_TZ);
		const smoothed = smoothSeries(all, (b) => b.avg_score, { halfWindow: 2, priorWeight: 1.0, priorValue: 3 });
		const merged = all.map((b, i) => ({ ...b, smoothed_score: smoothed[i], label: '' }));
		return filterWindow(merged, 30, DISPLAY_TZ);
	});

	const timeRange = $derived.by(() => {
		if (buckets.length === 0) return { min: 0, max: 0 };
		return {
			min: new Date(buckets[0].time).getTime(),
			max: new Date(buckets[buckets.length - 1].time).getTime()
		};
	});

	const annotations = $derived.by(() => {
		const out: Record<string, object> = {};
		if (range === 'week') {
			Object.assign(out, dayPartBandAnnotations(buckets, DISPLAY_TZ));
			Object.assign(out, dayBoundaryAnnotations(buckets, DISPLAY_TZ));
		}
		for (const evt of EVENTS) {
			const t = new Date(evt.date).getTime();
			if (t >= timeRange.min && t <= timeRange.max) {
				out[`event-${evt.date}`] = {
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
		return out;
	});

	const scoreRange = $derived(
		paddedRange(buckets.map((d) => d.smoothed_score), { floor: 1, ceil: 5, snapInt: true, minSpan: 2 })
	);

	const chartData = $derived({
		labels: buckets.map((d) => d.time),
		datasets: [
			{
				type: 'bar' as const,
				label: 'Sessions',
				data: buckets.map((d) => d.count),
				backgroundColor: 'rgba(99, 102, 241, 0.08)',
				hoverBackgroundColor: 'rgba(99, 102, 241, 0.18)',
				borderRadius: 2,
				yAxisID: 'volume',
				order: 2
			},
			{
				type: 'line' as const,
				label: 'Sentiment',
				data: buckets.map((d) => d.smoothed_score),
				borderColor: ACCENT,
				fill: false,
				tension: 0,
				pointRadius: 2,
				pointHoverRadius: 5,
				pointBackgroundColor: ACCENT,
				pointBorderColor: 'transparent',
				borderWidth: 1.75,
				spanGaps: true,
				yAxisID: 'score',
				order: 1
			}
		]
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
					font: { size: 12 },
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
			score: {
				position: 'left' as const,
				min: scoreRange.min,
				max: scoreRange.max,
				grid: { display: false },
				ticks: {
					color: TICK,
					font: { size: 14 },
					stepSize: 1,
					callback: (v: number | string) => {
						const n = Number(v);
						return Number.isInteger(n) ? SENTIMENT_EMOJI[n] ?? '' : '';
					}
				},
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
					title: (items: { dataIndex: number }[]) => {
						const b = buckets[items[0]?.dataIndex];
						if (!b) return '';
						const dt = new Date(b.time).toLocaleDateString('en-US', {
							timeZone: DISPLAY_TZ, month: 'short', day: 'numeric'
						});
						return range === 'week' && b.label ? `${dt} · ${b.label}` : dt;
					},
					label: (ctx: { parsed: { y: number | null }; dataset: { label?: string }; dataIndex: number }) => {
						if (ctx.dataset.label === 'Sessions') return `${ctx.parsed.y ?? 0} sessions`;
						const b = buckets[ctx.dataIndex];
						const score = ctx.parsed.y;
						if (score == null) return 'no data';
						return `${score.toFixed(2)} sentiment · ${b?.count ?? 0} sessions`;
					}
				}
			},
			annotation: { annotations }
		}
	});
</script>

<div class="h-48 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
