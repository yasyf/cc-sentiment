<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import type { ChartOptions } from 'chart.js';
	import type { HourlyPoint } from '$lib/types.js';
	import { ACCENT_BAR, ACCENT_BAR_HOVER, GRID, TICK, TOOLTIP, sentimentColor } from '$lib/chart-theme.js';

	Chart.register(BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController);

	const { data: raw }: { data: HourlyPoint[] } = $props();

	const allHours = $derived.by(() => {
		const byHour = new Map(raw.map((d) => [d.hour, d]));
		return Array.from({ length: 24 }, (_, i) => byHour.get(i) ?? { hour: i, avg_score: 0, count: 0 });
	});

	function fmtHour(h: number): string {
		if (h === 0) return '12a';
		if (h < 12) return `${h}a`;
		if (h === 12) return '12p';
		return `${h - 12}p`;
	}

	// Peak frustration windows from claude-code#42796 analysis (PST/PT)
	// 5pm PT = worst hour, 7pm PT = second worst, late night = recovery
	// We show these as UTC annotations: 5pm PT = 0:00 UTC, 7pm PT = 2:00 UTC
	// But since our users could be any timezone, we mark approximate US work hours
	// 9am-6pm PT = 16:00-01:00 UTC -- these are the "expected high usage" hours
	const peakHoursPT = new Set([17, 19]); // 5pm, 7pm PT -- worst per the issue

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

	const chartOptions: ChartOptions = {
		responsive: true,
		maintainAspectRatio: false,
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
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 10 } },
				border: { display: false },
				title: { display: true, text: 'sessions', color: TICK, font: { size: 10 } }
			},
			score: {
				position: 'right' as const,
				min: 1, max: 5,
				grid: { drawOnChartArea: false },
				ticks: { color: TICK, font: { size: 10 }, stepSize: 1 },
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
			tooltip: TOOLTIP
		}
	};
</script>

<div class="h-52 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
<p class="mt-2 text-[11px] text-text-dim">
	Times are UTC. Per <a href="https://github.com/anthropics/claude-code/issues/42796" class="text-accent hover:text-accent-hover">the analysis</a>, sentiment is worst at 5pm and 7pm PT (peak US load).
</p>
