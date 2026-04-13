<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, CategoryScale, LinearScale, Tooltip } from 'chart.js';
	import type { WeekdayPoint } from '$lib/types.js';

	Chart.register(BarElement, CategoryScale, LinearScale, Tooltip);

	const { data: raw }: { data: WeekdayPoint[] } = $props();

	const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

	function scoreColor(v: number): string {
		if (v < 1.5) return '#ef4444';
		if (v < 2.5) return '#f97316';
		if (v < 3.5) return '#eab308';
		if (v < 4.5) return '#22c55e';
		return '#06b6d4';
	}

	const sorted = $derived(raw.toSorted((a, b) => a.dow - b.dow));

	const chartData = $derived({
		labels: sorted.map((d) => DAYS[d.dow]),
		datasets: [
			{
				data: sorted.map((d) => d.avg_score),
				backgroundColor: sorted.map((d) => scoreColor(d.avg_score)),
				borderRadius: 6,
				maxBarThickness: 48
			}
		]
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		scales: {
			x: {
				grid: { display: false },
				ticks: { color: '#52525b', font: { size: 11 } },
				border: { display: false }
			},
			y: {
				min: 1,
				max: 5,
				grid: { color: 'rgba(255,255,255,0.04)' },
				ticks: { color: '#52525b', font: { size: 10 }, stepSize: 1 },
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
						`Avg: ${(ctx.parsed.y ?? 0).toFixed(2)} (${sorted[ctx.dataIndex]?.count ?? 0} records)`
				}
			}
		}
	};
</script>

<div class="h-48 w-full">
	<Bar data={chartData} options={chartOptions} />
</div>
