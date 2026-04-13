<script lang="ts">
	import type { HourlyPoint } from '$lib/types.js';

	const { data }: { data: HourlyPoint[] } = $props();

	const WIDTH = 600;
	const HEIGHT = 240;
	const PADDING = { top: 20, right: 20, bottom: 30, left: 40 };
	const INNER_W = WIDTH - PADDING.left - PADDING.right;
	const INNER_H = HEIGHT - PADDING.top - PADDING.bottom;

	function scoreColor(score: number): string {
		if (score < 1.5) return 'var(--color-sentiment-1)';
		if (score < 2.5) return 'var(--color-sentiment-2)';
		if (score < 3.5) return 'var(--color-sentiment-3)';
		if (score < 4.5) return 'var(--color-sentiment-4)';
		return 'var(--color-sentiment-5)';
	}

	const allHours = $derived.by(() => {
		const byHour = new Map(data.map((d) => [d.hour, d]));
		return Array.from({ length: 24 }, (_, i) => byHour.get(i) ?? { hour: i, avg_score: 0, count: 0 });
	});

	const maxCount = $derived(Math.max(1, ...data.map((d) => d.count)));

	function barWidth(): number {
		return INNER_W / 24 - 2;
	}

	function barX(hour: number): number {
		return PADDING.left + (hour / 24) * INNER_W + 1;
	}

	function barHeight(count: number): number {
		return (count / maxCount) * INNER_H;
	}

	const HOUR_LABELS: Record<number, string> = {
		0: '12a',
		6: '6a',
		12: '12p',
		18: '6p'
	};

	let hoveredHour = $state<number | null>(null);
</script>

{#if data.length === 0}
	<div class="flex h-72 items-center justify-center text-text-dim">No hourly data yet</div>
{:else}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		class="h-72 w-full"
		role="img"
		aria-label="Sentiment by hour of day"
	>
		{#each [1, 2, 3, 4, 5] as tick}
			<line
				x1={PADDING.left}
				y1={PADDING.top + INNER_H - ((tick - 1) / 4) * INNER_H}
				x2={WIDTH - PADDING.right}
				y2={PADDING.top + INNER_H - ((tick - 1) / 4) * INNER_H}
				stroke="var(--color-border)"
				stroke-dasharray="3,3"
				opacity="0.3"
			/>
		{/each}

		{#each allHours as point}
			{@const bw = barWidth()}
			{@const bh = barHeight(point.count)}
			{@const x = barX(point.hour)}
			{@const y = PADDING.top + INNER_H - bh}

			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<rect
				{x}
				{y}
				width={bw}
				height={Math.max(bh, point.count > 0 ? 2 : 0)}
				rx="2"
				fill={point.count > 0 ? scoreColor(point.avg_score) : 'transparent'}
				opacity={hoveredHour === null || hoveredHour === point.hour ? 0.9 : 0.3}
				class="transition-opacity duration-150"
				onmouseenter={() => (hoveredHour = point.hour)}
				onmouseleave={() => (hoveredHour = null)}
			/>
		{/each}

		{#each Object.entries(HOUR_LABELS) as [hour, label]}
			<text x={barX(Number(hour)) + barWidth() / 2} y={HEIGHT - 6} text-anchor="middle" fill="var(--color-text-dim)" font-size="10">
				{label}
			</text>
		{/each}

		{#if hoveredHour !== null}
			{@const p = allHours.find((d) => d.hour === hoveredHour)}
			{#if p && p.count > 0}
				{@const x = barX(p.hour) + barWidth() / 2}
				<rect x={x - 60} y="2" width="120" height="18" rx="4" fill="var(--color-bg-card)" stroke="var(--color-border)" />
				<text x={x} y="14" text-anchor="middle" fill="var(--color-text)" font-size="11">
					{p.hour}:00 - avg {p.avg_score.toFixed(1)} ({p.count})
				</text>
			{/if}
		{/if}
	</svg>
{/if}
