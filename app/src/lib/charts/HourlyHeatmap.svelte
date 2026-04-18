<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import annotationPlugin from 'chartjs-plugin-annotation';
	import type { ChartOptions } from 'chart.js';
	import type { HourlyPoint } from '$lib/types.js';
	import { ACCENT_BAR, ACCENT_BAR_HOVER, GRID, TICK, TOOLTIP, SENTIMENT_EMOJI, sentimentColor, paddedRange } from '$lib/chart-theme.js';

	Chart.register(BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController, annotationPlugin);

	const { data: raw }: { data: HourlyPoint[] } = $props();

	function fmtHour(h: number): string {
		if (h === 0) return '12a';
		if (h < 12) return `${h}a`;
		if (h === 12) return '12p';
		return `${h - 12}p`;
	}

	const currentPTHour = (() => {
		const raw = Number(new Intl.DateTimeFormat('en-US', {
			timeZone: 'America/Los_Angeles', hour: '2-digit', hour12: false
		}).format(new Date()));
		return raw === 24 ? 0 : raw;
	})();

	const allHours = $derived.by(() => {
		const byHour = new Map(raw.map((d) => [d.hour, d]));
		return Array.from({ length: 24 }, (_, pt) => {
			const src = byHour.get(pt);
			return { hour: pt, avg_score: src?.avg_score ?? 0, count: src?.count ?? 0 };
		});
	});

	const peakAnnotations = {
		peakUsage: {
			type: 'box' as const,
			xMin: '5p',
			xMax: '7p',
			backgroundColor: 'rgba(220, 38, 38, 0.08)',
			borderColor: 'rgba(220, 38, 38, 0.25)',
			borderWidth: 1,
			label: {
				display: true,
				content: 'peak usage',
				position: { x: 'center' as const, y: 'end' as const },
				yAdjust: -4,
				font: { size: 9 },
				color: '#dc2626',
				backgroundColor: 'rgba(255,255,255,0.9)',
				padding: 2
			}
		},
		currentHour: {
			type: 'line' as const,
			xMin: fmtHour(currentPTHour),
			xMax: fmtHour(currentPTHour),
			borderColor: 'rgba(99, 102, 241, 0.5)',
			borderWidth: 2,
			borderDash: [3, 3],
			label: {
				display: true,
				content: 'now',
				position: 'start' as const,
				yAdjust: -6,
				font: { size: 9, weight: 'bold' as const },
				color: '#6366f1',
				backgroundColor: 'rgba(255,255,255,0.9)',
				padding: 2
			}
		}
	};

	const chartData = $derived({
		labels: allHours.map((d) => fmtHour(d.hour)),
		datasets: [
			{
				type: 'bar' as const,
				label: 'Sessions',
				data: allHours.map((d) => d.count),
				backgroundColor: ACCENT_BAR,
				hoverBackgroundColor: ACCENT_BAR_HOVER,
				borderRadius: 3,
				yAxisID: 'volume',
				order: 2
			},
			{
				type: 'line' as const,
				label: 'Avg sentiment',
				data: allHours.map((d) => (d.count > 0 ? d.avg_score : null)),
				borderColor: '#6366f1',
				pointBackgroundColor: allHours.map((d) => d.count > 0 ? sentimentColor(d.avg_score) : 'transparent'),
				pointBorderColor: '#ffffff',
				pointBorderWidth: 1.5,
				pointRadius: allHours.map((d) => (d.count > 0 ? 4 : 0)),
				pointHoverRadius: 6,
				borderWidth: 2,
				tension: 0.3,
				spanGaps: true,
				yAxisID: 'score',
				order: 1
			}
		]
	});

	const scoreRange = $derived(
		paddedRange(allHours.filter((d) => d.count > 0).map((d) => d.avg_score), { floor: 1, ceil: 5, snapInt: true, minSpan: 2 })
	);

	const chartOptions: ChartOptions = $derived({
		responsive: true,
		maintainAspectRatio: false,
		layout: { padding: { top: 12 } },
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				grid: { display: false },
				ticks: { color: TICK, font: { size: 10 }, maxRotation: 0 },
				border: { display: false }
			},
			volume: {
				position: 'left' as const,
				beginAtZero: true,
				grace: '10%' as const,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 10 } },
				border: { display: false },
				title: { display: true, text: 'sessions', color: TICK, font: { size: 10 } }
			},
			score: {
				position: 'right' as const,
				min: scoreRange.min,
				max: scoreRange.max,
				grid: { drawOnChartArea: false },
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
			}
		},
		plugins: {
			legend: {
				display: true,
				position: 'bottom' as const,
				labels: { color: '#71717a', font: { size: 10 }, boxWidth: 10, padding: 20, usePointStyle: true }
			},
			tooltip: TOOLTIP,
			annotation: { annotations: peakAnnotations }
		}
	});
</script>

<div class="h-52 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
