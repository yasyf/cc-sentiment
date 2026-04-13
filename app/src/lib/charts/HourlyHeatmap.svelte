<script lang="ts">
	import { Chart as ChartComponent } from 'svelte5-chartjs';
	import { Chart, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, BarController, LineController } from 'chart.js';
	import type { HourlyPoint } from '$lib/types.js';

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

	function scoreColor(v: number): string {
		if (v === 0) return 'transparent';
		if (v < 2) return '#ef4444';
		if (v < 2.5) return '#f97316';
		if (v < 3.5) return '#eab308';
		if (v < 4.5) return '#22c55e';
		return '#06b6d4';
	}

	const chartData = $derived({
		labels: allHours.map((d) => fmtHour(d.hour)),
		datasets: [
			{
				type: 'bar' as const,
				label: 'Records',
				data: allHours.map((d) => d.count),
				backgroundColor: allHours.map(() => 'rgba(99, 102, 241, 0.25)'),
				hoverBackgroundColor: allHours.map(() => 'rgba(99, 102, 241, 0.45)'),
				borderRadius: 3,
				yAxisID: 'volume',
				order: 2
			},
			{
				type: 'line' as const,
				label: 'Avg Score',
				data: allHours.map((d) => (d.count > 0 ? d.avg_score : null)),
				borderColor: '#818cf8',
				pointBackgroundColor: allHours.map((d) => scoreColor(d.avg_score)),
				pointBorderColor: 'transparent',
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

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				grid: { color: 'rgba(255,255,255,0.04)' },
				ticks: { color: '#52525b', font: { size: 10 }, maxRotation: 0 },
				border: { display: false }
			},
			volume: {
				position: 'left' as const,
				beginAtZero: true,
				grid: { color: 'rgba(255,255,255,0.04)' },
				ticks: { color: '#52525b', font: { size: 10 } },
				border: { display: false },
				title: { display: true, text: 'records', color: '#52525b', font: { size: 10 } }
			},
			score: {
				position: 'right' as const,
				min: 1,
				max: 5,
				grid: { drawOnChartArea: false },
				ticks: { color: '#52525b', font: { size: 10 }, stepSize: 1 },
				border: { display: false },
				title: { display: true, text: 'score', color: '#52525b', font: { size: 10 } }
			}
		},
		plugins: {
			legend: {
				display: true,
				position: 'bottom' as const,
				labels: { color: '#71717a', font: { size: 10 }, boxWidth: 12, padding: 16 }
			},
			tooltip: {
				backgroundColor: '#12121a',
				borderColor: '#27272a',
				borderWidth: 1,
				titleColor: '#e4e4e7',
				bodyColor: '#71717a',
				padding: 10,
				cornerRadius: 8
			}
		}
	};
</script>

<div class="h-56 w-full">
	<ChartComponent type="bar" data={chartData} options={chartOptions} />
</div>
