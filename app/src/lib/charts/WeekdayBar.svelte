<script lang="ts">
	import type { WeekdayPoint } from '$lib/types.js';

	const { data }: { data: WeekdayPoint[] } = $props();

	const WIDTH = 600;
	const HEIGHT = 240;
	const PADDING = { top: 20, right: 20, bottom: 30, left: 40 };
	const INNER_W = WIDTH - PADDING.left - PADDING.right;
	const INNER_H = HEIGHT - PADDING.top - PADDING.bottom;
	const BAR_GAP = 10;

	const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

	function scoreColor(score: number): string {
		if (score < 1.5) return 'var(--color-sentiment-1)';
		if (score < 2.5) return 'var(--color-sentiment-2)';
		if (score < 3.5) return 'var(--color-sentiment-3)';
		if (score < 4.5) return 'var(--color-sentiment-4)';
		return 'var(--color-sentiment-5)';
	}

	const sorted = $derived(data.toSorted((a, b) => a.dow - b.dow));

	function barWidth(): number {
		return (INNER_W - BAR_GAP * 6) / 7;
	}

	function barX(i: number): number {
		return PADDING.left + i * (barWidth() + BAR_GAP);
	}

	function barHeight(score: number): number {
		return ((score - 1) / 4) * INNER_H;
	}

	let hoveredIndex = $state<number | null>(null);
</script>

{#if sorted.length === 0}
	<div class="flex h-72 items-center justify-center text-text-dim">No weekday data yet</div>
{:else}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		class="h-72 w-full"
		role="img"
		aria-label="Sentiment by day of week"
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
			<text
				x={PADDING.left - 8}
				y={PADDING.top + INNER_H - ((tick - 1) / 4) * INNER_H + 4}
				text-anchor="end"
				fill="var(--color-text-dim)"
				font-size="11"
			>
				{tick}
			</text>
		{/each}

		{#each sorted as point, i}
			{@const bw = barWidth()}
			{@const bh = barHeight(point.avg_score)}
			{@const x = barX(i)}
			{@const y = PADDING.top + INNER_H - bh}

			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<rect
				{x}
				{y}
				width={bw}
				height={bh}
				rx="4"
				fill={scoreColor(point.avg_score)}
				opacity={hoveredIndex === null || hoveredIndex === i ? 1 : 0.4}
				class="transition-opacity duration-150"
				onmouseenter={() => (hoveredIndex = i)}
				onmouseleave={() => (hoveredIndex = null)}
			/>

			<text
				x={x + bw / 2}
				y={y - 6}
				text-anchor="middle"
				fill="var(--color-text-muted)"
				font-size="11"
				font-weight="500"
			>
				{point.avg_score.toFixed(1)}
			</text>

			<text x={x + bw / 2} y={HEIGHT - 6} text-anchor="middle" fill="var(--color-text-dim)" font-size="11">
				{DAY_NAMES[point.dow]}
			</text>
		{/each}

		{#if hoveredIndex !== null}
			{@const p = sorted[hoveredIndex]}
			{@const x = barX(hoveredIndex) + barWidth() / 2}
			<rect x={x - 55} y="2" width="110" height="18" rx="4" fill="var(--color-bg-card)" stroke="var(--color-border)" />
			<text x={x} y="14" text-anchor="middle" fill="var(--color-text)" font-size="11">
				{DAY_NAMES[p.dow]}: {p.avg_score.toFixed(2)} ({p.count})
			</text>
		{/if}
	</svg>
{/if}
