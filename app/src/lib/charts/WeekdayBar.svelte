<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import type { WeekdayPoint } from '$lib/types.js';
	import { ACCENT_BAR, ACCENT_BAR_HOVER, GRID, TICK, TOOLTIP, sentimentColor } from '$lib/chart-theme.js';

	Chart.register(BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController);

	const { data: raw }: { data: WeekdayPoint[] } = $props();

	const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
	const sorted = $derived(raw.toSorted((a, b) => a.dow - b.dow));

	const chartData = $derived({
		labels: sorted.map((d) => DAYS[d.dow]),
		datasets: [
			{
				type: 'bar' as const,
				label: 'Sessions',
				data: sorted.map((d) => d.count),
				backgroundColor: ACCENT_BAR,
				hoverBackgroundColor: ACCENT_BAR_HOVER,
				borderRadius: 4,
				maxBarThickness: 40,
				yAxisID: 'volume',
				order: 2
			},
			{
				type: 'line' as const,
				label: 'Avg Score',
				data: sorted.map((d) => d.avg_score),
				borderColor: '#6366f1',
				pointBackgroundColor: sorted.map((d) => sentimentColor(d.avg_score)),
				pointBorderColor: '#ffffff',
				pointBorderWidth: 1.5,
				pointRadius: 5,
				pointHoverRadius: 7,
				borderWidth: 2,
				tension: 0.3,
				yAxisID: 'score',
				order: 1
			}
		]
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				grid: { display: false },
				ticks: { color: TICK, font: { size: 11 } },
				border: { display: false }
			},
			volume: {
				position: 'left' as const,
				beginAtZero: true,
				grid: { color: GRID },
				ticks: { color: TICK, font: { size: 10 } },
				border: { display: false }
			},
			score: {
				position: 'right' as const,
				min: 1, max: 5,
				grid: { drawOnChartArea: false },
				ticks: { color: TICK, font: { size: 10 }, stepSize: 1 },
				border: { display: false }
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

<div class="h-48 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
