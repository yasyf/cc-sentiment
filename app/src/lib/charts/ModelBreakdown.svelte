<script lang="ts">
	import { Bar } from 'svelte5-chartjs';
	import { Chart, BarElement, CategoryScale, LinearScale, Tooltip } from 'chart.js';
	import type { ModelBreakdown as ModelBreakdownData } from '$lib/types.js';
	import { chartTheme, sentimentColor, SENTIMENT_EMOJI, paddedRange } from '$lib/chart-theme.js';

	Chart.register(BarElement, CategoryScale, LinearScale, Tooltip);

	const { data: raw }: { data: ModelBreakdownData[] } = $props();

	const sorted = $derived(raw.toSorted((a, b) => b.count - a.count).slice(0, 10));

	function shortModel(id: string): string {
		return id.replace(/^claude-/, '').replace(/-\d{8}$/, '');
	}

	const chartData = $derived({
		labels: sorted.map((d) => shortModel(d.claude_model)),
		datasets: [{
			data: sorted.map((d) => d.avg_score),
			backgroundColor: sorted.map((d) => sentimentColor(d.avg_score) + '40'),
			hoverBackgroundColor: sorted.map((d) => sentimentColor(d.avg_score) + '70'),
			borderColor: sorted.map((d) => sentimentColor(d.avg_score)),
			borderWidth: 1,
			borderRadius: 4,
			maxBarThickness: 40
		}]
	});

	const xRange = $derived(
		paddedRange(sorted.map((d) => d.avg_score), { floor: 1, ceil: 5, snapInt: true, minSpan: 2 })
	);

	const chartOptions = $derived({
		responsive: true,
		maintainAspectRatio: false,
		indexAxis: 'y' as const,
		scales: {
			x: {
				min: xRange.min,
				max: xRange.max,
				grid: { color: chartTheme.GRID },
				ticks: {
					color: chartTheme.TICK,
					font: { size: 14 },
					stepSize: 1,
					callback: (v: number | string) => {
						const n = Number(v);
						return Number.isInteger(n) ? SENTIMENT_EMOJI[n] ?? '' : '';
					}
				},
				border: { display: false }
			},
			y: {
				grid: { display: false },
				ticks: { color: chartTheme.TICK, font: { size: 11 } },
				border: { display: false }
			}
		},
		plugins: {
			legend: { display: false },
			tooltip: {
				...chartTheme.TOOLTIP,
				callbacks: {
					label: (ctx: { parsed: { x: number | null }; dataIndex: number }) => {
						const d = sorted[ctx.dataIndex];
						const score = ctx.parsed.x ?? 0;
						const emoji = SENTIMENT_EMOJI[Math.round(score)] ?? '';
						return `${score.toFixed(2)} ${emoji}  ·  ${d?.count ?? 0} sessions`;
					}
				}
			}
		}
	});
</script>

{#if sorted.length > 0}
	<div class="w-full" style="height: {Math.max(sorted.length * 32, 120)}px">
		<Bar data={chartData} options={chartOptions} />
	</div>
{:else}
	<p class="py-8 text-center text-sm text-text-dim">No model data available</p>
{/if}
