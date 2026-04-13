<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, CategoryScale, LinearScale, Tooltip } from 'chart.js';
	import type { DistributionPoint } from '$lib/types.js';

	Chart.register(BarElement, CategoryScale, LinearScale, Tooltip);

	const { data: raw }: { data: DistributionPoint[] } = $props();

	const LABELS: Record<number, string> = {
		1: 'Frustrated', 2: 'Annoyed', 3: 'Neutral', 4: 'Satisfied', 5: 'Delighted'
	};
	const COLORS: Record<number, string> = {
		1: '#ef4444', 2: '#f97316', 3: '#eab308', 4: '#22c55e', 5: '#06b6d4'
	};

	const sorted = $derived(raw.toSorted((a, b) => a.score - b.score));

	const chartData = $derived({
		labels: sorted.map((d) => LABELS[d.score] ?? String(d.score)),
		datasets: [
			{
				data: sorted.map((d) => d.count),
				backgroundColor: sorted.map((d) => COLORS[d.score] ?? '#6366f1'),
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
				beginAtZero: true,
				grid: { color: 'rgba(255,255,255,0.04)' },
				ticks: { color: '#52525b', font: { size: 10 } },
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
					label: (ctx: { parsed: { y: number | null } }) => {
						const val = ctx.parsed.y ?? 0;
						const total = sorted.reduce((s, d) => s + d.count, 0);
						const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0';
						return `${val} records (${pct}%)`;
					}
				}
			}
		}
	};
</script>

<div class="h-48 w-full">
	<Bar data={chartData} options={chartOptions} />
</div>
