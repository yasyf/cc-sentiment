<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, CategoryScale, LinearScale, Tooltip } from 'chart.js';
	import type { DistributionPoint } from '$lib/types.js';
	import { chartTheme, SENTIMENT_EMOJI } from '$lib/chart-theme.js';

	Chart.register(BarElement, CategoryScale, LinearScale, Tooltip);

	const { data: raw }: { data: DistributionPoint[] } = $props();

	const LABELS: Record<number, string> = {
		1: 'Frustrated', 2: 'Annoyed', 3: 'Neutral', 4: 'Satisfied', 5: 'Delighted'
	};

	const sorted = $derived(raw.toSorted((a, b) => a.score - b.score));

	const chartData = $derived({
		labels: sorted.map((d) => {
			const emoji = SENTIMENT_EMOJI[d.score] ?? '';
			const label = LABELS[d.score] ?? String(d.score);
			return emoji ? `${emoji} ${label}` : label;
		}),
		datasets: [{
			data: sorted.map((d) => d.count),
			backgroundColor: sorted.map((d) => (chartTheme.SENTIMENT[d.score] ?? chartTheme.ACCENT) + '30'),
			hoverBackgroundColor: sorted.map((d) => (chartTheme.SENTIMENT[d.score] ?? chartTheme.ACCENT) + '60'),
			borderColor: sorted.map((d) => chartTheme.SENTIMENT[d.score] ?? chartTheme.ACCENT),
			borderWidth: 1,
			borderRadius: 4,
			maxBarThickness: 56
		}]
	});

	const chartOptions = $derived({
		responsive: true,
		maintainAspectRatio: false,
		scales: {
			x: {
				grid: { display: false },
				ticks: { color: chartTheme.TICK, font: { size: 11 } },
				border: { display: false }
			},
			y: {
				beginAtZero: true,
				grid: { color: chartTheme.GRID },
				ticks: { color: chartTheme.TICK, font: { size: 10 } },
				border: { display: false }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...chartTheme.TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { y: number | null } }) => {
						const val = ctx.parsed.y ?? 0;
						const total = sorted.reduce((s, d) => s + d.count, 0);
						const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0';
						return `${val} sessions (${pct}%)`;
					}
				}
			}
		}
	});
</script>

<div class="h-48 w-full">
	<Bar data={chartData} options={chartOptions} />
</div>
