<script lang="ts">
	import { Line } from 'svelte5-chartjs';
	import { Chart, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, Legend } from 'chart.js';
	import 'chartjs-adapter-date-fns';
	import type { TimelinePoint } from '$lib/types.js';

	Chart.register(LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, Legend);

	const { data: raw }: { data: TimelinePoint[] } = $props();

	const sorted = $derived(raw.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()));

	const chartData = $derived({
		labels: sorted.map((d) => d.time),
		datasets: [
			{
				label: 'Avg Sentiment',
				data: sorted.map((d) => d.avg_score),
				borderColor: '#6366f1',
				backgroundColor: 'rgba(99, 102, 241, 0.08)',
				fill: true,
				tension: 0.3,
				pointRadius: 3,
				pointHoverRadius: 6,
				pointBackgroundColor: '#6366f1',
				pointBorderColor: 'transparent',
				borderWidth: 2
			}
		]
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'index' as const, intersect: false },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'day' as const, tooltipFormat: 'MMM d, yyyy HH:mm' },
				grid: { color: 'rgba(255,255,255,0.04)' },
				ticks: { color: '#52525b', font: { size: 11 }, maxTicksLimit: 8 },
				border: { display: false }
			},
			y: {
				min: 1,
				max: 5,
				grid: { color: 'rgba(255,255,255,0.06)' },
				ticks: { color: '#52525b', font: { size: 11 }, stepSize: 1 },
				border: { display: false }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				backgroundColor: '#12121a',
				borderColor: '#27272a',
				borderWidth: 1,
				titleColor: '#e4e4e7',
				bodyColor: '#71717a',
				padding: 10,
				cornerRadius: 8,
				callbacks: {
					label: (ctx: { parsed: { y: number | null }; dataIndex: number }) =>
						`Score: ${(ctx.parsed.y ?? 0).toFixed(2)}  (${sorted[ctx.dataIndex]?.count ?? 0} records)`
				}
			}
		}
	};
</script>

<div class="h-56 w-full">
	<Line data={chartData} options={chartOptions} />
</div>
